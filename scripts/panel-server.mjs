import { spawn, spawnSync } from "node:child_process";
import { randomBytes, timingSafeEqual } from "node:crypto";
import { existsSync } from "node:fs";
import { readFile } from "node:fs/promises";
import { createServer } from "node:http";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const BODY_LIMIT = 2 * 1024 * 1024;
const STDOUT_LIMIT = 32 * 1024 * 1024;
const livePanels = new Set();
let stopping = false;

// An imported host must also release its worker when the CLI/MCP parent exits.
function exitCleanup() {
  for (const panel of livePanels) panel.killSync();
}
async function stopForSignal(signal) {
  if (stopping) return;
  stopping = true;
  await Promise.allSettled([...livePanels].map((panel) => panel.close()));
  process.exit(signal === "SIGINT" ? 130 : 143);
}
const onSigint = () => { void stopForSignal("SIGINT"); };
const onSigterm = () => { void stopForSignal("SIGTERM"); };
function registerPanel(panel) {
  if (!livePanels.size) {
    process.on("SIGINT", onSigint);
    process.on("SIGTERM", onSigterm);
    process.on("exit", exitCleanup);
  }
  livePanels.add(panel);
}
function unregisterPanel(panel) {
  livePanels.delete(panel);
  if (!livePanels.size) {
    process.removeListener("SIGINT", onSigint);
    process.removeListener("SIGTERM", onSigterm);
    process.removeListener("exit", exitCleanup);
  }
}

function rpcError(code, message) {
  return Object.assign(new Error(message), { code });
}
function httpError(status, message) {
  return Object.assign(new Error(message), { status });
}
function isObject(value) {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function safeEqual(value, expected) {
  if (typeof value !== "string") return false;
  const a = Buffer.from(value);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}
function safeLink(raw) {
  let url;
  try { url = new URL(raw); } catch { throw rpcError(-32602, "Invalid link URL"); }
  if (typeof raw !== "string" || raw.length > 8192 ||
      !["https:", "http:", "zotero:"].includes(url.protocol) || url.username || url.password) {
    throw rpcError(-32602, "Only HTTP, HTTPS and Zotero links without credentials are supported");
  }
  return url.href;
}
function promptText(params) {
  const text = Array.isArray(params?.content)
    ? params.content.filter((block) => block?.type === "text" && typeof block.text === "string")
      .map((block) => block.text).join("\n\n") : "";
  if (!text.trim()) throw rpcError(-32602, "ui/message requires text content");
  return text;
}

function startMcpClient(entry, args, { env, cwd, timeoutMs }) {
  const child = spawn(process.execPath, [entry, ...args], {
    env, cwd, stdio: ["pipe", "pipe", "pipe"], windowsHide: true,
    // A process group lets close() also stop Python/OCR descendants on POSIX.
    detached: process.platform !== "win32"
  });
  const pending = new Map();
  let nextId = 1;
  let buffer = "";
  let stderr = "";
  let failure = null;
  let closePromise;
  let exited = false;
  const exit = new Promise((done) => child.once("close", () => { exited = true; done(); }));
  const fail = (error) => {
    failure ||= error;
    for (const waiter of pending.values()) {
      clearTimeout(waiter.timer);
      waiter.reject(failure);
    }
    pending.clear();
  };
  child.stderr.setEncoding("utf8");
  child.stderr.on("data", (chunk) => { stderr = (stderr + chunk).slice(-16384); });
  child.on("error", (error) => fail(new Error(`Cannot start MCP worker: ${error.message}`)));
  child.on("exit", (code, signal) => fail(new Error(`MCP worker exited (${signal || code})`)));
  child.stdin.on("error", () => fail(new Error("MCP worker stdin closed")));
  child.stdout.setEncoding("utf8");
  child.stdout.on("end", () => fail(new Error("MCP worker stdout closed")));
  child.stdout.on("data", (chunk) => {
    buffer += chunk;
    let end;
    while ((end = buffer.indexOf("\n")) !== -1) {
      const line = buffer.slice(0, end);
      buffer = buffer.slice(end + 1);
      if (line.length > STDOUT_LIMIT) return fail(new Error("MCP response exceeded its size limit"));
      if (!line.trim()) continue;
      let message;
      try { message = JSON.parse(line); } catch { return fail(new Error("MCP emitted non-JSON stdout")); }
      if (!isObject(message) || message.jsonrpc !== "2.0") return fail(new Error("Invalid MCP response"));
      if (typeof message.method === "string") {
        if (Object.hasOwn(message, "id")) {
          const reply = message.method === "ping" ? { result: {} }
            : { error: { code: -32601, message: "Client method not supported" } };
          child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: message.id, ...reply }) + "\n");
        }
        continue; // Progress and list-changed notifications are not responses.
      }
      const waiter = pending.get(message.id);
      if (!waiter) continue;
      clearTimeout(waiter.timer);
      pending.delete(message.id);
      if (message.error) waiter.reject(rpcError(message.error.code || -32603, message.error.message || "MCP error"));
      else if (Object.hasOwn(message, "result")) waiter.resolve(message.result);
      else waiter.reject(new Error("MCP response has neither result nor error"));
    }
    if (buffer.length > STDOUT_LIMIT) fail(new Error("MCP response exceeded its size limit"));
  });
  const request = (method, params = {}, limit = timeoutMs) => new Promise((done, reject) => {
    if (failure) return reject(failure);
    const id = nextId++;
    const timer = setTimeout(() => {
      pending.delete(id);
      reject(rpcError(-32000, `MCP request timed out: ${method}`));
    }, limit);
    pending.set(id, { resolve: done, reject, timer });
    child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id, method, params }) + "\n", (error) => {
      if (error) fail(new Error("Cannot write to MCP worker"));
    });
  });
  const running = () => child.pid && child.exitCode === null && child.signalCode === null;
  const killSync = () => {
    if (!running()) return;
    try {
      if (process.platform === "win32") {
        spawnSync("taskkill.exe", ["/PID", String(child.pid), "/T", "/F"], {
          stdio: "ignore", windowsHide: true, timeout: 3000
        });
      } else process.kill(-child.pid, "SIGKILL");
    } catch { try { child.kill("SIGKILL"); } catch {} }
  };
  async function close() {
    if (closePromise) return closePromise;
    closePromise = (async () => {
      fail(new Error("Panel server closed"));
      if (exited) return;
      child.stdin.end();
      const timer = setTimeout(killSync, 750);
      try { await exit; } finally { clearTimeout(timer); }
    })();
    return closePromise;
  }
  return {
    child, request, close, killSync,
    get stderr() { return stderr; },
    notify(method, params = {}) {
      if (!failure) child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
    }
  };
}

// This function is serialized into the existing asset. No dependency on test code
// or an embedded host SDK is needed, and responses never re-enter the request path.
function browserBridge(config) {
  if (!config.frameId && window.parent !== window) return;
  const origin = window.location.origin;
  const sourceWindow = () => config.frameId
    ? document.getElementById(config.frameId)?.contentWindow : window;
  let modelContext = null;
  let lastOpened = null;
  let reservedPopup = null;
  let popupTimer;
  const boundDocuments = new WeakSet();

  const address = new URL(window.location.href);
  if (address.searchParams.has("token")) {
    address.searchParams.delete("token");
    window.history.replaceState(null, "", address.pathname + address.search + address.hash);
  }
  function checkedLink(raw) {
    const url = new URL(raw);
    if (typeof raw !== "string" || raw.length > 8192 ||
        !["http:", "https:", "zotero:"].includes(url.protocol) || url.username || url.password) {
      throw new Error("仅支持不含用户名密码的 HTTP / HTTPS / Zotero 链接。");
    }
    return url.href;
  }
  function overlay(title) {
    document.getElementById("sanmu-browser-dialog")?.remove();
    const root = document.createElement("div");
    root.id = "sanmu-browser-dialog";
    root.setAttribute("role", "dialog");
    root.setAttribute("aria-modal", "true");
    root.setAttribute("aria-label", title);
    root.style.cssText = "position:fixed;inset:0;z-index:2147483647;background:#0008;display:flex;align-items:center;justify-content:center;padding:20px;color:#1f2328;font:14px/1.6 system-ui";
    const box = document.createElement("section");
    box.style.cssText = "background:#fff;max-width:850px;width:100%;max-height:90vh;overflow:auto;border-radius:12px;padding:24px;box-shadow:0 12px 50px #0005";
    const heading = document.createElement("h2");
    heading.textContent = title;
    heading.style.cssText = "font-size:20px;margin:0 0 12px";
    const close = document.createElement("button");
    close.textContent = "关闭";
    close.type = "button";
    close.addEventListener("click", () => root.remove());
    root.addEventListener("keydown", (event) => { if (event.key === "Escape") root.remove(); });
    box.appendChild(heading);
    root.appendChild(box);
    document.body.appendChild(root);
    return { root, box, close };
  }
  function showHandoff(params, context = modelContext) {
    const text = (params.content || []).filter((item) => item?.type === "text")
      .map((item) => item.text).join("\n\n");
    const { box, close } = overlay("复制精读 / 分析指令到对话");
    const notice = document.createElement("p");
    notice.textContent = "浏览器宿主无法直接发送到 Claude / Codex / Pi / DSH 当前对话。请复制以下指令并粘贴到终端或 AI 对话中；内容尚未发送。";
    const input = document.createElement("textarea");
    input.id = "sanmu-browser-handoff-text";
    input.readOnly = true;
    input.setAttribute("aria-label", "待复制的精读指令");
    const contextText = (context?.content || []).filter((item) => item?.type === "text")
      .map((item) => item.text).join("\n\n");
    input.value = text + (contextText ? "\n\n补充上下文（尚未发送）：\n" + contextText : "");
    input.style.cssText = "display:block;width:100%;min-height:300px;max-height:55vh;margin:12px 0;padding:12px;box-sizing:border-box;resize:vertical;font:13px/1.6 monospace;color:#1f2328;background:#f6f8fa";
    const status = document.createElement("p");
    status.setAttribute("role", "status");
    const copy = document.createElement("button");
    copy.id = "sanmu-browser-copy-handoff";
    copy.type = "button";
    copy.textContent = "复制指令";
    copy.style.cssText = "margin-right:12px;padding:7px 16px";
    copy.addEventListener("click", async () => {
      let copied = false;
      try {
        if (navigator.clipboard?.writeText) {
          await navigator.clipboard.writeText(input.value);
          copied = true;
        }
      } catch {}
      if (!copied) {
        input.focus();
        input.select();
        try { copied = Boolean(document.execCommand("copy")); } catch {}
      }
      status.textContent = copied ? "已复制。请粘贴到当前 AI 对话并发送。"
        : "已选中指令，请按 Ctrl+C（macOS：Cmd+C）复制，再粘贴到当前 AI 对话。";
    });
    box.appendChild(notice);
    box.appendChild(input);
    box.appendChild(copy);
    box.appendChild(close);
    box.appendChild(status);
    input.focus();
    input.select();
  }
  function showLink(url) {
    const { box, close } = overlay("点击打开外部链接");
    const notice = document.createElement("p");
    notice.textContent = "浏览器阻止了自动弹窗。请点击此链接继续。";
    const link = document.createElement("a");
    link.href = url;
    link.target = "_blank";
    link.rel = "noopener noreferrer";
    link.textContent = url;
    link.style.cssText = "display:block;overflow-wrap:anywhere;margin:16px 0;color:#0969da";
    box.appendChild(notice);
    box.appendChild(link);
    box.appendChild(close);
  }
  function openLink(raw) {
    const url = checkedLink(raw);
    if (lastOpened?.url === url && Date.now() - lastOpened.at < 4000) return true;
    clearTimeout(popupTimer);
    const popup = reservedPopup || window.open("about:blank", "_blank");
    reservedPopup = null;
    if (!popup) { showLink(url); return false; }
    try {
      popup.opener = null;
      popup.location.replace(url);
      lastOpened = { url, at: Date.now() };
      return true;
    } catch {
      try { popup.close(); } catch {}
      showLink(url);
      return false;
    }
  }
  function bindClicks(doc) {
    if (!doc || boundDocuments.has(doc)) return;
    boundDocuments.add(doc);
    doc.addEventListener("click", (event) => {
      if (!event.isTrusted || (event.button !== undefined && event.button !== 0)) return;
      const target = event.target?.closest?.(".ext-link-btn,.fig-open-btn,.api-get-link-btn,.open-doi-btn,.raw-url-btn,.ext-url-direct-btn,#btnGetExtKeyLink,#btnGetMineruTokenLink,#rawOpenUrl,a[href]");
      if (!target) return;
      // The dialog's real anchor is also the fallback for popup-blocking browsers.
      if (target.closest("#sanmu-browser-dialog")) return;
      const raw = target.dataset?.url || target.getAttribute("href");
      if (raw) {
        try {
          openLink(raw);
          if (target.tagName === "A") event.preventDefault();
        } catch { event.preventDefault(); }
      } else if (target.id === "rawOpenUrl") {
        try { reservedPopup?.close(); } catch {}
        reservedPopup = window.open("about:blank", "_blank");
        if (reservedPopup) reservedPopup.opener = null;
        clearTimeout(popupTimer);
        popupTimer = setTimeout(() => {
          try { reservedPopup?.close(); } catch {}
          reservedPopup = null;
        }, 5000);
      }
    }, true);
  }
  if (config.frameId) {
    document.addEventListener("DOMContentLoaded", () => {
      const frame = document.getElementById(config.frameId);
      frame.addEventListener("load", () => bindClicks(frame.contentDocument));
      bindClicks(frame.contentDocument);
    }, { once: true });
  } else bindClicks(document);
  window.addEventListener("pagehide", () => {
    clearTimeout(popupTimer);
    try { reservedPopup?.close(); } catch {}
  }, { once: true });

  window.addEventListener("message", async (event) => {
    const source = sourceWindow();
    if (!source || event.source !== source || event.origin !== origin) return;
    const request = event.data;
    if (!request || request.jsonrpc !== "2.0" || typeof request.method !== "string" ||
        !Object.hasOwn(request, "id") || Object.hasOwn(request, "result") || Object.hasOwn(request, "error")) return;
    event.stopImmediatePropagation();
    let response;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.rpcTimeoutMs + 2000);
    try {
      let opened;
      if (request.method === "ui/open-link") opened = openLink(request.params?.url);
      if (request.method === "ui/update-model-context") modelContext = request.params;
      if (request.method === "ui/message") showHandoff(request.params || {});
      const http = await fetch("/rpc", {
        method: "POST", credentials: "same-origin", signal: controller.signal,
        headers: { "Content-Type": "application/json" }, body: JSON.stringify(request)
      });
      if (!http.ok) throw new Error(`浏览器宿主请求失败 (${http.status})，请重新运行 sanmu panel。`);
      response = await http.json();
      if (request.method === "ui/open-link" && response.result) response.result.openedInBrowser = opened;
    } catch (error) {
      response = { jsonrpc: "2.0", id: request.id, error: { code: -32603, message: String(error.message || error) } };
    } finally { clearTimeout(timer); }
    source.postMessage(response, origin);
  });
}

function injectBridge(html, options) {
  const script = `<script id="sanmu-browser-bridge">(${browserBridge.toString()})(${JSON.stringify(options).replace(/</g, "\\u003c")});</script>`;
  if (!/<head(?:\s[^>]*)?>/i.test(html)) throw new Error("Panel asset has no HTML head");
  return html.replace(/<head(?:\s[^>]*)?>/i, (head) => head + script);
}
function countHeader(request, name) {
  let count = 0;
  for (let i = 0; i < request.rawHeaders.length; i += 2) {
    if (request.rawHeaders[i].toLowerCase() === name) count += 1;
  }
  return count;
}
function readBody(request, timeoutMs) {
  return new Promise((done, reject) => {
    const chunks = [];
    let size = 0;
    const cleanup = () => {
      clearTimeout(timer);
      request.removeListener("data", data);
      request.removeListener("end", end);
      request.removeListener("error", error);
      request.removeListener("aborted", aborted);
    };
    const error = (err) => { cleanup(); reject(err); };
    const aborted = () => error(httpError(400, "Request aborted"));
    const data = (chunk) => {
      size += chunk.length;
      if (size > BODY_LIMIT) error(httpError(413, "Request body too large"));
      else chunks.push(chunk);
    };
    const end = () => { cleanup(); done(Buffer.concat(chunks).toString("utf8")); };
    const timer = setTimeout(() => error(httpError(408, "Request body timed out")), timeoutMs);
    request.on("data", data);
    request.once("end", end);
    request.once("error", error);
    request.once("aborted", aborted);
  });
}
function openInBrowser(url) {
  const command = process.platform === "win32" ? "rundll32.exe" : process.platform === "darwin" ? "open" : "xdg-open";
  const args = process.platform === "win32" ? ["url.dll,FileProtocolHandler", url] : [url];
  return new Promise((done) => {
    const child = spawn(command, args, { stdio: "ignore", detached: true, windowsHide: true });
    child.once("error", (error) => done({ browserOpened: false, browserError: error.message }));
    child.once("spawn", () => { child.unref(); done({ browserOpened: true }); });
  });
}

/**
 * Start a loopback-only browser host for the unmodified MCP Apps panel.
 * hostRoot is the installed repository/package root; env may override worker env.
 * url contains a launch token: navigation establishes an HttpOnly session cookie.
 * Use close() (idempotent) to release HTTP connections, RPC timers and the worker.
 */
export async function startPanelServer({
  port = 0, openBrowser = true, hostRoot = DEFAULT_ROOT, env = process.env,
  rpcTimeoutMs = 300000, bodyTimeoutMs = 10000
} = {}) {
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new TypeError("port must be an integer from 0 to 65535");
  for (const [name, value] of Object.entries({ rpcTimeoutMs, bodyTimeoutMs })) {
    if (!Number.isFinite(value) || value < 1 || value > 2147480000) throw new TypeError(`${name} must be a positive timeout`);
  }
  hostRoot = resolve(hostRoot);
  const canonicalRoot = join(hostRoot, "distribution", "zcode", "sanmu-paper-search");
  const panelPath = [join(hostRoot, "assets", "panel.html"), join(canonicalRoot, "assets", "panel.html")]
    .find((path) => existsSync(path));
  if (!panelPath) throw new Error(`Cannot find assets/panel.html under ${hostRoot}`);
  const html = await readFile(panelPath, "utf8");
  const faviconPath = [join(hostRoot, "assets", "favicon.ico"), join(canonicalRoot, "assets", "favicon.ico")]
    .find((path) => existsSync(path));
  const faviconBuffer = faviconPath ? await readFile(faviconPath) : null;
  const cliPath = join(hostRoot, "scripts", "sanmu.mjs");
  const entry = existsSync(cliPath) ? cliPath
    : [join(hostRoot, "scripts", "mcp-server.mjs"), join(canonicalRoot, "scripts", "mcp-server.mjs")]
      .find((path) => existsSync(path));
  if (!entry) throw new Error(`Cannot find the Sanmu MCP entry under ${hostRoot}`);
  const workerEnv = { ...process.env, ...env };
  const workspaceRoot = resolve(workerEnv.SANMU_WORKSPACE_ROOT || process.cwd());
  workerEnv.SANMU_WORKSPACE_ROOT = workspaceRoot;
  workerEnv.SANMU_PANEL_WORKER = "1";
  workerEnv.SANMU_PLUGIN_ROOT ||= entry === cliPath ? hostRoot : resolve(dirname(entry), "..");
  const client = startMcpClient(entry, entry === cliPath ? ["mcp"] : [], {
    env: workerEnv, cwd: workspaceRoot, timeoutMs: rpcTimeoutMs
  });
  const launchToken = randomBytes(32).toString("hex");
  const cookieName = "sanmu_panel_" + randomBytes(8).toString("hex");
  const cookieSecret = randomBytes(32).toString("hex");
  let origin = "";
  let closed = false;
  let closePromise;
  let modelContext = null;
  let toolsResult;
  const sockets = new Set();
  const authenticated = (request) => String(request.headers.cookie || "").split(";").some((part) => {
    const [name, ...rest] = part.trim().split("=");
    return name === cookieName && safeEqual(rest.join("="), cookieSecret);
  });
  function respond(request, response, status, body = "", contentType = "text/plain; charset=utf-8") {
    if (response.destroyed || response.writableEnded) return;
    response.writeHead(status, {
      "Content-Type": contentType, "Content-Length": Buffer.byteLength(body),
      "Cache-Control": "no-store", "Referrer-Policy": "no-referrer", "X-Content-Type-Options": "nosniff",
      "X-Frame-Options": "SAMEORIGIN",
      "Content-Security-Policy": "default-src 'none'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; img-src 'self' data: blob: https: http:; font-src 'self' data:; frame-src 'self'; frame-ancestors 'self'; base-uri 'none'; form-action 'none'; object-src 'none'"
    });
    response.end(request.method === "HEAD" ? undefined : body);
  }
  async function dispatch(method, params = {}) {
    if (!isObject(params)) throw rpcError(-32602, "RPC params must be an object");
    if (method === "ui/initialize") return {
      protocolVersion: "2026-01-26",
      hostInfo: { name: "sanmu-browser", version: "1.10.1" },
      hostCapabilities: { serverTools: {}, openLinks: {} },
      hostContext: { theme: "light", locale: "zh-CN", displayMode: "standalone" }
    };
    if (method === "tools/list") return toolsResult;
    if (method === "tools/call") {
      if (typeof params.name !== "string" || !params.name.trim() ||
          (params.arguments !== undefined && !isObject(params.arguments))) {
        throw rpcError(-32602, "tools/call requires a tool name and object arguments");
      }
      return client.request("tools/call", params); // Preserve the complete MCP result, including isError and _meta.
    }
    if (method === "ui/open-link") return { url: safeLink(params.url), openInBrowser: true };
    if (method === "ui/update-model-context") {
      if (!Array.isArray(params.content)) throw rpcError(-32602, "Model context requires content blocks");
      modelContext = params;
      return { storedLocally: true, delivered: false, fallback: "copy" };
    }
    if (method === "ui/message") return {
      isError: true, delivered: false, fallback: "copy", prompt: promptText(params), context: modelContext,
      message: "浏览器宿主无法直接发送到当前 AI 对话。请复制弹窗中的精读 / 分析指令并粘贴到对话。"
    };
    throw rpcError(-32601, `Unsupported browser-host method: ${method}`);
  }
  async function handle(request, response) {
    if (closed) throw httpError(503, "Panel server closed");
    if (countHeader(request, "host") !== 1 || request.headers.host !== new URL(origin).host) {
      throw httpError(403, "Invalid Host");
    }
    if (countHeader(request, "origin") > 1 ||
        (request.headers.origin !== undefined && request.headers.origin !== origin)) {
      throw httpError(403, "Invalid Origin");
    }
    if (!request.url.startsWith("/") || request.url.startsWith("//") || request.url.includes("\\")) {
      throw httpError(400, "Invalid request target");
    }
    const url = new URL(request.url, origin);
    if (["GET", "HEAD"].includes(request.method)) {
      if (url.pathname === "/favicon.ico") {
        if (faviconBuffer) return respond(request, response, 200, faviconBuffer, "image/x-icon");
        return respond(request, response, 204);
      }
      const tokens = url.searchParams.getAll("token");
      const launched = tokens.length === 1 && safeEqual(tokens[0], launchToken);
      if (tokens.length && !launched) throw httpError(403, "Invalid launch token");
      if (!launched && !authenticated(request)) throw httpError(401, "请使用 sanmu panel 提供的完整本地链接打开面板。");
      if (launched) response.setHeader("Set-Cookie", `${cookieName}=${cookieSecret}; HttpOnly; SameSite=Strict; Path=/`);
      const frameId = url.pathname === "/host" ? "sanmu-panel-frame" : undefined;
      let page = html;
      if (frameId) page = '<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>三木文献检索</title><style>html,body{height:100%;margin:0}iframe{display:block;width:100%;height:100%;border:0}</style></head><body><iframe id="sanmu-panel-frame" title="Sanmu Paper Search" src="/panel"></iframe></body></html>';
      else if (!["/", "/panel", "/standalone"].includes(url.pathname)) throw httpError(404, "Not found");
      return respond(request, response, 200, injectBridge(page, { frameId, rpcTimeoutMs }), "text/html; charset=utf-8");
    }
    if (request.method !== "POST" || url.pathname !== "/rpc") throw httpError(405, "Method not allowed");
    if (request.headers.origin !== origin || ["cross-site", "same-site"].includes(request.headers["sec-fetch-site"])) {
      throw httpError(403, "RPC requires a same-origin request");
    }
    if (!authenticated(request)) throw httpError(401, "Missing or invalid panel session");
    if (!/^application\/json(?:\s*;|$)/i.test(request.headers["content-type"] || "")) {
      throw httpError(415, "RPC requires application/json");
    }
    if (Number(request.headers["content-length"]) > BODY_LIMIT) throw httpError(413, "Request body too large");
    const raw = await readBody(request, bodyTimeoutMs);
    let message;
    try { message = JSON.parse(raw); } catch {
      return respond(request, response, 200, JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }), "application/json; charset=utf-8");
    }
    if (!isObject(message) || message.jsonrpc !== "2.0" || typeof message.method !== "string" ||
        !Object.hasOwn(message, "id") || !(message.id === null || typeof message.id === "string" ||
          (typeof message.id === "number" && Number.isFinite(message.id)))) {
      return respond(request, response, 200, JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32600, message: "Invalid JSON-RPC request" } }), "application/json; charset=utf-8");
    }
    let reply;
    try { reply = { jsonrpc: "2.0", id: message.id, result: await dispatch(message.method, message.params) }; }
    catch (error) {
      reply = { jsonrpc: "2.0", id: message.id, error: { code: Number.isInteger(error.code) ? error.code : -32603, message: error.message || "RPC failed" } };
    }
    respond(request, response, 200, JSON.stringify(reply), "application/json; charset=utf-8");
  }
  const server = createServer((request, response) => {
    void handle(request, response).catch((error) => {
      response.setHeader("Connection", "close");
      respond(request, response, error.status || 500, error.status ? error.message : "Panel host request failed");
      request.resume();
    });
  });
  server.headersTimeout = 10000;
  server.requestTimeout = Math.max(bodyTimeoutMs, 10000);
  server.keepAliveTimeout = 1000;
  server.on("connection", (socket) => {
    sockets.add(socket);
    socket.once("close", () => sockets.delete(socket));
  });
  const registration = { close, killSync: client.killSync };
  registerPanel(registration);
  async function close() {
    if (closePromise) return closePromise;
    closed = true;
    closePromise = (async () => {
      const stopped = new Promise((done) => {
        if (!server.listening) return done();
        server.close(done);
        for (const socket of sockets) socket.destroy();
      });
      try { await Promise.all([stopped, client.close()]); }
      finally { unregisterPanel(registration); }
    })();
    return closePromise;
  }
  server.once("close", () => { if (!closed) void close(); });
  try {
    const initialization = await client.request("initialize", {
      protocolVersion: "2025-11-25", capabilities: {}, clientInfo: { name: "sanmu-browser", version: "1.10.1" }
    }, Math.min(rpcTimeoutMs, 30000));
    client.notify("notifications/initialized");
    toolsResult = { tools: [] };
    const cursors = new Set();
    let cursor;
    do {
      const page = await client.request("tools/list", cursor ? { cursor } : {}, Math.min(rpcTimeoutMs, 30000));
      if (!Array.isArray(page?.tools)) throw new Error("MCP tools/list did not return tool schemas");
      toolsResult.tools.push(...page.tools);
      cursor = page.nextCursor;
      if (cursor && (typeof cursor !== "string" || cursors.has(cursor))) throw new Error("Invalid MCP tools/list cursor");
      if (cursor) cursors.add(cursor);
    } while (cursor);
    if (closed) throw new Error("Panel server closed during startup");
    await new Promise((done, reject) => {
      server.once("error", reject);
      server.listen(port, "127.0.0.1", () => { server.removeListener("error", reject); done(); });
    });
    origin = `http://127.0.0.1:${server.address().port}`;
    const url = `${origin}/?token=${launchToken}`;
    const browser = openBrowser ? await openInBrowser(url) : { browserOpened: false };
    return { url, origin, server, close, child: client.child, panelPath, hostRoot, workspaceRoot,
      tools: toolsResult.tools, mcpInfo: initialization.serverInfo, ...browser };
  } catch (error) {
    await close();
    throw error;
  }
}
