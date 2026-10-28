import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  mkdtempSync,
  readFileSync,
  readdirSync,
  realpathSync,
  renameSync,
  statSync,
  writeFileSync,
  copyFileSync,
  constants as fsConstants,
  rmSync
} from "node:fs";
import { homedir } from "node:os";
import { delimiter as pathDelimiter, basename, dirname, extname, join, relative, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath, pathToFileURL } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

function resolveEnvPath(val) {
  if (!val || typeof val !== "string") return "";
  const trimmed = val.trim();
  if (!trimmed || /\$\{[^}]+\}/.test(trimmed)) return "";
  return trimmed;
}

function readUserConfigFromEnv(envName, envObj = process.env) {
  return resolveEnvPath(envObj?.[envName]);
}

function resolveHostPaths(envObj = process.env) {
  const pluginRoot =
    resolveEnvPath(envObj?.SANMU_PLUGIN_ROOT) ||
    resolveEnvPath(envObj?.CLAUDE_PLUGIN_ROOT) ||
    resolveEnvPath(envObj?.PLUGIN_ROOT) ||
    resolve(__dirname, "..");
  const workspaceRoot =
    resolveEnvPath(envObj?.SANMU_WORKSPACE_ROOT) ||
    resolveEnvPath(envObj?.ZCODE_WORKSPACE_ROOT) ||
    resolveEnvPath(envObj?.ZCODE_PROJECT_DIR) ||
    resolveEnvPath(envObj?.DSH_WORKSPACE_ROOT) ||
    resolveEnvPath(envObj?.CLAUDE_PROJECT_DIR) ||
    process.cwd();
  const dshHome = resolveEnvPath(envObj?.DSH_HOME);
  const pluginData =
    resolveEnvPath(envObj?.SANMU_DATA_DIR) ||
    resolveEnvPath(envObj?.SANMU_PLUGIN_DATA) ||
    resolveEnvPath(envObj?.ZCODE_PLUGIN_DATA) ||
    resolveEnvPath(envObj?.DSH_PLUGIN_DATA) ||
    resolveEnvPath(envObj?.CLAUDE_PLUGIN_DATA) ||
    resolveEnvPath(envObj?.PLUGIN_DATA) ||
    (dshHome
      ? join(dshHome, "plugin-data", "sanmu-paper-search")
      : join(homedir(), envObj?.SANMU_HOST ? ".sanmu" : ".zcode", "plugin-data", "sanmu-paper-search"));
  return { pluginRoot, workspaceRoot, pluginData };
}

const { pluginRoot: PLUGIN_ROOT, workspaceRoot: INITIAL_WORKSPACE_ROOT, pluginData: PLUGIN_DATA } = resolveHostPaths(process.env);
let WORKSPACE_ROOT = INITIAL_WORKSPACE_ROOT;
const runtimePath = join(PLUGIN_ROOT, "scripts", "runtime.mjs");
const sharedRuntime = existsSync(runtimePath) ? await import(pathToFileURL(runtimePath).href) : null;
let preparedRuntime = null;
const zoteroToolsPath = join(PLUGIN_ROOT, "scripts", "zotero-tools.mjs");
const zoteroTools = existsSync(zoteroToolsPath) ? await import(pathToFileURL(zoteroToolsPath).href) : null;

const RUNS_DIR = join(PLUGIN_DATA, "runs");
const DOC2MD_JOBS_DIR = join(PLUGIN_DATA, "doc2md-jobs");
const CONFIG_PATH = join(PLUGIN_DATA, "config.json");
const STATE_PATH = join(PLUGIN_DATA, "state.json");
const EASYSCHOLAR_CACHE_PATH = join(PLUGIN_DATA, "easyscholar-cache.json");
const ZOTERO_KEYS_PATH = join(PLUGIN_DATA, "zotero-local-keys.json");
const OFFICIAL_ZOTERO_KEYS_PATH = join(dirname(PLUGIN_DATA), "zotero@zcode-plugins-official", "local-api-keys.json");
const PANEL_URI = "ui://sanmu-paper-search/panel.html";
const SURFACE_ID = "sanmu-paper-search";
const HTML_MIME = "text/html;profile=mcp-app";
const PANEL_HTML_PATH = [join(PLUGIN_ROOT, "assets", "panel.html"), join(PLUGIN_ROOT, "distribution", "zcode", "sanmu-paper-search", "assets", "panel.html")].find(existsSync) || join(PLUGIN_ROOT, "assets", "panel.html");
const SKILLS_ROOT = resolveEnvPath(process.env.SANMU_SKILLS_ROOT) || join(PLUGIN_ROOT, "skills");
const SEARCH_SCRIPTS = existsSync(join(SKILLS_ROOT, "sanmu-paper-search", "scripts", "paper_search.py"))
  ? join(SKILLS_ROOT, "sanmu-paper-search", "scripts") : join(PLUGIN_ROOT, "scripts");
const PAPER_SEARCH_PY = join(SEARCH_SCRIPTS, "paper_search.py");
const FETCH_PDF_PY = join(SEARCH_SCRIPTS, "fetch_pdf.py");
let browserPanel = null;

async function ensureBrowserPanel() {
  if (browserPanel && browserPanel.workspaceRoot === WORKSPACE_ROOT) return browserPanel;
  if (browserPanel) { await browserPanel.close(); browserPanel = null; }
  const panelServerPath = join(PLUGIN_ROOT, "scripts", "panel-server.mjs");
  if (!existsSync(panelServerPath)) throw new Error("浏览器工作台启动器缺失，请重新安装完整的 sanmu-skills 插件。");
  const { startPanelServer } = await import(pathToFileURL(panelServerPath).href);
  browserPanel = await startPanelServer({ hostRoot: PLUGIN_ROOT, openBrowser: false,
    env: { ...process.env, SANMU_PLUGIN_ROOT: PLUGIN_ROOT, SANMU_DATA_DIR: PLUGIN_DATA, SANMU_WORKSPACE_ROOT: WORKSPACE_ROOT } });
  return browserPanel;
}

async function prepareDocumentRuntime() {
  if (!sharedRuntime) return;
  preparedRuntime = await sharedRuntime.ensureRuntime({ root: PLUGIN_ROOT, dataDir: PLUGIN_DATA, env: getEffectiveConfig().childEnv });
  cachedPythonRuntime = null;
  cachedKatexInfo = null;
}

mkdirSync(RUNS_DIR, { recursive: true });
mkdirSync(DOC2MD_JOBS_DIR, { recursive: true });

let cachedDoc2mdScriptsDir = null;
let cachedKatexInfo = null;

function resolveDoc2mdScriptsDir() {
  if (cachedDoc2mdScriptsDir && existsSync(join(cachedDoc2mdScriptsDir, "build_markdown.py"))) {
    return cachedDoc2mdScriptsDir;
  }
  const candidates = [
    join(SKILLS_ROOT, "sanmu-doc2md", "scripts"),
    resolve(PLUGIN_ROOT, "..", "..", "skills", "sanmu-doc2md", "scripts"),
    resolve(__dirname, "..", "..", "..", "..", "skills", "sanmu-doc2md", "scripts"),
    join(WORKSPACE_ROOT, ".agents", "skills", "sanmu-doc2md", "scripts"),
    join(WORKSPACE_ROOT, ".agents", "repos", "wusen-skills", "skills", "sanmu-doc2md", "scripts"),
    join(homedir(), ".agents", "skills", "sanmu-doc2md", "scripts"),
    join(homedir(), ".agents", "repos", "wusen-skills", "skills", "sanmu-doc2md", "scripts")
  ];
  for (const dir of candidates) {
    try {
      if (existsSync(join(dir, "build_markdown.py"))) {
        cachedDoc2mdScriptsDir = dir;
        return dir;
      }
    } catch {}
  }
  return candidates[0];
}

function resolveKatexNodeModules(doc2mdScriptsDir = resolveDoc2mdScriptsDir()) {
  if (
    cachedKatexInfo?.nodeModulesDir &&
    existsSync(join(cachedKatexInfo.nodeModulesDir, "katex", "package.json"))
  ) {
    return cachedKatexInfo;
  }
  const candidates = [
    join(doc2mdScriptsDir, "node_modules"),
    join(resolve(__dirname, "..", "..", "..", "..", "skills", "sanmu-doc2md", "scripts"), "node_modules"),
    join(WORKSPACE_ROOT, ".agents", "skills", "sanmu-doc2md", "scripts", "node_modules"),
    join(WORKSPACE_ROOT, ".agents", "repos", "wusen-skills", "skills", "sanmu-doc2md", "scripts", "node_modules"),
    join(homedir(), ".agents", "skills", "sanmu-doc2md", "scripts", "node_modules"),
    join(homedir(), ".agents", "repos", "wusen-skills", "skills", "sanmu-doc2md", "scripts", "node_modules"),
    join(PLUGIN_DATA, "doc2md-node", "node_modules")
  ];
  for (const nmDir of candidates) {
    try {
      if (existsSync(join(nmDir, "katex", "package.json"))) {
        const distDir = join(nmDir, "katex", "dist");
        cachedKatexInfo = {
          nodeModulesDir: nmDir,
          katexDistDir: existsSync(distDir) ? distDir : null
        };
        return cachedKatexInfo;
      }
    } catch {}
  }
  return { nodeModulesDir: null, katexDistDir: null };
}

const STAGE_LABELS = {
  discovered: "待初筛",
  metadata_checked: "已读摘要",
  file_obtained: "已获PDF",
  identity_checked: "已核全文",
  read: "已精读",
  excluded: "已排除"
};

let cachedPythonRuntime = null;

function resolvePythonRuntime() {
  if (preparedRuntime) return preparedRuntime;
  if (sharedRuntime) return sharedRuntime.resolvePythonRuntime({ env: process.env, dataDir: PLUGIN_DATA });
  if (cachedPythonRuntime) return cachedPythonRuntime;
  const candidates =
    process.platform === "win32"
      ? [
          { cmd: "python", prefixArgs: [] },
          { cmd: "py", prefixArgs: ["-3"] },
          { cmd: "python3", prefixArgs: [] }
        ]
      : [
          { cmd: "python3", prefixArgs: [] },
          { cmd: "python", prefixArgs: [] }
        ];

  for (const cand of candidates) {
    try {
      const res = spawnSync(
        cand.cmd,
        [...cand.prefixArgs, "-c", "import sys; assert sys.version_info >= (3, 10); print(sys.version.split()[0])"],
        { encoding: "utf8", timeout: 5000, windowsHide: true }
      );
      if (res.status === 0 && res.stdout && res.stdout.trim()) {
        cachedPythonRuntime = {
          cmd: cand.cmd,
          prefixArgs: cand.prefixArgs,
          version: res.stdout.trim(),
          supported: true
        };
        return cachedPythonRuntime;
      }
    } catch {
      // Try next candidate
    }
  }
  return {
    cmd: "python",
    prefixArgs: [],
    version: null,
    supported: false
  };
}

function readJsonSafe(filePath, fallback) {
  try {
    if (!existsSync(filePath)) return fallback;
    return JSON.parse(readFileSync(filePath, "utf8"));
  } catch {
    return fallback;
  }
}

const WAIT_SYNC_ARRAY = typeof SharedArrayBuffer === "function" ? new Int32Array(new SharedArrayBuffer(4)) : null;

function sleepSyncMs(ms) {
  if (WAIT_SYNC_ARRAY && ms > 0) {
    try {
      Atomics.wait(WAIT_SYNC_ARRAY, 0, 0, ms);
      return;
    } catch {}
  }
  const end = Date.now() + ms;
  while (Date.now() < end) {}
}

function readLockOwner(lockPath) {
  try {
    const owner = JSON.parse(readFileSync(lockPath, "utf8"));
    return { pid: Number(owner?.pid) || 0, time: Number(owner?.time) || 0 };
  } catch {
    return null;
  }
}

function isProcessAlive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch (err) {
    return Boolean(err && err.code === "EPERM");
  }
}

const fileLockDepth = new Map();

function withFileLockSync(targetFilePath, fn, timeoutMs = 5000) {
  const depth = fileLockDepth.get(targetFilePath) || 0;
  if (depth > 0) {
    fileLockDepth.set(targetFilePath, depth + 1);
    try {
      return fn();
    } finally {
      fileLockDepth.set(targetFilePath, depth);
    }
  }
  mkdirSync(dirname(targetFilePath), { recursive: true });
  const lockPath = `${targetFilePath}.lock`;
  const deadline = Date.now() + timeoutMs;
  let acquired = false;
  while (!acquired) {
    try {
      writeFileSync(lockPath, JSON.stringify({ pid: process.pid, time: Date.now() }), {
        encoding: "utf8",
        mode: 0o600,
        flag: "wx"
      });
      acquired = true;
    } catch (err) {
      if (err && err.code === "EEXIST") {
        const owner = readLockOwner(lockPath);
        // Never steal a lock from a live process: doing so silently allows two
        // writers to apply stale snapshots. Only reclaim provably dead owners
        // (or an unreadable/leftover lock owned by this same pid).
        if (!owner || !isProcessAlive(owner.pid) || owner.pid === process.pid) {
          rmSync(lockPath, { force: true });
          continue;
        }
        if (Date.now() >= deadline) {
          throw new Error(
            `插件状态正被进程 ${owner.pid} 写入，请稍后重试（未强行抢占锁）。`
          );
        }
        sleepSyncMs(10);
        continue;
      }
      throw err;
    }
  }
  fileLockDepth.set(targetFilePath, 1);
  try {
    return fn();
  } finally {
    fileLockDepth.set(targetFilePath, 0);
    try {
      rmSync(lockPath, { force: true });
    } catch {}
  }
}

function writeJsonSafe(filePath, data) {
  mkdirSync(dirname(filePath), { recursive: true });
  const tempPath = `${filePath}.${process.pid}.${Math.random().toString(36).slice(2)}.tmp`;
  try {
    writeFileSync(tempPath, JSON.stringify(data, null, 2) + "\n", { encoding: "utf8", mode: 0o600, flag: "wx" });
    renameSync(tempPath, filePath);
  } finally {
    if (existsSync(tempPath)) rmSync(tempPath, { force: true });
  }
}

function readStoredObject(filePath) {
  if (!existsSync(filePath)) return {};
  let value;
  try {
    value = JSON.parse(readFileSync(filePath, "utf8"));
  } catch {
    throw new Error(`无法读取插件数据 ${basename(filePath)}；已保留原文件，请修复 JSON 后重试。`);
  }
  if (!value || typeof value !== "object" || Array.isArray(value)) {
    throw new Error(`插件数据 ${basename(filePath)} 必须为 JSON 对象；已保留原文件。`);
  }
  return value;
}

function loadPanelConfig() {
  // Defaults are applied after inheritance so an absent panel setting does not hide userConfig.
  return readStoredObject(CONFIG_PATH);
}

const API_PORTAL_URLS = {
  sciverse_api_key: "https://sciverse.space",
  ai4scholar_api_key: "https://ai4scholar.net",
  easyscholar_secret_key: "https://www.easyscholar.cc",
  polite_email: "https://unpaywall.org/products/api",
  serper_api_key: "https://serper.dev/api-key",
  exa_api_key: "https://dashboard.exa.ai/api-keys",
  semantic_scholar_api_key: "https://www.semanticscholar.org/product/api#api-key-form",
  openalex_api_key: "https://openalex.org/settings/api",
  mineru_api_token: "https://mineru.net/apiManage/token"
};

function cleanSecretInput(raw, stripAllWhitespace = false) {
  if (typeof raw !== "string") return "";
  let s = raw.trim().replace(/^["']+|["']+$/g, "").trim();
  s = s.replace(/^Bearer\s+/i, "").trim();
  if (stripAllWhitespace) {
    s = s.replace(/\s+/g, "");
  }
  return s;
}

function maskCredentialValue(val, isEmail = false) {
  const s = String(val || "").trim();
  if (!s) return "";
  if (isEmail) {
    const atIdx = s.indexOf("@");
    if (atIdx > 2) return `${s.slice(0, 2)}•••${s.slice(atIdx)}`;
    return s;
  }
  if (s.length >= 10) {
    return `${s.slice(0, 3)}••••••${s.slice(-2)}`;
  }
  return "••••••••";
}

function savePanelConfig(patch) {
  return withFileLockSync(CONFIG_PATH, () => {
    const current = loadPanelConfig();
    const next = { ...current };
    const secretKeys = new Set([
      "openalexApiKey",
      "s2ApiKey",
      "exaApiKey",
      "serperApiKey",
      "easyscholarSecretKey",
      "ai4scholarApiKey",
      "sciverseApiKey",
      "mineruApiToken",
      "mineruApiKey"
    ]);
    for (const [k, v] of Object.entries(patch || {})) {
      if (k === "clearKeys" || v === undefined || v === null) continue;
      if (typeof v === "string") {
        if (secretKeys.has(k)) {
          const cleaned = cleanSecretInput(v, k === "easyscholarSecretKey");
          if (cleaned !== "") {
            next[k] = cleaned;
            if (k === "mineruApiToken" && "mineruApiKey" in next) {
              delete next.mineruApiKey;
            }
          }
        } else if (v.trim() !== "" || k === "politeEmail") {
          next[k] = v.trim();
        }
      } else {
        next[k] = v;
      }
    }
    if (Array.isArray(patch?.clearKeys)) {
      for (const k of patch.clearKeys) {
        if (k in next) next[k] = "";
        if (k === "mineruApiToken" && "mineruApiKey" in next) next.mineruApiKey = "";
        if (k === "mineruApiKey" && "mineruApiToken" in next) next.mineruApiToken = "";
      }
    }
    writeJsonSafe(CONFIG_PATH, next);
    return next;
  });
}

function loadState() {
  const saved = readStoredObject(STATE_PATH);
  if ((saved.basket !== undefined && !Array.isArray(saved.basket)) ||
      (saved.evidenceMap !== undefined && (!saved.evidenceMap || typeof saved.evidenceMap !== "object" || Array.isArray(saved.evidenceMap))) ||
      (saved.evidence !== undefined && (!saved.evidence || typeof saved.evidence !== "object" || Array.isArray(saved.evidence)))) {
    throw new Error("state.json 中的收藏篮或阅读记录格式无效；已保留原文件。");
  }
  const mergedEvidenceMap = {
    ...(saved.evidence || {}),
    ...(saved.evidenceMap || {})
  };
  return {
    lastRunId: null,
    runNavHistory: [],
    lastZoteroImport: null,
    basket: [],
    ...saved,
    evidenceMap: mergedEvidenceMap,
    evidence: mergedEvidenceMap,
    zoteroImports: saved.zoteroImports || {},
    runExports: saved.runExports || {}
  };
}

function recordMainRunNavigation(state, runId) {
  if (!runId) return;
  const cleanId = basename(String(runId));
  state.lastRunId = cleanId;
  const hist = Array.isArray(state.runNavHistory) ? state.runNavHistory.filter((id) => id !== cleanId) : [];
  hist.push(cleanId);
  state.runNavHistory = hist.slice(-15);
}

function saveState(state) {
  if (state && typeof state === "object") {
    state.evidence = state.evidenceMap || state.evidence || {};
  }
  writeJsonSafe(STATE_PATH, state);
  return state;
}

function updateStateTransaction(mutator) {
  return withFileLockSync(STATE_PATH, () => {
    const state = loadState();
    const res = mutator(state);
    if (res !== false) {
      saveState(state);
    }
    return state;
  });
}

function isAbsolutePath(p) {
  return Boolean(p && (/^[A-Za-z]:[\\/]/.test(p) || p.startsWith("/") || p.startsWith("\\\\")));
}

function isWithin(root, target) {
  const rel = relative(resolve(root), resolve(target));
  return rel === "" || (rel !== ".." && !rel.startsWith(`..${process.platform === "win32" ? "\\" : "/"}`) && !isAbsolutePath(rel));
}

function resolveRunDir(runId, mustExist = true) {
  if (typeof runId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(runId)) {
    throw new Error("无效的批次 ID（只接受历史记录中的单个 ID）。");
  }
  const target = join(RUNS_DIR, runId);
  if (mustExist && (!existsSync(target) || !statSync(target).isDirectory())) {
    throw new Error(`未找到指定批次: ${runId}`);
  }
  if (existsSync(target) && !isWithin(realpathSync(RUNS_DIR), realpathSync(target))) {
    throw new Error("批次路径越出插件历史目录。");
  }
  return target;
}

function assertReadablePath(target) {
  const actual = existsSync(target) ? realpathSync(target) : resolve(target);
  for (const privateFile of [CONFIG_PATH, STATE_PATH, ZOTERO_KEYS_PATH, OFFICIAL_ZOTERO_KEYS_PATH]) {
    const protectedPath = existsSync(privateFile) ? realpathSync(privateFile) : resolve(privateFile);
    if (relative(protectedPath, actual) === "") throw new Error("插件私有配置、阅读状态与授权密钥文件不能通过文件工具读取或打开。");
  }
}

function uniqueDestination(dir, filename, shortIdHint = "") {
  const ext = extname(filename);
  const stem = basename(filename, ext);
  let candidate = join(dir, filename);
  if (!existsSync(candidate)) return candidate;
  const cleanHint = String(shortIdHint || "")
    .replace(/^10\.\d{4,9}\//i, "")
    .replace(/[^A-Za-z0-9_-]+/g, "")
    .slice(-12);
  if (cleanHint) {
    const hinted = join(dir, `${stem}-${cleanHint}${ext}`);
    if (!existsSync(hinted)) return hinted;
  }
  for (let n = 2; existsSync(candidate); n++) candidate = join(dir, `${stem}-${n}${ext}`);
  return candidate;
}

function redactSecrets(value) {
  const cfg = readJsonSafe(CONFIG_PATH, {}) || {};
  const secrets = new Set();
  for (const key of ["openalexApiKey", "s2ApiKey", "exaApiKey", "serperApiKey", "easyscholarSecretKey", "ai4scholarApiKey", "sciverseApiKey", "mineruApiToken", "mineruApiKey"]) {
    if (typeof cfg[key] === "string" && cfg[key].trim()) {
      secrets.add(cfg[key].trim());
      secrets.add(cfg[key].replace(/\s+/g, "").trim());
    }
  }
  const explicitEnvSecretKeys = new Set([
    "USER_CONFIG_OPENALEX_API_KEY",
    "USER_CONFIG_S2_API_KEY",
    "USER_CONFIG_EXA_API_KEY",
    "USER_CONFIG_SERPER_API_KEY",
    "USER_CONFIG_EASYSCHOLAR_SECRET_KEY",
    "USER_CONFIG_AI4SCHOLAR_API_KEY",
    "USER_CONFIG_SCIVERSE_API_KEY",
    "USER_CONFIG_MINERU_API_TOKEN",
    "OPENALEX_API_KEY",
    "S2_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY",
    "EXA_API_KEY",
    "SERPER_API_KEY",
    "EASYSCHOLAR_SECRET_KEY",
    "AI4SCHOLAR_API_KEY",
    "SCIVERSE_API_KEY",
    "SCIVERSE_API_TOKEN",
    "MINERU_API_TOKEN"
  ]);
  for (const [key, val] of Object.entries(process.env)) {
    if (!val || typeof val !== "string" || val.startsWith("${")) continue;
    const trimmed = val.trim();
    if (!trimmed) continue;
    if (explicitEnvSecretKeys.has(key)) {
      secrets.add(trimmed);
      secrets.add(trimmed.replace(/\s+/g, ""));
    } else if (
      /(?:API_KEY|TOKEN|SECRET|SECRET_KEY|PASSWORD)$/i.test(key) &&
      trimmed.length >= 6 &&
      !/^(?:true|false|null|none|undefined|\d+)$/i.test(trimmed)
    ) {
      secrets.add(trimmed);
      secrets.add(trimmed.replace(/\s+/g, ""));
    }
  }
  for (const keyFile of [ZOTERO_KEYS_PATH, OFFICIAL_ZOTERO_KEYS_PATH]) {
    for (const key of Object.values(readJsonSafe(keyFile, {}) || {})) {
      if (typeof key === "string" && key) secrets.add(key);
    }
  }
  const visit = (item) => {
    if (typeof item === "string") {
      for (const secret of [...secrets].filter(Boolean).sort((a, b) => b.length - a.length)) item = item.split(secret).join("[REDACTED]");
      return item.replace(/([?&](?:api[_-]?key|secret[_-]?key|key|token|access_token|secret|password)=)[^&#\s"<>]+/gi, "$1[REDACTED]");
    }
    if (Array.isArray(item)) return item.map(visit);
    if (item && typeof item === "object") return Object.fromEntries(Object.entries(item).map(([key, val]) => [key, visit(val)]));
    return item;
  };
  return visit(value);
}

function validateZoteroUrl(value) {
  const url = new URL(value);
  if (!["http:", "https:"].includes(url.protocol) || !["localhost", "127.0.0.1", "[::1]"].includes(url.hostname) ||
      url.username || url.password || url.search || url.hash || !["", "/"].includes(url.pathname)) {
    throw new Error("Zotero 地址必须为本机 localhost、127.0.0.1 或 [::1] 的 HTTP(S) 服务地址。");
  }
  return url.origin;
}

function resolveTargetSaveDir(customDir) {
  const { settings } = getEffectiveConfig();
  const raw = String(customDir || settings.exportSubdir || "00-收件箱-待整理/文献检索").trim();
  const destDir = isAbsolutePath(raw)
    ? resolve(raw)
    : resolve(WORKSPACE_ROOT, raw.replace(/^[/\\]+/, ""));
  mkdirSync(destDir, { recursive: true });
  return destDir;
}

function makeSafeFileStem(year, title, fallbackId = "paper", doiOrKey = "") {
  const cleanTitle = String(title || fallbackId || "paper")
    .replace(/[\\/:*?"<>|\r\n\t]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 76);
  const yrPrefix = year && /^\d{4}$/.test(String(year)) ? `${year}-` : "";
  const baseStem = `${yrPrefix}${cleanTitle || "paper"}`;
  if (!cleanTitle && doiOrKey) {
    const shortSuffix = String(doiOrKey).replace(/[^A-Za-z0-9_-]+/g, "").slice(-10);
    if (shortSuffix) return `${baseStem}-${shortSuffix}`;
  }
  return baseStem;
}

function scanSaveDirPdfIndex(customDir = null) {
  const byExactStem = new Map();
  const entries = [];
  try {
    const saveDir = resolveTargetSaveDir(customDir);
    if (!existsSync(saveDir)) return { byExactStem, entries };
    for (const entry of readdirSync(saveDir, { withFileTypes: true })) {
      if (!entry.isFile() || extname(entry.name).toLowerCase() !== ".pdf") continue;
      const fullPath = join(saveDir, entry.name);
      const stemLower = basename(entry.name, extname(entry.name)).toLowerCase().trim();
      const normAlnum = stemLower.replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "");
      byExactStem.set(stemLower, fullPath);
      entries.push({ stemLower, normAlnum, fullPath });
    }
  } catch {
    // Ignore scan errors on inaccessible directories
  }
  return { byExactStem, entries };
}

const localPdfFirstPageTextCache = new Map();

function extractPdfFirstPageTextSync(pdfPath) {
  if (!pdfPath || !existsSync(pdfPath)) return "";
  const resolved = resolve(pdfPath);
  const sig = `${resolved}|${fileStatSignature(resolved)}`;
  if (localPdfFirstPageTextCache.has(sig)) {
    return localPdfFirstPageTextCache.get(sig);
  }
  let buf;
  try {
    buf = readFileSync(resolved);
  } catch {
    return "";
  }
  if (!inspectPdfBufferNode(buf)) {
    localPdfFirstPageTextCache.set(sig, "");
    return "";
  }
  let text = "";
  const pyRt = resolvePythonRuntime();
  if (pyRt.supported) {
    try {
      const res = spawnSync(
        pyRt.cmd,
        [
          ...pyRt.prefixArgs,
          "-c",
          "import fitz, sys\nwith fitz.open(sys.argv[1]) as doc:\n    print(doc[0].get_text() if len(doc) else '')",
          resolved
        ],
        { encoding: "utf8", timeout: 8000, windowsHide: true }
      );
      if (res.status === 0 && typeof res.stdout === "string") {
        text = res.stdout;
      }
    } catch {}
  }
  localPdfFirstPageTextCache.set(sig, text);
  return text;
}

function verifyLocalPdfIdentitySync(pdfPath, paper) {
  if (!pdfPath || !paper || !existsSync(pdfPath)) {
    return { status: "needs_review", matched: false };
  }
  const targetDoi = normalizeDoi(paper?.identifiers?.doi?.[0] || "");
  const clean = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
  const targetTitle = clean(paper?.title || "");
  if (!targetDoi && !targetTitle) {
    return { status: "not_checked", matched: false };
  }
  const rawText = extractPdfFirstPageTextSync(pdfPath);
  if (!rawText || !rawText.trim()) {
    return { status: "needs_review", matched: false };
  }
  const heading = String(rawText)
    .split(/^\s*(?:references|bibliography|参考文献)\s*[:：]?\s*$/im)[0]
    .slice(0, 5000);
  const observedDois = new Set(
    (heading.match(/10\.\d{4,9}\/[^\s<>"']+/gi) || [])
      .map((d) => normalizeDoi(d))
      .filter(Boolean)
  );
  const doiFound = Boolean(targetDoi && observedDois.has(targetDoi));
  const hasConflictingDoi = Boolean(targetDoi && observedDois.size > 0 && !doiFound);
  const titleFound = Boolean(targetTitle && clean(heading).includes(targetTitle));
  const matched = Boolean((targetDoi ? doiFound : titleFound) && (targetTitle ? titleFound : true) && !hasConflictingDoi);
  return {
    status: matched ? "matched" : "needs_review",
    matched,
    doi_found: doiFound,
    title_found: titleFound,
    has_conflicting_doi: hasConflictingDoi
  };
}

function buildEvidencePdfOwnersMap(state) {
  const ownersByPath = new Map();
  for (const [k, ev] of Object.entries(state?.evidenceMap || {})) {
    if (ev?.pdfPath && typeof ev.pdfPath === "string" && existsSync(ev.pdfPath)) {
      ownersByPath.set(resolve(ev.pdfPath), k);
    }
  }
  return ownersByPath;
}

function matchLocalSavedPdfCandidate(paper, pdfIndex, currentPaperKey = null, ownersByPath = null) {
  if (!paper || !pdfIndex || !pdfIndex.entries.length) return null;
  const isOwnedByOther = (fullPath) => {
    if (!fullPath || !ownersByPath) return false;
    const owner = ownersByPath.get(resolve(fullPath));
    return Boolean(owner && currentPaperKey && owner !== currentPaperKey);
  };
  const doi = paper.identifiers?.doi?.[0];
  if (doi) {
    const cleanDoi = normalizeDoi(doi);
    const doiAlnum = cleanDoi.replace(/[^a-z0-9]+/g, "");
    const doiTail = (cleanDoi.split("/").pop() || "").replace(/[^a-z0-9]+/g, "");
    for (const item of pdfIndex.entries) {
      if (isOwnedByOther(item.fullPath)) continue;
      if ((doiAlnum.length >= 8 && item.normAlnum.includes(doiAlnum)) ||
          (doiTail.length >= 7 && item.normAlnum.includes(doiTail))) {
        return item.fullPath;
      }
    }
  }
  const stem = makeSafeFileStem(paper.year, paper.title, "paper").toLowerCase();
  if (pdfIndex.byExactStem.has(stem)) {
    const exactHit = pdfIndex.byExactStem.get(stem);
    if (!isOwnedByOther(exactHit)) return exactHit;
  }
  if (paper.title && String(paper.title).trim().length >= 8) {
    const normFullTitle = String(paper.title)
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "");
    const normStemTitle = String(paper.title)
      .replace(/[\\/:*?"<>|\r\n\t]+/g, " ")
      .replace(/\s+/g, " ")
      .trim()
      .slice(0, 76)
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "");
    if (normFullTitle.length >= 8) {
      for (const item of pdfIndex.entries) {
        if (isOwnedByOther(item.fullPath)) continue;
        if (item.stemLower === stem || item.normAlnum.includes(normFullTitle) || (normStemTitle.length >= 12 && item.normAlnum.includes(normStemTitle))) {
          return item.fullPath;
        }
      }
    }
  }
  return null;
}

function resolveVerifiedLocalPdfForPaper(paper, state, pdfIndex = null, ownersByPath = null) {
  if (!paper) return { localPdf: null, candidatePdf: null };
  const key = paper.paper_key || computePaperKey(paper);
  const savedEv = state?.evidenceMap?.[key] || {};
  // 1. Priority: explicitly recorded paper_key -> pdfPath in state.evidenceMap
  if (savedEv.pdfPath && existsSync(savedEv.pdfPath)) {
    return { localPdf: savedEv.pdfPath, candidatePdf: savedEv.pdfPath };
  }
  const owners = ownersByPath || buildEvidencePdfOwnersMap(state);
  const candidate =
    (paper.local_pdf_path && existsSync(paper.local_pdf_path) && (!owners.has(resolve(paper.local_pdf_path)) || owners.get(resolve(paper.local_pdf_path)) === key)
      ? paper.local_pdf_path
      : null) ||
    matchLocalSavedPdfCandidate(paper, pdfIndex, key, owners);
  if (!candidate) {
    return { localPdf: null, candidatePdf: null };
  }
  // 2. Filename match or unrecorded path is only a candidate; require first-page identity verification before auto-associating
  const check = verifyLocalPdfIdentitySync(candidate, paper);
  if (check.matched) {
    return { localPdf: candidate, candidatePdf: candidate };
  }
  return { localPdf: null, candidatePdf: candidate };
}

function resolveCredential(panelVal, userConfigEnvName, sysEnvNames) {
  if (panelVal && String(panelVal).trim() && !/\$\{[^}]+\}/.test(String(panelVal))) {
    return { value: String(panelVal).trim(), source: "panel" };
  }
  const ucVal = readUserConfigFromEnv(userConfigEnvName);
  if (ucVal) {
    return { value: ucVal, source: "userConfig" };
  }
  for (const name of sysEnvNames) {
    const sysVal = resolveEnvPath(process.env[name]);
    if (sysVal) {
      return { value: sysVal, source: "systemEnv" };
    }
  }
  return { value: "", source: "none" };
}

function getEffectiveConfig() {
  const cfg = loadPanelConfig();
  const polite = resolveCredential(cfg.politeEmail, "USER_CONFIG_POLITE_EMAIL", [
    "POLITE_EMAIL",
    "OPENALEX_EMAIL",
    "CROSSREF_EMAIL",
    "UNPAYWALL_EMAIL"
  ]);
  const openalex = resolveCredential(cfg.openalexApiKey, "USER_CONFIG_OPENALEX_API_KEY", [
    "OPENALEX_API_KEY"
  ]);
  const s2 = resolveCredential(cfg.s2ApiKey, "USER_CONFIG_S2_API_KEY", [
    "S2_API_KEY",
    "SEMANTIC_SCHOLAR_API_KEY"
  ]);
  const exa = resolveCredential(cfg.exaApiKey, "USER_CONFIG_EXA_API_KEY", ["EXA_API_KEY"]);
  const serper = resolveCredential(cfg.serperApiKey, "USER_CONFIG_SERPER_API_KEY", [
    "SERPER_API_KEY"
  ]);
  const rawEasyScholar = resolveCredential(
    cfg.easyscholarSecretKey,
    "USER_CONFIG_EASYSCHOLAR_SECRET_KEY",
    ["EASYSCHOLAR_SECRET_KEY"]
  );
  const easyscholar = {
    value: rawEasyScholar.value ? rawEasyScholar.value.replace(/\s+/g, "").trim() : "",
    source: rawEasyScholar.value ? rawEasyScholar.source : "none"
  };
  const ai4scholar = resolveCredential(
    cfg.ai4scholarApiKey,
    "USER_CONFIG_AI4SCHOLAR_API_KEY",
    ["AI4SCHOLAR_API_KEY"]
  );
  let sciverse = resolveCredential(
    cfg.sciverseApiKey,
    "USER_CONFIG_SCIVERSE_API_KEY",
    ["SCIVERSE_API_KEY", "SCIVERSE_API_TOKEN"]
  );
  if (!sciverse.value && resolveEnvPath(process.env.SANMU_DISABLE_SCIVERSE_CRED_FILE) !== "1") {
    const credObj = readJsonSafe(join(homedir(), ".sciverse", "credentials.json"), null);
    if (credObj && typeof credObj.token === "string" && credObj.token.trim()) {
      sciverse = { value: credObj.token.trim(), source: "systemEnv" };
    }
  }
  const mineru = resolveCredential(
    cfg.mineruApiToken || cfg.mineruApiKey,
    "USER_CONFIG_MINERU_API_TOKEN",
    ["MINERU_API_TOKEN"]
  );
  let zoteroUrl = "http://127.0.0.1:23119";
  try {
    zoteroUrl = validateZoteroUrl(
      resolveCredential(cfg.zoteroUrl, "USER_CONFIG_ZOTERO_URL", []).value || "http://127.0.0.1:23119"
    );
  } catch {
    zoteroUrl = "http://127.0.0.1:23119";
  }
  const exportSubdir = resolveCredential(cfg.exportSubdir, "USER_CONFIG_EXPORT_SUBDIR", []).value || "00-收件箱-待整理/文献检索";
  const doc2mdSubdir = resolveCredential(cfg.doc2mdSubdir, "USER_CONFIG_DOC2MD_SUBDIR", []).value || "00-收件箱-待整理/文献解析";
  const doc2mdScriptsDir = resolveDoc2mdScriptsDir();
  const katexInfo = resolveKatexNodeModules(doc2mdScriptsDir);

  const childEnv = {
    ...process.env,
    ...Object.fromEntries(Object.entries(preparedRuntime?.env || {}).filter(([key]) => ["PATH", "VIRTUAL_ENV", "NODE_PATH", "PYTHONNOUSERSITE"].includes(key))),
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    EASYSCHOLAR_CACHE_PATH
  };
  if (katexInfo.nodeModulesDir) {
    childEnv.NODE_PATH = childEnv.NODE_PATH
      ? `${katexInfo.nodeModulesDir}${pathDelimiter}${childEnv.NODE_PATH}`
      : katexInfo.nodeModulesDir;
  }
  if (polite.value) {
    childEnv.POLITE_EMAIL = polite.value;
    for (const name of ["OPENALEX_EMAIL", "CROSSREF_EMAIL", "UNPAYWALL_EMAIL"]) {
      if (polite.source !== "systemEnv" || !childEnv[name]) childEnv[name] = polite.value;
    }
  }
  if (openalex.value) childEnv.OPENALEX_API_KEY = openalex.value;
  if (s2.value) {
    childEnv.S2_API_KEY = s2.value;
    childEnv.SEMANTIC_SCHOLAR_API_KEY = s2.value;
  }
  if (exa.value) childEnv.EXA_API_KEY = exa.value;
  if (serper.value) childEnv.SERPER_API_KEY = serper.value;
  if (easyscholar.value) childEnv.EASYSCHOLAR_SECRET_KEY = easyscholar.value;
  if (ai4scholar.value) {
    childEnv.AI4SCHOLAR_API_KEY = ai4scholar.value;
    if (!s2.value) childEnv.AI4SCHOLAR_S2_PROXY = "1";
  }
  if (sciverse.value) {
    childEnv.SCIVERSE_API_KEY = sciverse.value;
    childEnv.SCIVERSE_API_TOKEN = sciverse.value;
  }
  if (mineru.value) {
    childEnv.MINERU_API_TOKEN = mineru.value;
  }

  const pyRt = resolvePythonRuntime();

  return {
    childEnv,
    status: {
      python_runtime: { configured: pyRt.supported, source: pyRt.version ? `Python ${pyRt.version}` : "missing" },
      polite_email: {
        configured: Boolean(polite.value),
        source: polite.source,
        masked: maskCredentialValue(polite.value, true),
        portalUrl: API_PORTAL_URLS.polite_email
      },
      openalex_api_key: {
        configured: Boolean(openalex.value),
        source: openalex.source,
        masked: maskCredentialValue(openalex.value),
        portalUrl: API_PORTAL_URLS.openalex_api_key
      },
      semantic_scholar_api_key: {
        configured: Boolean(s2.value || ai4scholar.value),
        source: s2.value ? s2.source : ai4scholar.value ? `ai4scholar (${ai4scholar.source})` : "none",
        masked: maskCredentialValue(s2.value || ai4scholar.value),
        portalUrl: API_PORTAL_URLS.semantic_scholar_api_key
      },
      exa_api_key: {
        configured: Boolean(exa.value),
        source: exa.source,
        masked: maskCredentialValue(exa.value),
        portalUrl: API_PORTAL_URLS.exa_api_key
      },
      serper_api_key: {
        configured: Boolean(serper.value),
        source: serper.source,
        masked: maskCredentialValue(serper.value),
        portalUrl: API_PORTAL_URLS.serper_api_key
      },
      easyscholar_secret_key: {
        configured: Boolean(easyscholar.value),
        source: easyscholar.source,
        masked: maskCredentialValue(easyscholar.value),
        portalUrl: API_PORTAL_URLS.easyscholar_secret_key
      },
      ai4scholar_api_key: {
        configured: Boolean(ai4scholar.value),
        source: ai4scholar.source,
        masked: maskCredentialValue(ai4scholar.value),
        portalUrl: API_PORTAL_URLS.ai4scholar_api_key
      },
      sciverse_api_key: {
        configured: Boolean(sciverse.value),
        source: sciverse.source,
        masked: maskCredentialValue(sciverse.value),
        portalUrl: API_PORTAL_URLS.sciverse_api_key
      },
      mineru_api_token: {
        configured: Boolean(mineru.value),
        source: mineru.source,
        masked: maskCredentialValue(mineru.value),
        portalUrl: API_PORTAL_URLS.mineru_api_token
      },
      katex_validator: { configured: Boolean(katexInfo.nodeModulesDir), source: katexInfo.nodeModulesDir ? "local" : "missing" },
      europepmc_public_access: { configured: true, source: "public" }
    },
    settings: {
      politeEmail: cfg.politeEmail || "",
      politeEmailConfigured: Boolean(polite.value),
      politeEmailSource: polite.source,
      apiPortalUrls: API_PORTAL_URLS,
      zoteroUrl,
      exportSubdir,
      resolvedExportDir: isAbsolutePath(exportSubdir)
        ? resolve(exportSubdir)
        : resolve(WORKSPACE_ROOT, exportSubdir.replace(/^[/\\]+/, "")),
      doc2mdSubdir,
      resolvedDoc2mdDir: isAbsolutePath(doc2mdSubdir)
        ? resolve(doc2mdSubdir)
        : resolve(WORKSPACE_ROOT, doc2mdSubdir.replace(/^[/\\]+/, "")),
      defaultSources: cfg.defaultSources || ["openalex", "semantic-scholar", "crossref"],
      defaultLimit: cfg.defaultLimit || 10,
      runsDir: RUNS_DIR,
      doc2mdJobsDir: DOC2MD_JOBS_DIR,
      workspaceRoot: WORKSPACE_ROOT
    }
  };
}

function runPython(scriptPath, args, timeoutMs = 180000) {
  const { childEnv } = getEffectiveConfig();
  const pyRt = resolvePythonRuntime();
  if (!pyRt.supported) {
    return Promise.resolve({
      exitCode: 3,
      stdout: "",
      stderr: "未检测到 Python 3.10+ 运行环境，请先在本机安装 Python 3.10 或更高版本。",
      parsed: {
        status: "failed",
        error: {
          kind: "python_missing",
          message: "未检测到 Python 3.10+ 运行环境，请先在本机安装 Python 3.10 或更高版本。"
        }
      }
    });
  }
  return new Promise((resolvePromise) => {
    const child = spawn(pyRt.cmd, [...pyRt.prefixArgs, scriptPath, ...args], {
      cwd: PLUGIN_ROOT,
      env: childEnv,
      stdio: ["ignore", "pipe", "pipe"],
      windowsHide: true
    });
    let stdout = "";
    let stderr = "";
    let timedOut = false;
    const timer = setTimeout(() => {
      timedOut = true;
      child.kill();
    }, timeoutMs);
    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
    });
    child.on("error", (err) => {
      clearTimeout(timer);
      resolvePromise({
        exitCode: 3,
        stdout: "",
        stderr: err.message,
        parsed: { status: "failed", error: { kind: "spawn", message: err.message } }
      });
    });
    child.on("close", (code) => {
      clearTimeout(timer);
      let parsed = null;
      const trimmed = stdout.trim();
      if (trimmed) {
        try {
          parsed = JSON.parse(trimmed);
        } catch {
          parsed = { rawText: trimmed };
        }
      }
      resolvePromise(redactSecrets({
        exitCode: timedOut ? 124 : code ?? 3,
        stdout: trimmed,
        stderr: timedOut ? "Python 操作已超时，结果可能不完整，请重试。" : stderr.trim(),
        parsed: timedOut ? { status: "failed", error: { kind: "timeout", message: "Python 操作已超时，结果可能不完整，请重试。" } } : parsed,
        timedOut
      }));
    });
  });
}

function pythonFailure(pyRes, fallback) {
  const detail = pyRes.parsed?.error;
  return typeof detail === "string" ? detail : detail?.message || pyRes.stderr || fallback;
}

function requirePythonBundle(pyRes, outDir, fallback) {
  const run = readJsonSafe(join(outDir, "run.json"), null);
  if (!run || ![0, 1].includes(pyRes.exitCode) || pyRes.parsed?.status === "failed" || run.status === "failed") {
    const sourceErrors = Object.entries(run?.source_status || {}).filter(([, item]) => item?.status === "error")
      .map(([name, item]) => `${name}: ${typeof item.error === "string" ? item.error : item.error?.message || "failed"}`).join("; ");
    const error = new Error(pythonFailure(pyRes, sourceErrors || fallback));
    error.runId = basename(outDir);
    error.exitCode = pyRes.exitCode;
    throw error;
  }
  return run;
}

function makeRunId(prefix) {
  const now = new Date();
  const stamp = now.toISOString().replace(/[:.]/g, "-").slice(0, 19);
  const rand = Math.random().toString(36).slice(2, 6);
  const cleanPrefix = String(prefix || "run")
    .toLowerCase()
    .replace(/[^a-z0-9_-]+/g, "-")
    .replace(/^-|-$/g, "")
    .slice(0, 24);
  return `${cleanPrefix || "run"}-${stamp}-${rand}`;
}

function formatAffiliationItem(aff) {
  if (!aff) return "";
  if (typeof aff === "string") return aff.trim();
  if (typeof aff === "object") {
    const name = aff.display_name || aff.name || aff.institution || "";
    const country = aff.country_code ? ` (${aff.country_code})` : "";
    return name ? `${name}${country}` : "";
  }
  return String(aff);
}

function normalizeAffiliationsList(affiliations) {
  if (!Array.isArray(affiliations)) return [];
  const uniqueList = [...new Set(affiliations.map(formatAffiliationItem).filter(Boolean))];
  if (uniqueList.length <= 1) return uniqueList;
  const shortInsts = uniqueList
    .filter((s) => s.length <= 68)
    .map((s) => s.replace(/\s*\([A-Z]{2}\)\s*$/, "").trim().toLowerCase())
    .filter((s) => s.length >= 6);
  if (!shortInsts.length) return uniqueList;
  return uniqueList.filter((item) => {
    if (item.length <= 75) return true;
    const normItem = item.replace(/[\u2010-\u2015]/g, "-").toLowerCase();
    return !shortInsts.some((inst) => normItem.includes(inst));
  });
}

function normalizeDoi(value) {
  return String(value || "")
    .trim()
    .replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "")
    .replace(/[.,;:)\]>]+$/, "")
    .replace(/\/(?:full|pdf|epdf|pdfdirect|abs|abstract|html|meta|suppl|figures|references|citations)(?:[/?#].*)?$/i, "")
    .replace(/^(10\.1021\/[^/\s]+)\/\d{5,}$/i, "$1")
    .toLowerCase();
}

function samePaperIdentity(target, candidate) {
  const a = target?.identifiers || {};
  const b = candidate?.identifiers || {};
  const aDois = (a.doi || []).map(normalizeDoi);
  const bDois = (b.doi || []).map(normalizeDoi);
  if (aDois.length && bDois.length) return aDois.some((id) => bDois.includes(id));
  for (const ns of ["openalex", "semantic_scholar", "pmid", "pmcid", "arxiv", "sciverse", "sciverse_doc", "sciverse_schema"]) {
    const normalized = (values) => (values || []).map((v) => String(v).toLowerCase().replace(/^https?:\/\/openalex\.org\//, ""));
    if (normalized(a[ns]).some((id) => normalized(b[ns]).includes(id))) return true;
  }
  const titleKey = (s) => String(s || "").normalize("NFKC").toLowerCase().replace(/[^\p{L}\p{N}]/gu, "");
  const title = titleKey(target?.title);
  return Boolean(title && title === titleKey(candidate?.title) && (!target.year || !candidate.year || target.year === candidate.year));
}

function computePaperKey(paper) {
  const ids = paper?.identifiers || {};
  if (ids.doi?.[0]) return `doi:${normalizeDoi(ids.doi[0])}`;
  if (ids.openalex?.[0]) return `openalex:${String(ids.openalex[0]).replace(/^https?:\/\/openalex\.org\//i, "")}`;
  if (ids.semantic_scholar?.[0]) return `s2:${ids.semantic_scholar[0]}`;
  if (ids.pmcid?.[0]) return `pmcid:${ids.pmcid[0]}`;
  if (ids.pmid?.[0]) return `pmid:${ids.pmid[0]}`;
  if (ids.arxiv?.[0]) return `arxiv:${ids.arxiv[0]}`;
  if (ids.sciverse?.[0]) return `sciverse:${ids.sciverse[0]}`;
  if (ids.sciverse_schema?.[0]) return `sciverse_schema:${ids.sciverse_schema[0]}`;
  if (ids.sciverse_doc?.[0]) return `sciverse_doc:${ids.sciverse_doc[0]}`;
  if (ids.patent?.[0]) return `patent:${ids.patent[0]}`;
  const normTitle = String(paper?.title || "")
    .toLowerCase()
    .replace(/[^\w\u4e00-\u9fa5]+/g, "")
    .slice(0, 64);
  return `title:${normTitle || "unknown"}-${paper?.year || "na"}`;
}

function computeResolvableSeedId(paper) {
  const ids = paper?.identifiers || {};
  if (ids.doi?.[0]) return String(ids.doi[0]);
  if (ids.openalex?.[0]) return `openalex:${String(ids.openalex[0]).replace(/^https?:\/\/openalex\.org\//i, "")}`;
  if (ids.semantic_scholar?.[0]) return `s2:${ids.semantic_scholar[0]}`;
  if (ids.pmid?.[0]) return `pmid:${ids.pmid[0]}`;
  if (ids.pmcid?.[0]) return `pmcid:${ids.pmcid[0]}`;
  if (ids.arxiv?.[0]) return `arxiv:${ids.arxiv[0]}`;
  if (ids.corpus_id?.[0]) return `corpusid:${ids.corpus_id[0]}`;
  if (ids.sciverse?.[0] && String(ids.sciverse[0]).toLowerCase().startsWith("paper:10.")) {
    return String(ids.sciverse[0]).slice(6);
  }
  return null;
}

function pickCompatibleSources(idStr, allowCrossref = false) {
  const s = String(idStr || "").trim();
  const low = s.toLowerCase();
  if (low.startsWith("openalex:") || /^w\d+$/i.test(s) || low.startsWith("pmcid:") || /^pmc\d+$/i.test(s)) {
    return ["openalex"];
  }
  if (
    low.startsWith("s2:") ||
    low.startsWith("semantic-scholar:") ||
    low.startsWith("arxiv:") ||
    low.startsWith("corpusid:")
  ) {
    return ["semantic-scholar"];
  }
  if (low.startsWith("pmid:")) {
    return ["openalex", "semantic-scholar"];
  }
  return allowCrossref ? ["openalex", "semantic-scholar", "crossref"] : ["openalex", "semantic-scholar"];
}

function isMachineTdmUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return false;
  const u = rawUrl.trim().toLowerCase();
  return (
    u.includes("api.elsevier.com/content/article/") ||
    u.includes("api.wiley.com/onlinelibrary/tdm/") ||
    u.includes("syndication.highwire.org/") ||
    u.includes("harvest.aps.org/") ||
    u.includes("httpaccept=text/xml") ||
    u.includes("httpaccept=text/plain")
  );
}

function isLikelyLandingPageUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return true;
  const u = rawUrl.trim().toLowerCase();
  if (!u.startsWith("https://") && !u.startsWith("http://arxiv.org/")) return true;
  if (isMachineTdmUrl(u)) return true;
  if (
    /^https?:\/\/(dx\.)?doi\.org\//.test(u) ||
    u.includes("pubmed.ncbi.nlm.nih.gov/") ||
    u.includes("doaj.org/article/") ||
    u.includes("semanticscholar.org/paper/") ||
    u.includes("openalex.org/w") ||
    u.includes("europepmc.org/article/") ||
    u.includes("figshare.com/articles/") ||
    u.includes("hal.science/hal-") && !u.endsWith("/document") && !u.endsWith(".pdf") ||
    /ncbi\.nlm\.nih\.gov\/pmc\/articles\/pmc\d+\/?(\?.*)?$/.test(u) ||
    u.includes("link.aps.org/article/") ||
    u.includes("dl.acm.org/doi/10.") && !u.includes("/doi/pdf/")
  ) {
    return true;
  }
  return false;
}

function isLikelyDirectPdfUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return false;
  const u = rawUrl.trim();
  const low = u.toLowerCase();
  if (isLikelyLandingPageUrl(u)) return false;
  if (/^https?:\/\/arxiv\.org\/(pdf|abs)\//i.test(u)) return true;
  if (/\.pdf([?#].*)?$/i.test(low)) return true;
  if (/\/(?:pdf|epdf|pdfdirect|articlepdf|article-pdf)(?:[/?#]|$)/i.test(low)) return true;
  if (
    low.includes("/bitstreams/") ||
    low.includes("delivery.cfm") ||
    low.includes("blobtype=pdf") ||
    low.includes("pdf=render")
  ) {
    return true;
  }
  return false;
}

function normalizeDirectPdfUrl(rawUrl) {
  const u = String(rawUrl || "").trim();
  const mAbs = u.match(/^https?:\/\/arxiv\.org\/abs\/([^\s?#]+)/i);
  if (mAbs) {
    return `https://arxiv.org/pdf/${mAbs[1].replace(/\.pdf$/i, "")}.pdf`;
  }
  const mPdf = u.match(/^https?:\/\/arxiv\.org\/pdf\/([^\s?#]+)/i);
  if (mPdf) {
    const clean = mPdf[1].replace(/\.pdf$/i, "");
    return `https://arxiv.org/pdf/${clean}.pdf`;
  }
  return u;
}

function extractDownloadablePdfUrls(paper) {
  const candidates = [];
  const seen = new Set();
  const pushUrl = (raw) => {
    if (!raw || !isLikelyDirectPdfUrl(raw)) return;
    const norm = normalizeDirectPdfUrl(raw);
    if (!norm.startsWith("https://")) return;
    if (seen.has(norm)) return;
    seen.add(norm);
    candidates.push(norm);
  };

  // 1. Prioritize arXiv direct PDFs (fast, reliable, no Cloudflare challenge)
  const ids = paper?.identifiers || {};
  for (const arx of ids.arxiv || []) {
    pushUrl(`https://arxiv.org/pdf/${String(arx).replace(/\.pdf$/i, "")}.pdf`);
  }

  // 2. Check oa_locations that are repository / arXiv bitstreams first
  for (const loc of paper?.oa_locations || []) {
    if (loc?.url && /arxiv\.org|dspace\.|osti\.gov|hal\.science.*\/document/i.test(loc.url)) {
      pushUrl(loc.url);
    }
  }

  // 3. Explicit pdfUrl and remaining oa_locations
  if (paper?.pdfUrl) pushUrl(paper.pdfUrl);
  for (const loc of paper?.oa_locations || []) {
    if (loc?.url) pushUrl(loc.url);
  }

  return candidates;
}

function unpackJournalAndTldr(paper) {
  let journalStr = "";
  let journalInfo = paper?.journal_info || null;
  if (paper?.journal && typeof paper.journal === "object") {
    journalStr = String(paper.journal.name || paper.venue || "").trim();
    journalInfo = paper.journal;
  } else if (typeof paper?.journal === "string") {
    journalStr = paper.journal.trim();
  }
  const venueStr =
    typeof paper?.venue === "string"
      ? paper.venue.trim()
      : paper?.venue && typeof paper.venue === "object"
        ? String(paper.venue.name || "").trim()
        : "";
  const finalJournal = journalStr || venueStr || "";

  let tldrStr = null;
  if (paper?.tldr && typeof paper.tldr === "object") {
    tldrStr = String(paper.tldr.text || "").trim() || null;
  } else if (typeof paper?.tldr === "string") {
    tldrStr = paper.tldr.trim() || null;
  }

  return {
    journal: finalJournal,
    venue: venueStr || finalJournal,
    journal_info: journalInfo,
    tldr: tldrStr
  };
}

function canonicalLinkDedupKey(rawUrl) {
  const s = String(rawUrl || "").trim();
  if (!s) return "";
  const httpsUrl = s.replace(/^http:\/\//i, "https://").replace(/^https:\/\/dx\.doi\.org\//i, "https://doi.org/");
  const mPmc = httpsUrl.match(/^https:\/\/(?:www\.|pmc\.)?ncbi\.nlm\.nih\.gov\/pmc\/articles\/(?:PMC)?(\d+)\/?(?:\?tool=ebi)?$/i) ||
               httpsUrl.match(/^https:\/\/europepmc\.org\/articles?\/(?:pmc\/)?(?:PMC)?(\d+)\/?$/i);
  if (mPmc) return `pmc-page:${mPmc[1]}`;
  return httpsUrl.replace(/\/+$/, "").toLowerCase();
}

function describeLocationLinkLabel(url, provider, isPdf, licenseStatus) {
  const low = String(url || "").toLowerCase();
  let hostTag = provider || "web";
  if (low.includes("arxiv.org")) hostTag = "arXiv";
  else if (low.includes("pmc.ncbi.nlm.nih.gov") || low.includes("ncbi.nlm.nih.gov/pmc")) hostTag = "PMC";
  else if (low.includes("europepmc.org")) hostTag = "Europe PMC";
  else if (low.includes("mdpi.com")) hostTag = "MDPI";
  else if (low.includes("frontiersin.org")) hostTag = "Frontiers";
  else if (low.includes("nature.com")) hostTag = "Nature";
  else if (low.includes("springer.com")) hostTag = "Springer";
  else if (low.includes("wiley.com")) hostTag = "Wiley";
  else if (low.includes("acs.org")) hostTag = "ACS";
  else if (low.includes("rsc.org")) hostTag = "RSC";
  else if (low.includes("sciencedirect.com") || low.includes("elsevier.com")) hostTag = "Elsevier";
  else if (low.includes("figshare.com")) hostTag = "Figshare";
  else if (low.includes("zenodo.org")) hostTag = "Zenodo";
  else if (low.includes("osti.gov")) hostTag = "OSTI";
  else if (low.includes("hal.science")) hostTag = "HAL";
  else if (low.includes("doaj.org")) hostTag = "DOAJ";

  if (isPdf) return `OA PDF [${hostTag}]`;
  if (licenseStatus === "open") return `OA 网页 [${hostTag}]`;
  return `来源页 [${hostTag}]`;
}

function buildClickableLinks(paper) {
  const links = [];
  const seen = new Set();
  const add = (label, url, kind, isDirectPdf = false) => {
    if (!url || typeof url !== "string" || !url.startsWith("http")) return;
    if (isMachineTdmUrl(url)) return;
    const dedupKey = canonicalLinkDedupKey(url);
    if (!dedupKey || seen.has(dedupKey)) return;
    seen.add(dedupKey);
    links.push({ label, url, kind, isDirectPdf });
  };

  const ids = paper?.identifiers || {};
  for (const d of ids.doi || []) {
    add(`DOI: ${d}`, `https://doi.org/${d}`, "doi", false);
  }
  for (const oa of ids.openalex || []) {
    const cleanOa = String(oa).replace(/^https?:\/\/openalex\.org\//i, "");
    add(`OpenAlex: ${cleanOa}`, `https://openalex.org/${cleanOa}`, "openalex", false);
  }
  for (const s2 of ids.semantic_scholar || []) {
    add(`Semantic Scholar`, `https://www.semanticscholar.org/paper/${s2}`, "s2", false);
  }
  for (const pmid of ids.pmid || []) {
    add(`PubMed: ${pmid}`, `https://pubmed.ncbi.nlm.nih.gov/${pmid}/`, "pmid", false);
  }
  for (const pmcid of ids.pmcid || []) {
    const cleanPmc = String(pmcid).toUpperCase().startsWith("PMC") ? String(pmcid).toUpperCase() : `PMC${pmcid}`;
    add(`Europe PMC: ${cleanPmc}`, `https://europepmc.org/article/PMC/${cleanPmc.replace(/^PMC/i, "")}`, "pmcid", false);
  }
  for (const arx of ids.arxiv || []) {
    const cleanArx = String(arx).replace(/\.pdf$/i, "");
    add(`arXiv 摘要: ${cleanArx}`, `https://arxiv.org/abs/${cleanArx}`, "arxiv", false);
    add(`arXiv PDF: ${cleanArx}`, `https://arxiv.org/pdf/${cleanArx}.pdf`, "pdf", true);
  }
  if (paper?.url) {
    add("原文/来源页", paper.url, "source", isLikelyDirectPdfUrl(paper.url));
  }
  if (paper?.pdfUrl) {
    add("PDF 直链", normalizeDirectPdfUrl(paper.pdfUrl), "pdf", isLikelyDirectPdfUrl(paper.pdfUrl));
  }
  for (const loc of paper?.oa_locations || []) {
    if (loc?.url && !isMachineTdmUrl(loc.url)) {
      const isPdf = isLikelyDirectPdfUrl(loc.url);
      const normUrl = isPdf ? normalizeDirectPdfUrl(loc.url) : loc.url;
      const tag = describeLocationLinkLabel(normUrl, loc.provider, isPdf, loc.license_status);
      add(tag, normUrl, isPdf ? "pdf" : "oa_page", isPdf);
    }
  }
  return links;
}

async function fetchZoteroCollectionsMap(baseUrl) {
  const colMap = new Map();
  const collections = [];
  try {
    const resp = await fetch(`${baseUrl}/api/users/0/collections?limit=100`, {
      headers: { "Zotero-Allowed-Request": "1" },
      signal: AbortSignal.timeout(1500)
    });
    if (!resp.ok) return { colMap, collections };
    const raw = await resp.json();
    if (!Array.isArray(raw)) return { colMap, collections };
    const byKey = new Map();
    for (const c of raw) {
      if (c?.key && c?.data?.name) {
        byKey.set(c.key, {
          key: c.key,
          name: c.data.name,
          parentCollection: c.data.parentCollection || null
        });
      }
    }
    for (const [key, item] of byKey.entries()) {
      let label = item.name;
      if (item.parentCollection && byKey.has(item.parentCollection)) {
        label = `${byKey.get(item.parentCollection).name} / ${item.name}`;
      }
      colMap.set(key, label);
      collections.push({ key, name: item.name, fullName: label, parentKey: item.parentCollection });
    }
    collections.sort((a, b) => a.fullName.localeCompare(b.fullName, "zh-CN"));
  } catch {
    // Ignore collection list timeout
  }
  return { colMap, collections };
}

const ZOTERO_CACHE_TTL_MS = 20000;
const zoteroStatusCache = {
  checkedAt: 0,
  baseUrl: "",
  zoteroOnline: false,
  serverId: null,
  colMap: new Map(),
  collections: []
};
const zoteroPaperMatchCache = new Map();

function invalidateZoteroProbeCache(paperKey = null) {
  if (paperKey) {
    for (const k of zoteroPaperMatchCache.keys()) {
      if (k.endsWith(`::${paperKey}`)) zoteroPaperMatchCache.delete(k);
    }
  } else {
    zoteroPaperMatchCache.clear();
    zoteroStatusCache.checkedAt = 0;
  }
}

async function probeLocalZotero(papers, forceRefresh = false) {
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");
  const nowMs = Date.now();
  let zoteroOnline = false;
  let serverId = null;
  let colMap = new Map();
  let collections = [];

  try {
    const pingResp = await fetch(`${baseUrl}/api/`, {
      method: "GET",
      headers: { "Zotero-Allowed-Request": "1" },
      signal: AbortSignal.timeout(1200)
    });
    serverId = pingResp.headers.get("zotero-server-id") || null;
    zoteroOnline = pingResp.ok && Boolean(serverId || pingResp.headers.get("zotero-api-version"));
  } catch {
    zoteroOnline = false;
  }

  if (!zoteroOnline) {
    zoteroPaperMatchCache.clear();
    zoteroStatusCache.checkedAt = nowMs;
    zoteroStatusCache.baseUrl = baseUrl;
    zoteroStatusCache.zoteroOnline = false;
    zoteroStatusCache.serverId = null;
    zoteroStatusCache.colMap = new Map();
    zoteroStatusCache.collections = [];
    return { zoteroOnline: false, serverId: null, collections: [], matches: {} };
  }

  if (
    !forceRefresh &&
    zoteroStatusCache.zoteroOnline &&
    zoteroStatusCache.baseUrl === baseUrl &&
    zoteroStatusCache.serverId === serverId &&
    nowMs - zoteroStatusCache.checkedAt < ZOTERO_CACHE_TTL_MS
  ) {
    colMap = zoteroStatusCache.colMap;
    collections = zoteroStatusCache.collections;
  } else {
    const colRes = await fetchZoteroCollectionsMap(baseUrl);
    colMap = colRes.colMap;
    collections = colRes.collections;
    zoteroStatusCache.checkedAt = Date.now();
    zoteroStatusCache.baseUrl = baseUrl;
    zoteroStatusCache.zoteroOnline = true;
    zoteroStatusCache.serverId = serverId;
    zoteroStatusCache.colMap = colMap;
    zoteroStatusCache.collections = collections;
  }

  if (!Array.isArray(papers) || papers.length === 0) {
    return { zoteroOnline, serverId, collections, matches: {} };
  }

  const norm = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
  const matches = {};
  const seenKeys = new Set();
  const uncachedTasks = [];

  for (const paper of papers.slice(0, 30)) {
    if (!paper) continue;
    const key = paper.paper_key || computePaperKey(paper);
    if (seenKeys.has(key)) continue;
    seenKeys.add(key);
    const cacheKey = `${baseUrl}::${key}`;
    if (!forceRefresh) {
      const cached = zoteroPaperMatchCache.get(cacheKey);
      if (cached && Date.now() - cached.checkedAt < ZOTERO_CACHE_TTL_MS) {
        if (cached.matchedObj) matches[key] = cached.matchedObj;
        continue;
      }
    }
    uncachedTasks.push({ paper, key, cacheKey });
  }

  const probeSinglePaper = async ({ paper, key, cacheKey }) => {
    const doi = paper?.identifiers?.doi?.[0];
    const cleanDoi = normalizeDoi(doi);
    const targetTitle = norm(paper.title);
    const queryCandidates = [];
    if (cleanDoi) {
      queryCandidates.push({ q: cleanDoi, qmode: "everything" });
    }
    if (paper?.title && String(paper.title).trim().length >= 6) {
      queryCandidates.push({ q: String(paper.title).trim().slice(0, 80), qmode: "titleCreatorYear" });
    }
    if (!queryCandidates.length) {
      zoteroPaperMatchCache.set(cacheKey, { checkedAt: Date.now(), matchedObj: null });
      return;
    }

    let matchedObj = null;
    for (const qc of queryCandidates) {
      try {
        const url = `${baseUrl}/api/users/0/items?q=${encodeURIComponent(qc.q)}&qmode=${qc.qmode}&limit=8`;
        const resp = await fetch(url, {
          headers: { "Zotero-Allowed-Request": "1" },
          signal: AbortSignal.timeout(1400)
        });
        if (!resp.ok) continue;
        const items = await resp.json();
        if (!Array.isArray(items) || items.length === 0) continue;
        for (const item of items) {
          const data = item?.data || {};
          if (data.itemType === "attachment" || data.itemType === "note" || data.itemType === "annotation") continue;
          const itemDoi = normalizeDoi(data.DOI);
          const itemTitle = norm(data.title);
          const doiMatched = Boolean(cleanDoi && itemDoi && itemDoi === cleanDoi);
          const noDoiConflict = !(cleanDoi && itemDoi && cleanDoi !== itemDoi);
          const year = String(paper.year || "");
          const itemYear = String(data.date || "").match(/\b\d{4}\b/)?.[0] || "";
          const typeMatches = !(["preprint", "posted-content"].includes(paper.type) && data.itemType !== "preprint");
          const titleMatched = Boolean(noDoiConflict && targetTitle && itemTitle === targetTitle && typeMatches &&
            (!year || !itemYear || year === itemYear));
          if (doiMatched || titleMatched) {
            const colNames = (data.collections || []).map((ck) => colMap.get(ck) || ck).filter(Boolean);
            const tags = (data.tags || []).map((t) => t?.tag).filter(Boolean);
            matchedObj = {
              matched: true,
              itemKey: item.key,
              title: data.title,
              itemType: data.itemType,
              date: data.date,
              publicationTitle: data.publicationTitle || "",
              collections: colNames,
              tags,
              selectUri: `zotero://select/library/items/${item.key}`
            };
            break;
          }
        }
        if (matchedObj) break;
      } catch {
        // Ignore per-item Zotero timeout
      }
    }
    zoteroPaperMatchCache.set(cacheKey, { checkedAt: Date.now(), matchedObj });
    if (matchedObj) {
      matches[key] = matchedObj;
    }
  };

  const CONCURRENCY = 5;
  for (let i = 0; i < uncachedTasks.length; i += CONCURRENCY) {
    await Promise.all(uncachedTasks.slice(i, i + CONCURRENCY).map(probeSinglePaper));
  }
  return { zoteroOnline, serverId, collections, matches };
}

async function getOrAuthorizeZoteroWriteKey(baseUrl, serverId) {
  if (!serverId) {
    const r = await fetch(`${baseUrl}/api/`, {
      headers: { "Zotero-Allowed-Request": "1" },
      signal: AbortSignal.timeout(2000)
    });
    serverId = r.headers.get("zotero-server-id");
  }
  if (!serverId) {
    throw new Error("未检测到本机 Zotero 8+ 本地服务（请确认 Zotero 已启动且开启本地通信）");
  }

  // 1. Check our plugin store and official Zotero plugin store
  for (const keyFile of [ZOTERO_KEYS_PATH, OFFICIAL_ZOTERO_KEYS_PATH]) {
    const stored = readJsonSafe(keyFile, {});
    if (stored && typeof stored[serverId] === "string" && stored[serverId].length >= 12) {
      return { key: stored[serverId], serverId };
    }
  }

  // 2. Request authorization from Zotero Desktop (pops up Allow / Always Allow in Zotero)
  const authResp = await fetch(`${baseUrl}/api/local/authorize`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Zotero-Allowed-Request": "1",
      "Zotero-Server-ID": serverId
    },
    body: JSON.stringify({ appName: "Sanmu Paper Search (三木文献检索)" }),
    signal: AbortSignal.timeout(60000)
  });

  if (authResp.status === 403) {
    throw new Error("你在 Zotero 弹窗中拒绝了写入授权。");
  }
  if (!authResp.ok) {
    throw new Error(`Zotero 本地写入授权未通过 (HTTP ${authResp.status})，请确认使用 Zotero 8+ 并在弹窗中点击「允许/始终允许」。`);
  }
  const body = await authResp.json();
  if (!body?.key) {
    throw new Error("Zotero 未返回有效写入密钥。");
  }
  if (body.remember) {
    const current = readJsonSafe(ZOTERO_KEYS_PATH, {});
    current[serverId] = body.key;
    writeJsonSafe(ZOTERO_KEYS_PATH, current);
  }
  return { key: body.key, serverId };
}

function normalizeUserTags(tags) {
  if (Array.isArray(tags)) {
    return [...new Set(tags.map((t) => String(t || "").trim()).filter(Boolean))].slice(0, 30);
  }
  if (typeof tags === "string") {
    return [...new Set(tags.split(/[,，;；]+/).map((t) => t.trim()).filter(Boolean))].slice(0, 30);
  }
  return [];
}

function setPaperNoteAndTags(state, paperKey, userTags, userNote, paperObj = null) {
  const key = String(paperKey || paperObj?.paper_key || (paperObj ? computePaperKey(paperObj) : "")).trim();
  if (!key) throw new Error("未指定要保存批注/标签的论文键值 (paperKey)。");
  const prev = state.evidenceMap[key] || {};
  const nextTags = userTags !== undefined ? normalizeUserTags(userTags) : prev.user_tags;
  const nextNote = userNote !== undefined ? String(userNote ?? "").trim() : prev.user_note;
  state.evidenceMap[key] = {
    ...prev,
    ...(nextTags !== undefined ? { user_tags: nextTags } : {}),
    ...(nextNote !== undefined ? { user_note: nextNote } : {}),
    updatedAt: new Date().toISOString()
  };
  if (Array.isArray(state.basket)) {
    state.basket = state.basket.map((bp) =>
      (bp.paper_key || computePaperKey(bp)) === key
        ? {
            ...bp,
            ...(nextTags !== undefined ? { user_tags: nextTags } : {}),
            ...(nextNote !== undefined ? { user_note: nextNote } : {})
          }
        : bp
    );
  }
  return { paperKey: key, user_tags: nextTags || [], user_note: nextNote || "" };
}

async function savePaperToLocalZotero(paper, collectionKey = "") {
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");

  const pKey = paper.paper_key || computePaperKey(paper);
  invalidateZoteroProbeCache(pKey);
  // First check if already in Zotero
  const probe = await probeLocalZotero([paper], false);
  if (!probe.zoteroOnline) {
    throw new Error("未检测到本机 Zotero 桌面端在线，请先启动 Zotero。");
  }
  if (probe.matches[pKey]?.itemKey) {
    return {
      alreadyExists: true,
      itemKey: probe.matches[pKey].itemKey,
      selectUri: probe.matches[pKey].selectUri,
      collections: probe.matches[pKey].collections || []
    };
  }

  const { key: apiKey, serverId } = await getOrAuthorizeZoteroWriteKey(baseUrl, probe.serverId);
  const unpacked = unpackJournalAndTldr(paper);
  const doi = paper?.identifiers?.doi?.[0] || "";
  const creators = (paper?.authors || [])
    .map((a) => {
      const full = String(a?.name || "").trim();
      if (!full) return null;
      const parts = full.split(/\s+/);
      if (parts.length >= 2) {
        return {
          creatorType: "author",
          firstName: parts.slice(0, -1).join(" "),
          lastName: parts[parts.length - 1]
        };
      }
      return { creatorType: "author", name: full };
    })
    .filter(Boolean);

  const curState = loadState();
  const savedEv = curState.evidenceMap?.[pKey] || {};
  const userTags = normalizeUserTags(
    Array.isArray(paper?.user_tags) && paper.user_tags.length
      ? paper.user_tags
      : savedEv.user_tags || []
  );
  const userNote =
    typeof paper?.user_note === "string" && paper.user_note.trim()
      ? paper.user_note.trim()
      : typeof savedEv.user_note === "string" && savedEv.user_note.trim()
        ? savedEv.user_note.trim()
        : "";

  const rankSummary = paper?.venue_rank?.summary ? String(paper.venue_rank.summary).trim() : "";
  const extraTags = [{ tag: "sanmu-paper-search" }];
  if (paper?.venue_rank?.jcr) extraTags.push({ tag: `JCR:${paper.venue_rank.jcr}` });
  if (paper?.venue_rank?.cas_up) extraTags.push({ tag: paper.venue_rank.cas_up });
  if (paper?.venue_rank?.ccf) extraTags.push({ tag: `CCF-${paper.venue_rank.ccf}` });
  for (const ut of userTags) {
    if (!extraTags.some((et) => et.tag === ut)) {
      extraTags.push({ tag: ut });
    }
  }

  const itemPayload = {
    itemType: paper?.type === "preprint" || paper?.type === "posted-content" ? "preprint" : "journalArticle",
    title: paper?.title || "Untitled",
    creators,
    date: paper?.publication_date || (paper?.year ? String(paper.year) : ""),
    publicationTitle: unpacked.journal || "",
    ...(unpacked.journal_info?.volume ? { volume: String(unpacked.journal_info.volume) } : {}),
    ...(unpacked.journal_info?.pages ? { pages: String(unpacked.journal_info.pages) } : {}),
    ...(doi ? { DOI: doi } : {}),
    url: doi ? `https://doi.org/${doi}` : paper?.url || "",
    abstractNote: paper?.abstract || unpacked.tldr || "",
    ...(rankSummary ? { extra: rankSummary } : {}),
    collections: collectionKey ? [collectionKey] : [],
    tags: extraTags
  };

  const createResp = await fetch(`${baseUrl}/api/users/0/items`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      "Zotero-Allowed-Request": "1",
      "Zotero-API-Key": apiKey,
      "Zotero-Server-ID": serverId
    },
    body: JSON.stringify([itemPayload]),
    signal: AbortSignal.timeout(15000)
  });

  if (!createResp.ok) {
    const errTxt = await createResp.text().catch(() => "");
    throw new Error(`写入 Zotero 失败 (HTTP ${createResp.status}): ${errTxt.slice(0, 200)}`);
  }

  const createJson = await createResp.json();
  const createdKey =
    createJson?.successful?.["0"]?.key ||
    createJson?.success?.["0"] ||
    createJson?.unchanged?.["0"] ||
    null;
  if (!createdKey) {
    throw new Error("Zotero 未返回新建条目的 Key。");
  }

  // If paper already has a recorded or identity-verified local PDF on disk, attach it to the new Zotero item!
  let pdfAttached = false;
  let attachmentKey = null;
  const { localPdf: localPdfPath } = resolveVerifiedLocalPdfForPaper(
    paper,
    curState,
    scanSaveDirPdfIndex(),
    buildEvidencePdfOwnersMap(curState)
  );
  if (localPdfPath && existsSync(localPdfPath)) {
    try {
      const pdfBytes = readFileSync(localPdfPath);
      const filename = basename(localPdfPath);
      const attResp = await fetch(`${baseUrl}/api/users/0/items`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Zotero-Allowed-Request": "1",
          "Zotero-API-Key": apiKey,
          "Zotero-Server-ID": serverId
        },
        body: JSON.stringify([
          {
            itemType: "attachment",
            parentItem: createdKey,
            linkMode: "imported_file",
            title: "Full Text PDF",
            contentType: "application/pdf",
            filename,
            tags: [],
            relations: {}
          }
        ]),
        signal: AbortSignal.timeout(15000)
      });
      if (attResp.ok) {
        const attJson = await attResp.json();
        const attKey = attJson?.successful?.["0"]?.key || attJson?.success?.["0"];
        if (attKey) {
          attachmentKey = attKey;
          const md5 = createHash("md5").update(pdfBytes).digest("hex");
          const form = new URLSearchParams({
            md5,
            filename,
            filesize: String(pdfBytes.length),
            mtime: String(Date.now()),
            contentType: "application/pdf"
          });
          const authUpload = await fetch(`${baseUrl}/api/users/0/items/${attKey}/file`, {
            method: "POST",
            headers: {
              "Content-Type": "application/x-www-form-urlencoded",
              "If-None-Match": "*",
              "Zotero-Allowed-Request": "1",
              "Zotero-API-Key": apiKey,
              "Zotero-Server-ID": serverId
            },
            body: form.toString(),
            signal: AbortSignal.timeout(15000)
          });
          if (authUpload.ok) {
            const grant = await authUpload.json();
            if (grant?.exists) {
              pdfAttached = true;
            } else if (grant?.url && grant?.uploadKey) {
              const upResp = await fetch(grant.url, {
                method: "POST",
                headers: { "Content-Type": "application/pdf" },
                body: pdfBytes,
                signal: AbortSignal.timeout(30000)
              });
              if (upResp.ok || upResp.status === 201 || upResp.status === 204) {
                const regResp = await fetch(`${baseUrl}/api/users/0/items/${attKey}/file`, {
                  method: "POST",
                  headers: {
                    "Content-Type": "application/x-www-form-urlencoded",
                    "If-None-Match": "*",
                    "Zotero-Allowed-Request": "1",
                    "Zotero-API-Key": apiKey,
                    "Zotero-Server-ID": serverId
                  },
                  body: new URLSearchParams({ upload: grant.uploadKey }).toString(),
                  signal: AbortSignal.timeout(15000)
                });
                pdfAttached = regResp.ok || regResp.status === 204;
              }
            }
          }
        }
      }
    } catch {
      // Non-fatal if PDF attachment upload fails; item metadata is already saved
    }
  }

  // If paper has a user_note, create a Zotero child note under createdKey
  let noteCreated = false;
  let noteKey = null;
  if (userNote) {
    try {
      const escNote = (s) =>
        String(s)
          .replace(/&/g, "&amp;")
          .replace(/</g, "&lt;")
          .replace(/>/g, "&gt;")
          .replace(/"/g, "&quot;");
      const noteHtml = userNote
        .split(/\r?\n/)
        .map((line) => `<p>${escNote(line)}</p>`)
        .join("");
      const noteResp = await fetch(`${baseUrl}/api/users/0/items`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Zotero-Allowed-Request": "1",
          "Zotero-API-Key": apiKey,
          "Zotero-Server-ID": serverId
        },
        body: JSON.stringify([
          {
            itemType: "note",
            parentItem: createdKey,
            note: noteHtml,
            tags: userTags.map((t) => ({ tag: t })),
            relations: {}
          }
        ]),
        signal: AbortSignal.timeout(15000)
      });
      if (noteResp.ok) {
        const noteJson = await noteResp.json();
        const nKey = noteJson?.successful?.["0"]?.key || noteJson?.success?.["0"] || null;
        if (nKey) {
          noteKey = nKey;
          noteCreated = true;
        }
      }
    } catch {
      // Non-fatal if child note creation fails; parent metadata is already saved
    }
  }

  const createdZoteroItems = [
    createdKey,
    ...(attachmentKey ? [attachmentKey] : []),
    ...(noteKey ? [noteKey] : [])
  ];

  let undoAvailable = false;
  let undoWarning = null;
  try {
    await rememberZoteroImport(baseUrl, serverId, apiKey, createdKey, pKey, attachmentKey, noteKey);
    undoAvailable = true;
  } catch (err) {
    undoWarning = err.message;
  }
  invalidateZoteroProbeCache(pKey);
  return {
    alreadyExists: false,
    createdByPlugin: true,
    itemKey: createdKey,
    attachmentKey,
    noteKey,
    noteCreated,
    createdZoteroItems,
    pdfAttached,
    undoAvailable,
    undoWarning,
    selectUri: `zotero://select/library/items/${createdKey}`
  };
}

function zoteroItemSnapshot(item) {
  const sort = (value) => Array.isArray(value) ? value.map(sort) : value && typeof value === "object"
    ? Object.fromEntries(Object.keys(value).sort().filter((key) => !["key", "version"].includes(key)).map((key) => [key, sort(value[key])])) : value;
  return JSON.stringify(sort(item?.data || {}));
}

async function rememberZoteroImport(baseUrl, serverId, apiKey, itemKey, paperKey, attachmentKey = null, noteKey = null) {
  const headers = { "Zotero-Allowed-Request": "1", "Zotero-API-Key": apiKey, "Zotero-Server-ID": serverId };
  const resp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}`, { headers, signal: AbortSignal.timeout(8000) });
  if (!resp.ok) throw new Error("条目已保存，但未能回读核验；为保护文献库，暂不提供自动撤销。");
  const item = await resp.json();
  const version = item?.version ?? item?.data?.version;
  if (item?.key !== itemKey || !item.data || !Number.isInteger(version)) throw new Error("Zotero 回读身份/版本无效，无法建立自动撤销记录。");
  const createdZoteroItems = [
    itemKey,
    ...(attachmentKey ? [attachmentKey] : []),
    ...(noteKey ? [noteKey] : [])
  ];
  const record = { itemKey, paperKey, serverId, baseUrl, version, snapshot: zoteroItemSnapshot(item), attachmentKey, noteKey, createdZoteroItems, children: {} };
  for (const childKey of [attachmentKey, noteKey].filter(Boolean)) {
    const childResp = await fetch(`${baseUrl}/api/users/0/items/${childKey}`, { headers, signal: AbortSignal.timeout(8000) });
    if (!childResp.ok) throw new Error("未能回读子条目，无法建立自动撤销记录。");
    const child = await childResp.json();
    if (child?.data?.parentItem !== itemKey) throw new Error("子条目父条目不匹配。");
    record.children[childKey] = { version: child.version ?? child.data.version, snapshot: zoteroItemSnapshot(child) };
  }
  updateStateTransaction((state) => {
    state.zoteroImports = { ...(state.zoteroImports || {}), [itemKey]: record };
  });
  return record;
}

async function deletePaperFromLocalZotero(itemKey, record) {
  if (!record || record.itemKey !== itemKey || !record.snapshot || !Number.isInteger(record.version)) {
    throw new Error("只能撤销有完整核验记录的插件导入；既有或旧版未核验条目请在 Zotero 中管理。");
  }
  const { settings } = getEffectiveConfig();
  const baseUrl = settings.zoteroUrl;
  const probe = await probeLocalZotero([]);
  if (!probe.zoteroOnline) throw new Error("未检测到本机 Zotero 桌面端在线，请先确认 Zotero 正在运行。");
  if (probe.serverId !== record.serverId || baseUrl !== record.baseUrl) throw new Error("Zotero 服务实例已改变，拒绝撤销其他文献库的导入。");
  const { key: apiKey, serverId } = await getOrAuthorizeZoteroWriteKey(baseUrl, probe.serverId);
  const headers = { "Zotero-Allowed-Request": "1", "Zotero-API-Key": apiKey, "Zotero-Server-ID": serverId };
  const response = await fetch(`${baseUrl}/api/users/0/items/${itemKey}`, { headers, signal: AbortSignal.timeout(8000) });
  if (response.status === 404) return { deleted: true, itemKey };
  if (!response.ok) throw new Error(`无法核验 Zotero 条目 (HTTP ${response.status})，未执行删除。`);
  const item = await response.json();
  if (item?.key !== itemKey || (item.version ?? item.data?.version) !== record.version || zoteroItemSnapshot(item) !== record.snapshot) {
    throw new Error("Zotero 条目的身份或版本已改变，未执行删除。请在 Zotero 中核对。");
  }
  const childResp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}/children?limit=100`, { headers, signal: AbortSignal.timeout(8000) });
  if (!childResp.ok) throw new Error("未能核验 Zotero 子条目，未执行删除。");
  const children = await childResp.json();
  if (!Array.isArray(children) || children.some((child) => {
    const saved = record.children?.[child.key];
    return !saved || child.data?.parentItem !== itemKey || (child.version ?? child.data?.version) !== saved.version || zoteroItemSnapshot(child) !== saved.snapshot;
  })) throw new Error("Zotero 条目已新增或修改附件/笔记，未执行删除。");
  for (const [childKey, childRec] of Object.entries(record.children || {})) {
    let childDelResp;
    try {
      childDelResp = await fetch(`${baseUrl}/api/users/0/items/${childKey}`, {
        method: "DELETE",
        headers: { ...headers, ...(Number.isInteger(childRec?.version) ? { "If-Unmodified-Since-Version": String(childRec.version) } : {}) },
        signal: AbortSignal.timeout(8000)
      });
    } catch (err) {
      throw new Error(`撤销 Zotero 子条目 (${childKey}) 失败 (${err?.message || "network error"})，已停止删除父条目并保留导入记录。`);
    }
    if (!childDelResp.ok && childDelResp.status !== 404) {
      throw new Error(`撤销 Zotero 子条目 (${childKey}) 失败 (HTTP ${childDelResp.status})，已停止删除父条目并保留导入记录。`);
    }
  }
  const delResp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}`, {
    method: "DELETE", headers: { ...headers, "If-Unmodified-Since-Version": String(record.version) }, signal: AbortSignal.timeout(10000)
  });
  if (!delResp.ok && delResp.status !== 404) throw new Error(`撤销 Zotero 导入失败 (HTTP ${delResp.status})，导入记录已保留。`);
  return { deleted: true, itemKey, deletedItems: record.createdZoteroItems || [itemKey] };
}

function normalizeVenueCacheKey(name) {
  return String(name || "")
    .replace(/\s*\((?:online|print|electronic|issn[^)]*)\)\s*$/i, "")
    .replace(/\s*\[[A-Z]\]\s*$/, "")
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, " ")
    .trim();
}

function compactVenueRank(vr) {
  if (!vr || typeof vr !== "object") return null;
  const { official_all, official_select, custom_ranks, ...rest } = vr;
  return rest;
}

function createEnrichContext(state) {
  return {
    basketKeys: new Set((state?.basket || []).map((p) => p.paper_key || computePaperKey(p))),
    pdfIndex: scanSaveDirPdfIndex(),
    ownersByPath: buildEvidencePdfOwnersMap(state),
    rankCache: readJsonSafe(EASYSCHOLAR_CACHE_PATH, {}) || {}
  };
}

function enrichPapersList(papers, state, zoteroMatches = {}, runMeta = null, sharedCtx = null) {
  const ctx = sharedCtx || createEnrichContext(state);
  const { basketKeys, pdfIndex, ownersByPath, rankCache } = ctx;
  return (papers || []).map((rawP, idx) => {
    const key = rawP.paper_key || computePaperKey(rawP);
    const savedEv = state.evidenceMap?.[key] || {};
    const enrichedPatch = savedEv.enriched || {};
    const p = {
      ...rawP,
      ...enrichedPatch,
      identifiers: { ...(rawP.identifiers || {}), ...(enrichedPatch.identifiers || {}) },
      oa_locations: [...(rawP.oa_locations || []), ...(enrichedPatch.oa_locations || [])]
    };

    const unpacked = unpackJournalAndTldr(p);
    let venueRank = p.venue_rank || null;
    if (!venueRank && unpacked.journal) {
      const vKey = normalizeVenueCacheKey(unpacked.journal);
      if (vKey && rankCache[vKey]?.rank) {
        venueRank = rankCache[vKey].rank;
      }
    }
    venueRank = compactVenueRank(venueRank);
    const { localPdf, candidatePdf } = resolveVerifiedLocalPdfForPaper(
      { ...p, paper_key: key },
      state,
      pdfIndex,
      ownersByPath
    );

    let stage = savedEv.stage || p.handoff_stage || "discovered";
    if (
      (localPdf || runMeta?.fetchedPaperKey === key) &&
      stage !== "read" &&
      stage !== "identity_checked" &&
      stage !== "excluded"
    ) {
      stage = "file_obtained";
    }
    if (p.read === true && stage !== "excluded") {
      stage = "read";
    }
    const normalizedAuthors = (p.authors || []).map((a) => {
      const { raw_affiliation_strings, ...restAuthor } = a || {};
      return {
        ...restAuthor,
        affiliations: normalizeAffiliationsList(a?.affiliations)
      };
    });
    const relCorrections = (p.relations || []).filter(
      (r) => r && (r.type === "retraction" || r.type === "correction" || r.type === "is-retracted-by")
    );
    const combinedCorrections = [...(p.comment_corrections || []), ...relCorrections];
    const rawProbeMatch = zoteroMatches[key];
    const wasJustUndone =
      savedEv.zoteroUndoneKey &&
      rawProbeMatch?.itemKey === savedEv.zoteroUndoneKey &&
      Date.now() - (savedEv.zoteroUndoneAt || 0) < 10000;
    const baseZMatch = wasJustUndone
      ? null
      : rawProbeMatch ||
        savedEv.zoteroMatch ||
        p.zotero_match ||
        (p.local_matches?.length ? { matched: true, localMatches: p.local_matches } : null);
    const isPluginCreated = Boolean(baseZMatch?.itemKey && state.zoteroImports?.[baseZMatch.itemKey]);
    const zMatch = baseZMatch
      ? {
          ...baseZMatch,
          createdByPlugin: isPluginCreated,
          undoAvailable: isPluginCreated,
          attachmentKey: baseZMatch.attachmentKey || savedEv.zoteroMatch?.attachmentKey || null
        }
      : null;
    const candidatePdfUrls = extractDownloadablePdfUrls(p);
    const userTags = normalizeUserTags(
      savedEv.user_tags !== undefined ? savedEv.user_tags : p.user_tags !== undefined ? p.user_tags : []
    );
    const userNote =
      savedEv.user_note !== undefined
        ? String(savedEv.user_note || "")
        : p.user_note !== undefined
          ? String(p.user_note || "")
          : "";
    const epmcSavedDir = savedEv.epmc_saved_dir || p.epmc_saved_dir || null;
    const epmcSupplFiles = Array.isArray(savedEv.epmc_suppl_files)
      ? savedEv.epmc_suppl_files
      : Array.isArray(p.epmc_suppl_files)
        ? p.epmc_suppl_files
        : [];
    const sciverseSavedDir = savedEv.sciverse_saved_dir || p.sciverse_saved_dir || null;
    const sciverseFigureFiles = Array.isArray(savedEv.sciverse_figure_files)
      ? savedEv.sciverse_figure_files
      : Array.isArray(p.sciverse_figure_files)
        ? p.sciverse_figure_files
        : [];
    const sciverseExcerptPath = savedEv.sciverse_excerpt_path || p.sciverse_excerpt_path || null;
    const sciverseMaterialsPath = savedEv.sciverse_materials_path || p.sciverse_materials_path || null;
    const doc2mdOutDir =
      savedEv.doc2md_out_dir && existsSync(savedEv.doc2md_out_dir)
        ? savedEv.doc2md_out_dir
        : p.doc2md_out_dir && existsSync(p.doc2md_out_dir)
          ? p.doc2md_out_dir
          : null;
    const doc2mdMdPath =
      savedEv.doc2md_md_path && existsSync(savedEv.doc2md_md_path)
        ? savedEv.doc2md_md_path
        : p.doc2md_md_path && existsSync(p.doc2md_md_path)
          ? p.doc2md_md_path
          : null;
    const doc2mdSiOutDir =
      savedEv.doc2md_si_out_dir && existsSync(savedEv.doc2md_si_out_dir)
        ? savedEv.doc2md_si_out_dir
        : p.doc2md_si_out_dir && existsSync(p.doc2md_si_out_dir)
          ? p.doc2md_si_out_dir
          : null;
    const doc2mdJobId = savedEv.doc2md_job_id || p.doc2md_job_id || null;
    const { versions, conflicts, ...cleanP } = p;

    return {
      ...cleanP,
      journal: unpacked.journal,
      venue: unpacked.venue,
      journal_info: unpacked.journal_info,
      venue_rank: venueRank,
      tldr: unpacked.tldr,
      abstract_available: Boolean(p.abstract),
      authors: normalizedAuthors,
      comment_corrections: combinedCorrections,
      index: idx + 1,
      paper_key: key,
      resolvable_seed_id: computeResolvableSeedId(p),
      clickable_links: buildClickableLinks(p),
      candidate_pdf_urls: candidatePdfUrls,
      primary_pdf_url: candidatePdfUrls[0] || null,
      handoff_stage: stage,
      handoff_stage_label: STAGE_LABELS[stage] || stage,
      screening_basis: savedEv.basis || (p.abstract ? "abstract" : p.evidence_level || "metadata_only"),
      screening_reason: savedEv.reason ?? p.screening_reason ?? "",
      user_tags: userTags,
      user_note: userNote,
      local_pdf_path: localPdf,
      ...(candidatePdf && !localPdf ? { candidate_local_pdf_path: candidatePdf } : {}),
      ...(epmcSavedDir ? { epmc_saved_dir: epmcSavedDir } : {}),
      ...(epmcSupplFiles.length ? { epmc_suppl_files: epmcSupplFiles } : {}),
      ...(sciverseSavedDir ? { sciverse_saved_dir: sciverseSavedDir } : {}),
      ...(sciverseFigureFiles.length ? { sciverse_figure_files: sciverseFigureFiles } : {}),
      ...(sciverseExcerptPath ? { sciverse_excerpt_path: sciverseExcerptPath } : {}),
      ...(sciverseMaterialsPath ? { sciverse_materials_path: sciverseMaterialsPath } : {}),
      ...(doc2mdOutDir ? { doc2md_out_dir: doc2mdOutDir } : {}),
      ...(doc2mdMdPath ? { doc2md_md_path: doc2mdMdPath } : {}),
      ...(doc2mdSiOutDir ? { doc2md_si_out_dir: doc2mdSiOutDir } : {}),
      ...(doc2mdJobId ? { doc2md_job_id: doc2mdJobId } : {}),
      in_basket: basketKeys.has(key),
      zotero_match: zMatch
    };
  });
}

function extractDoisFromText(text) {
  if (!text) return [];
  const headText = String(text).split(/\n##\s+References\b/i)[0];
  const matches = headText.match(/10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/g) || [];
  return [...new Set(matches.map((m) => normalizeDoi(m)).filter((d) => /^10\.\d{4,9}\/\S+$/.test(d)))];
}

function parseRawItemToCandidatePaper(flatItem, idx, state, sharedCtx = null) {
  const verifiedDois = flatItem.verified_dois || [];
  const candidateDois = flatItem.extracted_dois || [];
  const yearMatch = String(flatItem.publicationDate || flatItem.filingDate || "").match(/\b(19|20)\d{2}\b/);
  const year = flatItem.year || (yearMatch ? Number(yearMatch[0]) : null);

  let authors = [];
  if (flatItem.inventor) {
    authors = String(flatItem.inventor)
      .split(/[,;]/)
      .map((s) => s.trim())
      .filter(Boolean)
      .map((name) => ({ name, identifiers: {}, affiliations: flatItem.assignee ? [flatItem.assignee] : [] }));
  } else if (flatItem.author) {
    authors = String(flatItem.author)
      .split(/[,;]/)
      .map((s) => s.trim())
      .filter((s) => s && !/^et al\.?$/i.test(s))
      .map((name) => ({ name, identifiers: {}, affiliations: [] }));
  } else if (flatItem.publicationInfo) {
    const cleanInfo = String(flatItem.publicationInfo).replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    const authorPart = cleanInfo.split(/\s+-\s+/)[0] || "";
    authors = authorPart
      .replace(/…/g, "")
      .split(",")
      .map((s) => s.trim())
      .filter(Boolean)
      .map((name) => ({ name, identifiers: {}, affiliations: [] }));
  }

  let venue = "";
  if (flatItem.publicationNumber) {
    venue = `Patent ${flatItem.publicationNumber}${flatItem.assignee ? ` (${flatItem.assignee})` : ""}`;
  } else if (flatItem.publicationInfo) {
    const cleanInfo = String(flatItem.publicationInfo).replace(/\u00a0/g, " ").replace(/\s+/g, " ").trim();
    const parts = cleanInfo.split(/\s+-\s+/);
    const rawVenue = (parts[1] || parts[0] || "Google Scholar").replace(/,?\s*\b(19|20)\d{2}\b\s*$/, "").trim();
    venue = rawVenue || "Google Scholar";
  } else if (flatItem.serper_type) {
    venue = `Serper (${flatItem.serper_type})`;
  }

  // Never populate identifiers.doi from snippet/text; snippet DOIs remain unverified clues in candidate_dois.
  const identifiers = {};
  if (verifiedDois.length) identifiers.doi = verifiedDois;
  if (flatItem.publicationNumber) identifiers.patent = [flatItem.publicationNumber];

  const oaLocations = [];
  if (flatItem.pdfUrl) {
    oaLocations.push({
      provider: flatItem.serper_type || "serper",
      url: flatItem.pdfUrl,
      license_status: "free_link",
      version: "fulltext_pdf"
    });
  }

  const rawPaper = {
    title: flatItem.title || flatItem.id || `检索条目 #${idx + 1}`,
    year,
    journal: venue,
    venue,
    authors,
    identifiers,
    ...(candidateDois.length ? { candidate_dois: candidateDois } : {}),
    citation_count: flatItem.citedBy ?? null,
    url: flatItem.link || flatItem.url || "",
    pdfUrl: flatItem.pdfUrl || null,
    oa_locations: oaLocations,
    evidence_level: "metadata_only",
    abstract: "",
    abstract_available: false,
    search_snippet: flatItem.snippet || flatItem.text || "",
    provenance: [{ provider: flatItem.serper_type ? `serper:${flatItem.serper_type}` : "raw_search" }]
  };

  return enrichPapersList([rawPaper], state, {}, null, sharedCtx)[0];
}

function normalizeRawResults(resultsRaw, state, sharedCtx = null) {
  let rawArray = [];
  let containerMeta = null;
  if (Array.isArray(resultsRaw)) {
    rawArray = resultsRaw;
  } else if (resultsRaw && typeof resultsRaw === "object" && Array.isArray(resultsRaw.results)) {
    rawArray = resultsRaw.results;
    containerMeta = {
      provider: resultsRaw.provider || "serper",
      type: resultsRaw.type || "scholar",
      queries: resultsRaw.queries || []
    };
  } else {
    return [];
  }
  const ctx = sharedCtx || createEnrichContext(state);

  return rawArray.map((item, idx) => {
    const isWrappedSerper =
      item && typeof item === "object" && item.data && typeof item.data === "object" && !item.title;
    const flat = isWrappedSerper
      ? {
          ...item.data,
          serper_type: item.type || containerMeta?.type || "scholar",
          query_index: item.query_index ?? 0,
          page: item.page ?? 1
        }
      : {
          ...item,
          serper_type: item?.type || containerMeta?.type || undefined
        };

    const entityDoi = Array.isArray(flat.entities)
      ? flat.entities.find((e) => e?.type === "publication" && e?.properties?.doi)?.properties?.doi
      : null;
    const verifiedText = entityDoi
      ? String(entityDoi)
      : [flat.url, flat.link, flat.doi].filter(Boolean).join(" ");
    const verifiedDois = extractDoisFromText(verifiedText);
    const snippetText = entityDoi
      ? String(entityDoi)
      : [flat.url, flat.link, flat.doi, flat.snippet, flat.text ? String(flat.text).split(/\n##\s+References\b/i)[0] : ""]
          .filter(Boolean)
          .join(" ");
    const extractedDois = extractDoisFromText(snippetText);
    const enrichedItem = {
      ...flat,
      index: idx + 1,
      verified_dois: verifiedDois,
      extracted_dois: extractedDois
    };
    // General web/maps/shop snippets can mention unrelated DOIs and are not paper identities.
    enrichedItem.candidate_paper = flat.serper_type === "scholar"
      ? parseRawItemToCandidatePaper(enrichedItem, idx, state, ctx) : null;
    return enrichedItem;
  });
}

function formatRunHumanSummary(runJson, acqJson, paperCount, resultCount) {
  const cmd = runJson?.command || runJson?.action || (acqJson ? "fetch_pdf" : "search");
  const q = runJson?.query ?? acqJson?.requested_identity ?? {};

  if (cmd === "search") {
    const kw = typeof q === "string" ? q : q.text || q.query || "";
    const srcs = Array.isArray(q.sources) ? q.sources.join(" + ") : "多源";
    return {
      modeLabel: "多源主题检索",
      title: kw ? `检索：「${kw}」` : "多源学术检索",
      subtitle: `数据源: ${srcs} · 去重后 ${paperCount} 篇文献`
    };
  }
  if (cmd === "lookup") {
    const idVal = typeof q === "string" ? q : q.id || "";
    return {
      modeLabel: "DOI / ID 精确核验",
      title: `精确核验：${idVal}`,
      subtitle: q.expected_title ? `期望标题核对 · ${paperCount} 篇` : `共匹配 ${paperCount} 篇标准题录`
    };
  }
  if (cmd === "related") {
    const relMap = {
      references: "参考文献 (向前溯源)",
      citations: "施引文献 (最新跟进)",
      recommendations: "相似论文推荐"
    };
    return {
      modeLabel: "引文网络追踪",
      title: `${relMap[q.relation] || "相关文献"}：${q.id || ""}`,
      subtitle: `共发现 ${paperCount} 篇关联文献`
    };
  }
  if (cmd === "author") {
    const target = q.text || q.author_id || q.from_paper || "学者查询";
    const candCount = Array.isArray(runJson?.author_candidates) ? runJson.author_candidates.length : 0;
    return {
      modeLabel: "学者消歧与代表作",
      title: `学者：${target} (${q.provider || "openalex"})`,
      subtitle:
        paperCount > 0
          ? `已拉取 ${paperCount} 篇代表作`
          : `定位到 ${candCount} 位候选学者`
    };
  }
  if (cmd === "epmc") {
    const rawKw = typeof q === "string" ? q : q.query || q.id || "检索";
    const cleanKw = String(rawKw)
      .replace(/\s+sort_(?:cited|date):y\s*$/i, "")
      .replace(/^\((.*)\)\s+AND\s+(?:HAS_SUPPL:y|OPEN_ACCESS:y|HAS_DATA:y|SRC:PPR|HAS_FT:y|\(PUB_YEAR:\[\d+\s+TO\s+\d+\]\))(?:\s+AND\s+.*)*$/i, "$1")
      .replace(/^\((.*)\)$/, "$1")
      .replace(/^DOI:"(.*)"$/i, "$1")
      .replace(/^EXT_ID:"(\d+)"\s+AND\s+SRC:MED$/i, "pmid:$1")
      .trim();
    const fetchMode = runJson?.fetch_mode || q?.fetch || "none";
    const actuallyFetched = Boolean(runJson?.fulltext_xml_fetched || runJson?.supplementary_fetched);
    return {
      modeLabel: "Europe PMC 与全文/SI",
      title: `Europe PMC：「${cleanKw || rawKw}」`,
      subtitle: `匹配 ${paperCount} 篇文献${actuallyFetched && fetchMode && fetchMode !== "none" ? ` · 已抓包 (${fetchMode})` : ""}`
    };
  }
  if (cmd === "exa") {
    const kw = typeof q === "string" ? q : q.query || "";
    return {
      modeLabel: "Exa AI 语义检索",
      title: `Exa 语义：「${kw}」`,
      subtitle: `发现 ${paperCount} 篇论文 (${resultCount} 条原始片段)`
    };
  }
  if (cmd === "serper") {
    const sType = q.type || "scholar";
    const typeMap = {
      scholar: "Google Scholar (谷歌学术)",
      patents: "Google Patents (全球专利)",
      search: "Google 学术网页定位",
      webpage: "网页全文提取"
    };
    const firstReq = Array.isArray(q.requests) && q.requests[0] ? q.requests[0] : {};
    const kw = firstReq.q || firstReq.url || "";
    const yr = q.year_from ? ` (${q.year_from}年至今)` : "";
    return {
      modeLabel: typeMap[sType] || `Serper (${sType})`,
      title: `${typeMap[sType] || "Serper"}：「${kw}」${yr}`,
      subtitle: `返回 ${resultCount} 条结果`
    };
  }
  if (cmd === "oa" || cmd === "resolve_only") {
    return {
      modeLabel: "OA 开放获取定位",
      title: `OA 全文定位：${q.input || ""}`,
      subtitle: `已核查 ${paperCount} 篇文献的开放获取链接`
    };
  }
  if (cmd === "fetch_pdf") {
    return {
      modeLabel: "PDF 下载与校验",
      title: `PDF 下载：${q.title || q.doi || "全文文件"}`,
      subtitle: `首屏校验状态: ${acqJson?.identity?.status || "已保存"}`
    };
  }
  if (cmd === "merge") {
    return {
      modeLabel: "多批次去重合并",
      title: `合并记录 (${(q.inputs || []).length} 个来源)`,
      subtitle: `去重后共 ${paperCount} 篇文献`
    };
  }
  if (cmd === "diff") {
    return {
      modeLabel: "记录增量对比",
      title: `对比：${q.before || ""} ➔ ${q.after || ""}`,
      subtitle: `共 ${paperCount} 篇当前文献`
    };
  }
  if (cmd === "rank") {
    const vList = Array.isArray(q.venues) ? q.venues.join(", ") : "";
    return {
      modeLabel: "期刊等级查询 (easyScholar)",
      title: vList ? `期刊等级：${vList.slice(0, 48)}` : "期刊等级与分区批量标注",
      subtitle: `已核验 ${resultCount || paperCount} 条期刊/论文记录`
    };
  }
  if (cmd === "ai4scholar") {
    const m = q.mode || runJson?.ai4scholar_mode || "s2-search";
    const modeNames = {
      "s2-search": "Semantic Scholar 相关性检索 (AI4Scholar)",
      "s2-bulk": "Semantic Scholar 批量布尔检索 (AI4Scholar)",
      "s2-match": "Semantic Scholar 标题精确匹配",
      "s2-detail": "Semantic Scholar 论文详情",
      "s2-batch": "Semantic Scholar 批量查询",
      "s2-citations": "Semantic Scholar 施引追踪",
      "s2-references": "Semantic Scholar 参考文献溯源",
      "s2-recommendations": "Semantic Scholar 相似论文推荐",
      "s2-snippets": "Semantic Scholar 全文片段搜索 (500词摘录)",
      "s2-authors": "Semantic Scholar 学者搜索",
      "s2-author-papers": "Semantic Scholar 学者代表作",
      "gs-search": "Google Scholar 谷歌学术 (AI4Scholar)",
      "gs-cite": "Google Scholar 标准引用获取",
      "gs-profiles": "Google Scholar 学者主页搜索",
      "gs-author": "Google Scholar 学者主页与论文",
      patents: "Google Patents 全球专利检索 (AI4Scholar)",
      "pubmed-search": "PubMed 生物医学文献检索 (AI4Scholar)",
      "pubmed-detail": "PubMed 论文详情 (PMID)",
      "pubmed-batch": "PubMed 批量查询",
      "pubmed-citations": "PubMed 施引文献追踪",
      "pubmed-related": "PubMed 相似相关文献",
      draw: "AI 科研绘图与矢量化 (Nano Draw)",
      "auto-cite": "学术段落自动配参考文献 (Auto-Cite)",
      credits: "AI4Scholar 账户积分查询"
    };
    const kw = q.query || q.id || (Array.isArray(q.ids) ? q.ids.join(", ") : "") || "";
    const credInfo = runJson?.credits?.remaining_latest !== undefined && runJson?.credits?.remaining_latest !== null
      ? ` · 剩余积分 ${runJson.credits.remaining_latest}`
      : "";
    return {
      modeLabel: modeNames[m] || `AI4Scholar (${m})`,
      title: kw ? `${modeNames[m] || m}：「${String(kw).slice(0, 42)}」` : modeNames[m] || `AI4Scholar (${m})`,
      subtitle: `${paperCount ? `共 ${paperCount} 篇文献` : `${resultCount} 条结果`}${credInfo}`
    };
  }
  if (cmd === "sciverse") {
    const m = q.mode || runJson?.sciverse_mode || "meta-search";
    const sciverseModeNames = {
      "meta-search": "SciVerse 结构化文献检索",
      "semantic-search": "SciVerse 全文语义片段检索 (Agentic RAG)",
      content: "SciVerse 论文原文 Markdown 切片与原图",
      resource: "SciVerse 论文图表资源下载",
      relations: "SciVerse 论文引文关系追踪",
      catalog: "SciVerse 元数据字段目录自省",
      "schema-search": "SciVerse Paper Schema 结构化检索",
      "schema-materials": "SciVerse Paper Schema 复现与方法材料包",
      "schema-entities": "SciVerse Paper Schema 结构对象检索",
      "schema-evidence": "SciVerse Paper Schema 结构化证据检索",
      "schema-citations": "SciVerse Paper Schema 引文图谱扩展",
      "schema-in-paper": "SciVerse Paper Schema 单篇正文检索与定位"
    };
    const kw = q.query || q.id || q.doc_id || q.schema_id || "";
    const chunkInfo = runJson?.chunk_count ? ` · ${runJson.chunk_count} 条原文片段` : "";
    const contentInfo = runJson?.sciverse_content?.chars_returned
      ? ` · 原文 ${runJson.sciverse_content.chars_returned} 字符${runJson.sciverse_content.downloaded_figures?.length ? ` · 已下原图 ${runJson.sciverse_content.downloaded_figures.length} 张` : ""}`
      : "";
    return {
      modeLabel: sciverseModeNames[m] || `SciVerse (${m})`,
      title: kw ? `${sciverseModeNames[m] || m}：「${String(kw).slice(0, 42)}」` : sciverseModeNames[m] || `SciVerse (${m})`,
      subtitle: `${paperCount ? `共 ${paperCount} 篇文献` : `${resultCount} 条记录`}${chunkInfo}${contentInfo}`
    };
  }
  return {
    modeLabel: cmd,
    title: `检索记录 (${cmd})`,
    subtitle: `${paperCount || resultCount} 条记录`
  };
}

function loadRunBundle(runIdOrPath, zoteroMatches = {}, sharedCtx = null) {
  const runDir = resolveRunDir(runIdOrPath);
  if (!existsSync(runDir)) {
    throw new Error(`Run directory not found: ${runIdOrPath}`);
  }
  const runId = basename(runDir);
  const rawRunJson = readJsonSafe(join(runDir, "run.json"), {});
  const papersRaw = readJsonSafe(join(runDir, "papers.json"), []);
  const resultsRaw = readJsonSafe(join(runDir, "results.json"), null);
  const manifestJson = readJsonSafe(join(runDir, "manifest.json"), null);
  const fulltextSummary = readJsonSafe(join(runDir, "fulltext_summary.json"), null);
  const supplManifest = readJsonSafe(join(runDir, "supplementary_manifest.json"), null);
  const acquisitionJson = readJsonSafe(join(runDir, "acquisition.json"), null);
  const summaryMd = existsSync(join(runDir, "summary.md"))
    ? readFileSync(join(runDir, "summary.md"), "utf8")
    : "";
  const reportMd = existsSync(join(runDir, "report.md"))
    ? readFileSync(join(runDir, "report.md"), "utf8")
    : "";

  const SYSTEM_JSON_NAMES = new Set([
    "run.json",
    "manifest.json",
    "papers.json",
    "results.json",
    "response.json",
    "raw_responses.json",
    "acquisition.json",
    "fulltext_summary.json",
    "supplementary_manifest.json"
  ]);

  const files = [];
  function walkFiles(dir, relPrefix = "") {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const rel = relPrefix ? `${relPrefix}/${entry.name}` : entry.name;
      const full = join(dir, entry.name);
      if (entry.isSymbolicLink()) continue;
      if (entry.isDirectory()) {
        walkFiles(full, rel);
      } else {
        const st = statSync(full);
        const ext = extname(entry.name).toLowerCase();
        const isDeliverable =
          !SYSTEM_JSON_NAMES.has(entry.name) &&
          (ext === ".pdf" ||
            ext === ".zip" ||
            ext === ".docx" ||
            ext === ".xlsx" ||
            ext === ".pptx" ||
            ext === ".png" ||
            ext === ".jpg" ||
            ext === ".jpeg" ||
            ext === ".webp" ||
            ext === ".svg" ||
            ext === ".bib" ||
            rel.startsWith("supplementary/") ||
            rel.startsWith("figures/") ||
            entry.name === "annotated_text.md" ||
            entry.name === "fulltext_excerpt.md" ||
            entry.name === "schema_materials.md" ||
            entry.name === "report.md");
        files.push({
          path: rel,
          fullPath: full,
          size: st.size,
          ext,
          isDeliverable
        });
      }
    }
  }
  walkFiles(runDir);

  const hasFetchedFiles = existsSync(join(runDir, "fulltext.xml")) ||
    Boolean(supplManifest?.files?.some((file) => isWithin(runDir, resolve(runDir, file.path)) && existsSync(resolve(runDir, file.path))));
  const state = loadState();
  const ctx = sharedCtx || createEnrichContext(state);
  let papers = enrichPapersList(papersRaw, state, zoteroMatches, {
    fetchedPaperKey: hasFetchedFiles && papersRaw[0] ? computePaperKey(papersRaw[0]) : null
  }, ctx);
  const exportedSaveDirForRun = state.runExports?.[runId] || rawRunJson.exported_save_dir || null;
  const supplDeliverables = files.filter((f) => f.path.startsWith("supplementary/"));
  if (papers.length > 0 && supplDeliverables.length > 0) {
    const firstP = papers[0];
    const savedSupplDir = exportedSaveDirForRun ? join(exportedSaveDirForRun, "supplementary") : join(runDir, "supplementary");
    const supplPaths = supplDeliverables.map((f) =>
      exportedSaveDirForRun ? join(exportedSaveDirForRun, f.path) : f.fullPath
    );
    papers[0] = {
      ...firstP,
      epmc_saved_dir: firstP.epmc_saved_dir || savedSupplDir,
      epmc_suppl_files: firstP.epmc_suppl_files?.length ? firstP.epmc_suppl_files : supplPaths
    };
  }
  if (rawRunJson.diff && typeof rawRunJson.diff === "object") {
    const addedList = Array.isArray(rawRunJson.diff.added) ? rawRunJson.diff.added : [];
    const changedList = Array.isArray(rawRunJson.diff.changed) ? rawRunJson.diff.changed : [];
    const removedList = Array.isArray(rawRunJson.diff.removed) ? rawRunJson.diff.removed : [];
    papers = papers.map((p) => {
      const isAdded = addedList.some((cand) => computePaperKey(cand) === p.paper_key || samePaperIdentity(p, cand));
      const changedEntry = changedList.find(
        (c) => samePaperIdentity(p, { identifiers: c.identifiers || {}, title: c.after?.title, year: c.after?.year })
      );
      return {
        ...p,
        diff_status: isAdded ? "added" : changedEntry ? "changed" : "unchanged",
        diff_fields: changedEntry?.fields || []
      };
    });
    if (removedList.length > 0) {
      const enrichedRemoved = enrichPapersList(removedList, state, zoteroMatches, null, ctx).map((rp, idx) => ({
        ...rp,
        index: papers.length + idx + 1,
        diff_status: "removed",
        diff_fields: []
      }));
      papers = [...papers, ...enrichedRemoved];
    }
  }
  const sourceStatuses = rawRunJson.source_status || rawRunJson.source_statuses || {};
  const cmdName = rawRunJson.command || rawRunJson.action || (acquisitionJson ? "fetch_pdf" : "search");
  const ai4sMode = rawRunJson.ai4scholar_mode || rawRunJson.query?.mode || "";
  const sciverseMode = rawRunJson.sciverse_mode || rawRunJson.query?.mode || "";
  const isWrappedSerperPayload = Boolean(resultsRaw && typeof resultsRaw === "object" && !Array.isArray(resultsRaw) && Array.isArray(resultsRaw.results));
  const shouldIncludeRawResults =
    isWrappedSerperPayload ||
    cmdName === "serper" ||
    (cmdName === "exa" && (rawRunJson.unconverted_result_count > 0 || papers.length === 0)) ||
    (cmdName === "ai4scholar" && ai4sMode === "s2-snippets" && papers.length === 0) ||
    (cmdName === "sciverse" && ["catalog", "schema-entities", "schema-in-paper"].includes(sciverseMode) && papers.length === 0);
  const enrichedResults = shouldIncludeRawResults ? normalizeRawResults(resultsRaw, state, ctx) : [];

  const normalizedCandidates = Array.isArray(rawRunJson.author_candidates)
    ? rawRunJson.author_candidates.map((c) => {
        const cleanAuthorId = String(c.author_id || "").replace(/^https?:\/\/openalex\.org\//i, "");
        const wCount = c.works_count ?? c.paper_count ?? undefined;
        const cCount = c.cited_by_count ?? c.citation_count ?? undefined;
        return {
          ...c,
          author_id: cleanAuthorId,
          works_count: wCount,
          paper_count: wCount,
          cited_by_count: cCount,
          citation_count: cCount,
          affiliations: normalizeAffiliationsList(c.affiliations)
        };
      })
    : [];

  const humanSummary = formatRunHumanSummary(rawRunJson, acquisitionJson, papers.length, countNormalizedResults(resultsRaw));

  const runJson = {
    ...rawRunJson,
    ...(state.runExports?.[runId] ? { exported_save_dir: state.runExports[runId] } : {}),
    command: cmdName,
    action: cmdName,
    timestamp: rawRunJson.created_at || rawRunJson.timestamp || acquisitionJson?.retrieved_at || null,
    source_status: sourceStatuses,
    source_statuses: sourceStatuses,
    author_candidates: normalizedCandidates,
    humanSummary
  };

  return {
    runId,
    runDir,
    run: runJson,
    papers,
    results: enrichedResults,
    summaryMd,
    reportMd,
    manifest: manifestJson,
    fulltextSummary,
    supplementaryManifest: supplManifest,
    acquisition: acquisitionJson,
    files,
    deliverableFiles: files.filter((f) => f.isDeliverable)
  };
}

function countNormalizedResults(resultsRaw) {
  if (Array.isArray(resultsRaw)) return resultsRaw.length;
  if (resultsRaw && typeof resultsRaw === "object" && Array.isArray(resultsRaw.results)) {
    return resultsRaw.results.length;
  }
  return 0;
}

const runSummaryCache = new Map();

function fileStatSignature(filePath) {
  try {
    if (!existsSync(filePath)) return "0:0";
    const st = statSync(filePath);
    return `${st.mtimeMs}:${st.size}`;
  } catch {
    return "0:0";
  }
}

function listRecentRuns(limit = 40) {
  if (!existsSync(RUNS_DIR)) return [];
  const dirEntries = [];
  for (const e of readdirSync(RUNS_DIR, { withFileTypes: true })) {
    if (!e.isDirectory() || e.name.startsWith(".")) continue;
    try {
      const dir = join(RUNS_DIR, e.name);
      const st = statSync(dir);
      dirEntries.push({ name: e.name, dir, mtimeMs: st.mtimeMs });
    } catch {}
  }
  dirEntries.sort((a, b) => b.mtimeMs - a.mtimeMs);
  return dirEntries.slice(0, limit).map(({ name, dir, mtimeMs }) => {
    const sig = [
      mtimeMs,
      fileStatSignature(join(dir, "run.json")),
      fileStatSignature(join(dir, "acquisition.json")),
      fileStatSignature(join(dir, "papers.json")),
      fileStatSignature(join(dir, "results.json"))
    ].join("|");
    const cached = runSummaryCache.get(dir);
    if (cached && cached.sig === sig) {
      return cached.entry;
    }
    const runJson = readJsonSafe(join(dir, "run.json"), null);
    const acqJson = readJsonSafe(join(dir, "acquisition.json"), null);
    const papers = readJsonSafe(join(dir, "papers.json"), []);
    const resultsRaw = readJsonSafe(join(dir, "results.json"), null);
    const paperCount = Array.isArray(papers) ? papers.length : 0;
    const resultCount = countNormalizedResults(resultsRaw);
    const cmd = runJson?.command || runJson?.action || (acqJson ? "fetch_pdf" : "search");
    const human = formatRunHumanSummary(runJson, acqJson, paperCount, resultCount);
    const entry = {
      runId: name,
      runDir: dir,
      mtimeMs,
      timestamp: runJson?.created_at || runJson?.timestamp || acqJson?.retrieved_at || new Date(mtimeMs).toISOString(),
      action: cmd,
      modeLabel: human.modeLabel,
      humanTitle: human.title,
      humanSubtitle: human.subtitle,
      status: runJson?.status || acqJson?.status || "unknown",
      query: runJson?.query || acqJson?.requested_identity || {},
      paperCount,
      resultCount,
      savedPath: acqJson?.saved_pdf_path || runJson?.exported_save_dir || null,
      sourceStatuses: runJson?.source_status || runJson?.source_statuses || {}
    };
    runSummaryCache.set(dir, { sig, entry });
    return entry;
  });
}

function formatBibtexKey(paper, idx = 1) {
  const firstAuthor = (paper.authors?.[0]?.name || "author")
    .split(/\s+/)
    .pop()
    .replace(/[^A-Za-z0-9]/g, "")
    .toLowerCase();
  const year = paper.year || "nd";
  const firstWord = (paper.title || "paper")
    .split(/\s+/)[0]
    .replace(/[^A-Za-z0-9]/g, "")
    .toLowerCase();
  return `${firstAuthor || "paper"}${year}${firstWord || idx}`;
}

function generateCitations(papers) {
  const bibtexBlocks = [];
  const gbtLines = [];
  const apaLines = [];
  const usedKeys = new Set();
  const bibValue = (value) => String(value).replace(/[\r\n]+/g, " ").replace(/[{}]/g, (char) => `\\${char}`);

  (papers || []).forEach((p, idx) => {
    const baseKey = formatBibtexKey(p, idx + 1);
    let key = baseKey;
    for (let n = 2; usedKeys.has(key); n++) key = `${baseKey}${n}`;
    usedKeys.add(key);
    const authors = (p.authors || []).map((a) => a.name).filter(Boolean);
    const bibAuthors = authors.join(" and ") || "Unknown";
    const doi = p.identifiers?.doi?.[0] || "";
    const journal = unpackJournalAndTldr(p).journal;
    const year = p.year || "";
    const title = p.title || "Untitled";
    const url = p.clickable_links?.[0]?.url || (doi ? `https://doi.org/${doi}` : p.url || "");

    bibtexBlocks.push(
      [
        `@article{${key},`,
        `  title = {${bibValue(title)}},`,
        `  author = {${bibValue(bibAuthors)}},`,
        year ? `  year = {${bibValue(year)}},` : null,
        journal ? `  journal = {${bibValue(journal)}},` : null,
        doi ? `  doi = {${bibValue(doi)}},` : null,
        url ? `  url = {${bibValue(url)}}` : null,
        `}`
      ]
        .filter(Boolean)
        .join("\n")
    );

    const gbtAuthors =
      authors.length > 3 ? `${authors.slice(0, 3).join(", ")}, 等` : authors.join(", ") || "佚名";
    gbtLines.push(
      `[${idx + 1}] ${gbtAuthors}. ${title}[J]. ${journal || "Academic Venue"}${year ? `, ${year}` : ""}${doi ? `. DOI:${doi}` : ""}.`
    );

    const apaAuthors =
      authors.length > 5 ? `${authors.slice(0, 5).join(", ")}, et al.` : authors.join(", ") || "Unknown";
    apaLines.push(
      `${apaAuthors} (${year || "n.d."}). ${title}. *${journal || "Preprint / Venue"}*. ${doi ? `https://doi.org/${doi}` : url}`
    );
  });

  return {
    bibtex: bibtexBlocks.join("\n\n"),
    gbt7714: gbtLines.join("\n"),
    apa: apaLines.join("\n\n")
  };
}

function summarizeRunForModel(bundle) {
  const lines = [];
  const r = bundle.run || {};
  const human = r.humanSummary || {};
  lines.push(
    `### 三木文献检索结果：${human.title || bundle.runId}`,
    `- **检索模式**：${human.modeLabel || r.command || "search"} | **状态**：\`${r.status || "success"}\` | **论文数**：\`${bundle.papers?.length || 0}\` | **原始结果数**：\`${bundle.results?.length || 0}\``,
    `- **批次 ID**：\`${bundle.runId}\`（归档路径：\`${bundle.runDir}\`）`
  );
  if (r.exported_save_dir) {
    lines.push(`- **已同步保存至目录**：\`${r.exported_save_dir}\``);
  }
  const srcMap = r.source_status || r.source_statuses || {};
  if (Object.keys(srcMap).length) {
    const statusParts = Object.entries(srcMap).map(
      ([k, v]) =>
        `${k}: ${v.status}${v.error ? ` (${v.error})` : ""} [fetched=${v.fetched ?? 0}${v.truncated ? ", truncated" : ""}]${v.sort_scope === "retrieved_candidates" ? "（仅对已召回候选排序，并非全库排名）" : ""}`
    );
    lines.push(`- **数据源状态**：${statusParts.join(" | ")}`);
  }
  if (bundle.papers?.length) {
    lines.push("", "#### 候选论文列表（可在右侧「三木文献检索」面板查看完整详情、下载 PDF 或加入收藏篮）：");
    for (const p of bundle.papers.slice(0, 15)) {
      const doi = p.identifiers?.doi?.[0];
      const primaryLink = p.clickable_links?.[0]?.url;
      const titleMd = primaryLink ? `[${p.title}](${primaryLink})` : p.title;
      const authorsStr = (p.authors || [])
        .slice(0, 3)
        .map((a) => a.name)
        .join(", ");
      const rankBadges = p.venue_rank?.badges?.length ? ` | 等级: ${p.venue_rank.badges.join(" · ")}` : "";
      lines.push(
        `${p.index}. **${titleMd}** (${p.year || "n.d."}) — ${authorsStr || "Unknown"}${p.journal ? ` | *${p.journal}*` : ""}${rankBadges} | 进度: ${p.handoff_stage_label || p.handoff_stage}${doi ? ` | DOI: [${doi}](https://doi.org/${doi})` : ""}`
      );
    }
  }
  if (r.draw_result) {
    const dr = r.draw_result;
    lines.push("", `#### 🎨 AI4Scholar 科研绘图结果 (${dr.action})：`);
    if (dr.imageUrl) lines.push(`- **图片链接**：${dr.imageUrl}`, "", `![科研配图](${dr.imageUrl})`);
    if (dr.svgUrl) lines.push(`- **SVG 矢量链接**：${dr.svgUrl}`);
    if (dr.pdfUrl) lines.push(`- **矢量 PDF 链接**：${dr.pdfUrl}`);
    if (dr.pptxUrl) lines.push(`- **可编辑 PPTX 链接**：${dr.pptxUrl}`);
    if (dr.savedFiles && Object.keys(dr.savedFiles).length) {
      lines.push(`- **已下载本地文件**：\`${JSON.stringify(dr.savedFiles)}\``);
    }
    if (dr.optimizedPrompt) lines.push(`- **优化后 Prompt**：${dr.optimizedPrompt}`);
    if (dr.critique) lines.push("", "#### 专家审稿意见：", dr.critique);
  }
  if (r.auto_cite_result) {
    const ac = r.auto_cite_result;
    lines.push("", "#### 📝 Auto-Cite 自动引文标注结果：", "", ac.annotatedText || "");
    if (Array.isArray(ac.references) && ac.references.length) {
      lines.push("", "#### 匹配参考文献：", ...ac.references.map((ref, idx) => `[${ref.number || idx + 1}] ${ref.formatted || ref.title}`));
    }
  }
  if (r.credits_balance) {
    const cb = r.credits_balance;
    lines.push(
      "",
      `#### 💰 AI4Scholar 积分余额：可用 **${cb.total_available ?? 0}**（永久积分: ${cb.permanent ?? 0}，会员月度剩余: ${cb.member_monthly_remaining ?? 0}，当前 Key 累计消耗: ${cb.key_credits_used ?? 0}）`
    );
  }
  if (r.gs_citations) {
    const gc = r.gs_citations;
    if (Array.isArray(gc.citations) && gc.citations.length) {
      lines.push("", "#### 📋 Google Scholar 标准引用格式：");
      for (const c of gc.citations) {
        lines.push(`- **${c.title || "格式"}**：${c.snippet || ""}`);
      }
    }
    if (Array.isArray(gc.links) && gc.links.length) {
      lines.push(
        "- **导出链接**：" + gc.links.map((l) => `[${l.name || "Link"}](${l.link})`).join(" | ")
      );
    }
  }
  if (r.sciverse_content) {
    const sc = r.sciverse_content;
    lines.push(
      "",
      `#### 📖 SciVerse 论文原文 Markdown 切片 (doc_id: \`${sc.doc_id}\`, offset: \`${sc.offset}\`, 返回字符: \`${sc.chars_returned}\`, 下一偏移: \`${sc.next_offset ?? "末尾"}\`)：`
    );
    if (Array.isArray(sc.downloaded_figures) && sc.downloaded_figures.length) {
      lines.push(
        `- **已提取下载原图 (${sc.downloaded_figures.length} 张)**：` +
          sc.downloaded_figures.map((f) => `\`${f.saved_path || f.local_path}\``).join(", ")
      );
    }
    if (sc.text) {
      lines.push("", "```markdown", String(sc.text).slice(0, 2400) + (String(sc.text).length > 2400 ? "\n...(更多内容见 fulltext_excerpt.md 或右侧面板)" : ""), "```");
    }
  }
  if (r.sciverse_materials && Array.isArray(r.sciverse_materials.items) && r.sciverse_materials.items.length) {
    lines.push("", `#### 🧬 SciVerse Paper Schema 结构化研究材料包 (goal: \`${r.sciverse_materials.goal}\`)：`);
    for (const mItem of r.sciverse_materials.items.slice(0, 5)) {
      const sp = mItem.schema_paper || {};
      lines.push(
        `- **${sp.title || mItem.schema_id}** (\`${mItem.schema_id}\`, ${sp.year || "n.d."})`,
        ...(sp.research_problem ? [`  - **研究问题**：${sp.research_problem}`] : []),
        ...(sp.central_contribution ? [`  - **核心贡献**：${sp.central_contribution}`] : []),
        ...(sp.headline_result ? [`  - **主要结果**：${sp.headline_result}`] : []),
        `  - **结构对象 (Entities)**：${(mItem.entities || []).length} 项 | **逻辑关系 (Relations)**：${(mItem.relations || []).length} 条 | **结构证据 (Evidence)**：${(mItem.evidence || []).length} 条`
      );
    }
  }
  if (Array.isArray(r.sciverse_evidence) && r.sciverse_evidence.length) {
    lines.push("", `#### 📐 SciVerse 结构化证据命中（共 ${r.sciverse_evidence.length} 条）：`);
    for (const ev of r.sciverse_evidence.slice(0, 10)) {
      lines.push(
        `- **[${(ev.groups || []).join(",")}] \`${ev.key || "value"}\`** (${ev.paper_title || ev.schema_id})：${String(ev.value_text || "").slice(0, 220)}`
      );
    }
  }
  if (r.sciverse_in_paper && Array.isArray(r.sciverse_in_paper.segments) && r.sciverse_in_paper.segments.length) {
    lines.push("", `#### 🔎 SciVerse 单篇正文段落检索 (\`${r.sciverse_in_paper.schema_id}\`, 共 ${r.sciverse_in_paper.segments.length} 段)：`);
    for (const seg of r.sciverse_in_paper.segments.slice(0, 8)) {
      lines.push(`- **${seg.marker || seg.paragraph_id}** (${(seg.section_path || []).join(" / ")})：${String(seg.text || "").slice(0, 240)}`);
    }
  }
  if (bundle.results?.length && (!bundle.papers || bundle.papers.length === 0)) {
    lines.push("", `#### 检索结果条目（共 ${bundle.results.length} 条）：`);
    for (const item of bundle.results.slice(0, 15)) {
      const title = item.title || item.id || "Result";
      const link = item.link || item.url || "";
      const metaParts = [
        item.year ? `${item.year}` : "",
        item.publicationInfo || item.publicationNumber || "",
        item.citedBy !== undefined ? `被引:${item.citedBy}` : "",
        item.extracted_dois?.length ? `DOI:${item.extracted_dois[0]}` : ""
      ].filter(Boolean);
      lines.push(
        `${item.index}. ${link ? `**[${title}](${link})**` : `**${title}**`}${metaParts.length ? ` (${metaParts.join(" · ")})` : ""}${item.snippet ? `\n   > ${String(item.snippet).slice(0, 160)}` : ""}`
      );
    }
  }
  return lines.join("\n");
}

function openFileInSystem(fullPath) {
  if (!existsSync(fullPath)) return false;
  try {
    if (process.platform === "win32") {
      const child = spawn("explorer.exe", [fullPath], {
        detached: true,
        stdio: "ignore",
        windowsHide: false
      });
      child.unref();
      return true;
    }
    const opener = process.platform === "darwin" ? "open" : "xdg-open";
    const child = spawn(opener, [fullPath], { detached: true, stdio: "ignore" });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

function openZoteroUriInSystem(uri) {
  if (!uri || !uri.startsWith("zotero://")) return false;
  try {
    if (process.platform === "win32") {
      const child = spawn("explorer.exe", [uri], {
        detached: true,
        stdio: "ignore",
        windowsHide: true
      });
      child.unref();
      return true;
    }
    const opener = process.platform === "darwin" ? "open" : "xdg-open";
    const child = spawn(opener, [uri], { detached: true, stdio: "ignore" });
    child.unref();
    return true;
  } catch {
    return false;
  }
}

function copyTextToSystemClipboard(rawText) {
  const text = String(rawText ?? "");
  if (!text) return false;
  try {
    if (process.platform === "win32") {
      const utf16Bom = Buffer.concat([Buffer.from([0xff, 0xfe]), Buffer.from(text, "utf16le")]);
      const res = spawnSync("clip.exe", [], {
        input: utf16Bom,
        windowsHide: true,
        timeout: 4000
      });
      if (res.status === 0) return true;
    } else if (process.platform === "darwin") {
      const res = spawnSync("pbcopy", [], {
        input: Buffer.from(text, "utf8"),
        timeout: 4000
      });
      if (res.status === 0) return true;
    } else {
      for (const [cmd, args] of [
        ["wl-copy", []],
        ["xclip", ["-selection", "clipboard"]],
        ["xsel", ["--clipboard", "--input"]]
      ]) {
        const res = spawnSync(cmd, args, {
          input: Buffer.from(text, "utf8"),
          timeout: 4000
        });
        if (res.status === 0) return true;
      }
    }
  } catch {
    // Ignore clipboard helper errors
  }
  return false;
}

async function enrichPaperAbstractAndMetadata(paper) {
  const seedId = paper?.resolvable_seed_id || paper?.identifiers?.doi?.[0] || "";
  const titleQuery = String(paper?.title || "").trim();
  const tempOut = join(RUNS_DIR, `.enrich-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`);
  let bestPaper = null;

  try {
    if (seedId) {
      const allSrcs = pickCompatibleSources(seedId, true);
      const fastSrcs = allSrcs.filter((s) => s !== "semantic-scholar");
      if (fastSrcs.length > 0) {
        await runPython(PAPER_SEARCH_PY, ["lookup", seedId, "--sources", fastSrcs.join(","), "--out", tempOut], 35000);
        const found = readJsonSafe(join(tempOut, "papers.json"), []);
        if (Array.isArray(found)) bestPaper = found.find((candidate) => samePaperIdentity(paper, candidate)) || null;
        rmSync(tempOut, { recursive: true, force: true });
      }
      if ((!bestPaper || !bestPaper.abstract) && allSrcs.includes("semantic-scholar")) {
        await runPython(PAPER_SEARCH_PY, ["lookup", seedId, "--sources", "semantic-scholar", "--out", tempOut], 35000);
        const s2Found = readJsonSafe(join(tempOut, "papers.json"), []).filter((candidate) => samePaperIdentity(paper, candidate));
        if (Array.isArray(s2Found) && s2Found[0]) {
          bestPaper = bestPaper
            ? {
                ...bestPaper,
                abstract: bestPaper.abstract || s2Found[0].abstract || null,
                tldr: bestPaper.tldr || s2Found[0].tldr || null,
                oa_locations: [...(bestPaper.oa_locations || []), ...(s2Found[0].oa_locations || [])],
                identifiers: { ...(bestPaper.identifiers || {}), ...(s2Found[0].identifiers || {}) }
              }
            : s2Found[0];
        }
        rmSync(tempOut, { recursive: true, force: true });
      }
    }

    // If still no abstract, try Europe PMC by DOI/ID or title
    if ((!bestPaper || !bestPaper.abstract) && (seedId || titleQuery)) {
      const epmcOut = `${tempOut}-epmc`;
      const epmcArgs = ["epmc", "--limit", "3", "--out", epmcOut];
      if (seedId && /^(10\.\d{4,9}\/|pmid:|pmcid:|PMC\d+)/i.test(seedId)) {
        epmcArgs.push("--id", seedId);
      } else if (titleQuery) {
        epmcArgs.push(titleQuery);
      }
      await runPython(PAPER_SEARCH_PY, epmcArgs, 35000);
      const epmcFound = readJsonSafe(join(epmcOut, "papers.json"), []);
      if (Array.isArray(epmcFound) && epmcFound.length > 0) {
        const match = epmcFound.find((candidate) => samePaperIdentity(paper, candidate));
        if (match) {
          bestPaper = bestPaper
            ? {
                ...bestPaper,
                abstract: bestPaper.abstract || match.abstract || null,
                oa_locations: [...(bestPaper.oa_locations || []), ...(match.oa_locations || [])],
                identifiers: { ...(bestPaper.identifiers || {}), ...(match.identifiers || {}) }
              }
            : match;
        }
      }
      rmSync(epmcOut, { recursive: true, force: true });
    }

    // If still no abstract and SciVerse is configured, try SciVerse meta-search by DOI or title
    if ((!bestPaper || !bestPaper.abstract) && getEffectiveConfig().status.sciverse_api_key?.configured && (seedId || titleQuery)) {
      const svOut = `${tempOut}-sciverse`;
      const svArgs = ["sciverse", "--mode", "meta-search", "--limit", "3", "--out", svOut];
      if (seedId && /^10\.\d{4,9}\//i.test(seedId)) {
        svArgs.push("--id", seedId);
      } else if (titleQuery) {
        svArgs.push(titleQuery);
      }
      await runPython(PAPER_SEARCH_PY, svArgs, 35000);
      const svFound = readJsonSafe(join(svOut, "papers.json"), []);
      if (Array.isArray(svFound) && svFound.length > 0) {
        const match = svFound.find((candidate) => samePaperIdentity(paper, candidate));
        if (match) {
          bestPaper = bestPaper
            ? {
                ...bestPaper,
                abstract: bestPaper.abstract || match.abstract || null,
                sciverse_doc_id: bestPaper.sciverse_doc_id || match.sciverse_doc_id || null,
                sciverse_unique_id: bestPaper.sciverse_unique_id || match.sciverse_unique_id || null,
                fwci: bestPaper.fwci ?? match.fwci ?? null,
                oa_locations: [...(bestPaper.oa_locations || []), ...(match.oa_locations || [])],
                identifiers: { ...(bestPaper.identifiers || {}), ...(match.identifiers || {}) }
              }
            : match;
        }
      }
      rmSync(svOut, { recursive: true, force: true });
    }

    // If still no abstract and we only had a title (e.g. Google Scholar item without DOI), search OpenAlex + S2
    if ((!bestPaper || !bestPaper.abstract) && !seedId && titleQuery) {
      const srchOut = `${tempOut}-srch`;
      await runPython(
        PAPER_SEARCH_PY,
        ["search", titleQuery, "--sources", "openalex,semantic-scholar", "--limit", "3", "--out", srchOut],
        45000
      );
      const srchFound = readJsonSafe(join(srchOut, "papers.json"), []);
      if (Array.isArray(srchFound) && srchFound.length > 0) {
        const match = srchFound.find((candidate) => samePaperIdentity(paper, candidate));
        if (match) bestPaper = match;
      }
      rmSync(srchOut, { recursive: true, force: true });
    }
  } finally {
    try {
      if (existsSync(tempOut)) rmSync(tempOut, { recursive: true, force: true });
    } catch {}
  }

  if (!bestPaper) {
    throw new Error("跨源查询未找到更多题录或摘要信息（可能该出版商未公开摘要元数据）。");
  }

  const unpacked = unpackJournalAndTldr(bestPaper);
  const enriched = {
    ...(bestPaper.abstract ? { abstract: bestPaper.abstract, abstract_available: true, evidence_level: "abstract" } : {}),
    ...(unpacked.tldr ? { tldr: unpacked.tldr } : {}),
    ...(unpacked.journal ? { journal: unpacked.journal, venue: unpacked.venue } : {}),
    ...(bestPaper.citation_count !== undefined && bestPaper.citation_count !== null
      ? { citation_count: bestPaper.citation_count }
      : {}),
    ...(bestPaper.reference_count !== undefined && bestPaper.reference_count !== null
      ? { reference_count: bestPaper.reference_count }
      : {}),
    ...(bestPaper.authors?.length ? { authors: bestPaper.authors } : {}),
    identifiers: bestPaper.identifiers || {},
    oa_locations: bestPaper.oa_locations || []
  };

  return enriched;
}

function normalizeGraphNodeId(idOrKey) {
  const s = String(idOrKey || "").trim();
  if (!s) return "";
  const mDoi = s.match(/10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/);
  if (mDoi) return `doi:${mDoi[0].toLowerCase()}`;
  if (/^openalex:/i.test(s)) return `openalex:${s.replace(/^openalex:/i, "").toUpperCase()}`;
  if (/^W\d+$/i.test(s)) return `openalex:${s.toUpperCase()}`;
  if (/^(s2:|semantic-scholar:)/i.test(s)) return `s2:${s.replace(/^(s2:|semantic-scholar:)/i, "").toLowerCase()}`;
  return s.toLowerCase();
}

function findKnownPaperByAnyId(rawId, state) {
  const normTarget = normalizeGraphNodeId(rawId);
  const cleanRaw = String(rawId || "").trim().toLowerCase();
  const matchPaper = (p) => {
    if (!p) return false;
    const pk = normalizeGraphNodeId(p.paper_key || computePaperKey(p));
    if (pk && pk === normTarget) return true;
    const ids = p.identifiers || {};
    for (const d of ids.doi || []) {
      if (`doi:${String(d).toLowerCase()}` === normTarget || String(d).toLowerCase() === cleanRaw) return true;
    }
    for (const oa of ids.openalex || []) {
      const cleanOa = String(oa).replace(/^https?:\/\/openalex\.org\//i, "").toUpperCase();
      if (`openalex:${cleanOa}` === normTarget) return true;
    }
    for (const s2 of ids.semantic_scholar || []) {
      if (`s2:${String(s2).toLowerCase()}` === normTarget || String(s2).toLowerCase() === cleanRaw) return true;
    }
    for (const arx of ids.arxiv || []) {
      if (`arxiv:${String(arx).toLowerCase()}` === normTarget || String(arx).toLowerCase() === cleanRaw) return true;
    }
    return false;
  };

  for (const bp of state?.basket || []) {
    if (matchPaper(bp)) return bp;
  }
  const recent = listRecentRuns(18);
  for (const rMeta of recent) {
    const rPapers = readJsonSafe(join(RUNS_DIR, basename(rMeta.runId), "papers.json"), []);
    if (Array.isArray(rPapers)) {
      for (const rp of rPapers) {
        if (matchPaper(rp)) return rp;
      }
    }
  }
  return null;
}

function resolveBestSeedForRelated(rawId, state) {
  const clean = String(rawId || "").trim();
  const known = findKnownPaperByAnyId(clean, state);
  if (known) {
    const doi = known.identifiers?.doi?.[0];
    if (doi) return { resolvedId: String(doi), knownPaper: known };
    const oa = known.identifiers?.openalex?.[0];
    if (oa && /^s2:/i.test(clean)) {
      return {
        resolvedId: `openalex:${String(oa).replace(/^https?:\/\/openalex\.org\//i, "")}`,
        knownPaper: known
      };
    }
  }
  return { resolvedId: clean, knownPaper: known };
}

function inspectPdfBufferNode(buf) {
  if (!Buffer.isBuffer(buf) || buf.length < 16) return null;
  const head = buf.subarray(0, Math.min(1024, buf.length)).toString("latin1");
  const tail = buf.subarray(Math.max(0, buf.length - 65536)).toString("latin1");
  if (!head.includes("%PDF-") || !tail.includes("%%EOF")) return null;
  return { header: true, trailer: true, bytes: buf.length };
}

async function claimBrowserPdf(input = {}) {
  const cleanDoi = input.doi ? String(input.doi).trim() : "";
  const cleanTitle = input.title ? String(input.title).trim() : "";
  const destDir = resolveTargetSaveDir(input.saveDir);
  const customDownloadDir = resolveEnvPath(process.env.SANMU_BROWSER_DOWNLOAD_DIR);
  const scanDirs = [
    ...(customDownloadDir ? [resolve(customDownloadDir)] : [join(homedir(), "Downloads")]),
    destDir
  ];

  const nowMs = Date.now();
  const maxAgeMs = 30 * 60 * 1000;
  const seenPaths = new Set();
  const rawCandidates = [];

  for (const dir of scanDirs) {
    try {
      if (!existsSync(dir) || !statSync(dir).isDirectory()) continue;
      for (const entry of readdirSync(dir, { withFileTypes: true })) {
        if (!entry.isFile() || extname(entry.name).toLowerCase() !== ".pdf") continue;
        const fullPath = resolve(dir, entry.name);
        if (seenPaths.has(fullPath)) continue;
        seenPaths.add(fullPath);
        const st = statSync(fullPath);
        if (nowMs - st.mtimeMs <= maxAgeMs && st.size > 0 && st.size <= 120 * 1024 * 1024) {
          rawCandidates.push({ fullPath, name: entry.name, mtimeMs: st.mtimeMs, size: st.size });
        }
      }
    } catch {
      // Ignore inaccessible directories
    }
  }

  rawCandidates.sort((a, b) => b.mtimeMs - a.mtimeMs);
  const candidates = rawCandidates.slice(0, 10);
  if (candidates.length === 0) {
    throw new Error(
      "未在系统下载目录 (Downloads) 或保存目录中发现最近 30 分钟内新下载的 PDF 文件。请先在浏览器中下载 PDF，完成后再点击「📥 认领浏览器刚下载的 PDF」。"
    );
  }

  let selected = null;
  const validCandidates = [];

  for (const cand of candidates) {
    let buf;
    try {
      buf = readFileSync(cand.fullPath);
    } catch {
      continue;
    }
    const nodeChecks = inspectPdfBufferNode(buf);
    if (!nodeChecks) continue;

    const sha256 = createHash("sha256").update(buf).digest("hex");
    const pyArgs = ["--check-local", cand.fullPath];
    if (cleanDoi) pyArgs.push("--doi", cleanDoi);
    if (cleanTitle) pyArgs.push("--title", cleanTitle);
    const pyRes = await runPython(FETCH_PDF_PY, pyArgs, 25000);
    const pyIdentity = pyRes.exitCode === 0 ? pyRes.parsed?.identity : null;
    // PDF metadata and later-page streams are not evidence about the first page.
    const identity = { ...(pyIdentity || {}), status: pyIdentity?.status === "matched" ? "matched" : "needs_review" };

    const enrichedCand = {
      ...cand,
      sha256,
      checks: pyRes.parsed?.format_checks || nodeChecks,
      identity,
      hasConflictingDoi: Boolean(pyIdentity?.has_conflicting_doi)
    };
    validCandidates.push(enrichedCand);

    if (identity.status === "matched") {
      selected = enrichedCand;
      break;
    }
  }

  if (!selected) {
    const recent3MinValid = validCandidates.filter(
      (c) => nowMs - c.mtimeMs <= 3 * 60 * 1000 && !c.hasConflictingDoi
    );
    if (recent3MinValid.length === 1) {
      selected = {
        ...recent3MinValid[0],
        identity: {
          ...(recent3MinValid[0].identity || {}),
          status: "needs_review",
          reason: "claimed_unique_recent_download"
        }
      };
    }
  }

  if (!selected) {
    throw new Error(
      `已扫描最近 30 分钟内的 ${candidates.length} 个候选 PDF 文件，但未找到首屏匹配目标论文 DOI/标题的 PDF（且无最近 3 分钟内唯一新下载的无冲突 PDF）。请确认已在浏览器中完成目标文献下载。`
    );
  }

  const runId = makeRunId("claim-pdf");
  const outDir = join(RUNS_DIR, runId);
  mkdirSync(outDir, { recursive: true });

  const fileStem = makeSafeFileStem(input.year, input.title, input.doi || runId, input.doi || input.paperKey || "");
  const shortSuffix = String(input.doi || input.paperKey || selected.sha256 || "")
    .replace(/^(?:doi:|openalex:|s2:|pmid:|pmcid:|arxiv:)/i, "")
    .replace(/^10\.\d{4,9}\//i, "")
    .replace(/[^A-Za-z0-9_-]+/g, "")
    .slice(-8);
  const desiredFilename = shortSuffix && !fileStem.endsWith(`-${shortSuffix}`)
    ? `${fileStem}-${shortSuffix}.pdf`
    : `${fileStem}.pdf`;

  let savedPdfPath;
  if (resolve(dirname(selected.fullPath)) === resolve(destDir) && basename(selected.fullPath) === desiredFilename) {
    savedPdfPath = selected.fullPath;
  } else {
    savedPdfPath = uniqueDestination(destDir, desiredFilename, shortSuffix || runId);
    copyFileSync(selected.fullPath, savedPdfPath, fsConstants.COPYFILE_EXCL);
  }
  copyFileSync(savedPdfPath, join(outDir, "paper.pdf"));

  const identityStatus = selected.identity?.status === "matched" ? "matched" : "needs_review";
  const acq = {
    schema_version: "1.0",
    retrieved_at: new Date().toISOString(),
    status: "file_obtained",
    claimed_from_browser: true,
    claimed_source_path: selected.fullPath,
    saved_pdf_path: savedPdfPath,
    saved_dir: destDir,
    file: "paper.pdf",
    sha256: selected.sha256,
    format_checks: selected.checks,
    identity: { ...(selected.identity || {}), status: identityStatus },
    identity_status: identityStatus,
    requested_identity: { doi: cleanDoi || null, title: cleanTitle || null },
    read: false
  };
  writeJsonSafe(join(outDir, "acquisition.json"), acq);

  const key = input.paperKey || (cleanDoi ? `doi:${cleanDoi.toLowerCase()}` : null);
  updateStateTransaction((state) => {
    if (key) {
      const prev = state.evidenceMap[key] || {};
      state.evidenceMap[key] = {
        ...prev,
        stage: prev.stage === "read" ? "read" : identityStatus === "matched" ? "identity_checked" : "file_obtained",
        pdfPath: savedPdfPath,
        acquisition: acq,
        updatedAt: new Date().toISOString()
      };
    }
    if (key && Array.isArray(state.basket)) {
      state.basket = state.basket.map((bp) =>
        (bp.paper_key || computePaperKey(bp)) === key
          ? { ...bp, local_pdf_path: savedPdfPath }
          : bp
      );
    }
  });

  const panelState = await getFullPanelState();
  return {
    text: `📥 已成功认领浏览器刚下载的 PDF 并归档至：\`${savedPdfPath}\`\n- 首屏身份核验: \`${identityStatus}\`（原文件：\`${selected.name}\`）`,
    structured: {
      ...panelState,
      lastDownload: {
        runId,
        outDir,
        pdfPath: savedPdfPath,
        savedDir: destDir,
        claimedFrom: selected.fullPath,
        identity_status: identityStatus,
        acquisition: acq
      }
    }
  };
}

function buildRelationshipGraph(activeBundle, recentRuns, basket, state, zoteroMatches = {}, sharedCtx = null) {
  const ctx = sharedCtx || createEnrichContext(state);
  const nodeMap = new Map();
  const aliasToNodeId = new Map();
  const nodeSourcesMap = new Map();
  const edges = [];
  const edgeKeys = new Set();

  const recordNodeSource = (nodeId, sourceKey) => {
    if (!nodeId || !sourceKey) return;
    if (!nodeSourcesMap.has(nodeId)) nodeSourcesMap.set(nodeId, new Set());
    nodeSourcesMap.get(nodeId).add(String(sourceKey));
  };

  const registerAliases = (nodeId, paper) => {
    aliasToNodeId.set(nodeId, nodeId);
    const ids = paper?.identifiers || {};
    for (const d of ids.doi || []) aliasToNodeId.set(`doi:${String(d).toLowerCase()}`, nodeId);
    for (const oa of ids.openalex || []) {
      const clean = String(oa).replace(/^https?:\/\/openalex\.org\//i, "").toUpperCase();
      aliasToNodeId.set(`openalex:${clean}`, nodeId);
    }
    for (const s2 of ids.semantic_scholar || []) {
      aliasToNodeId.set(`s2:${String(s2).toLowerCase()}`, nodeId);
    }
    if (paper?.resolvable_seed_id) {
      aliasToNodeId.set(normalizeGraphNodeId(paper.resolvable_seed_id), nodeId);
    }
  };

  const upsertPaperNode = (paper, role = "search_hit", priority = 1, sourceTag = "") => {
    if (!paper) return null;
    const rawKey = paper.paper_key || computePaperKey(paper);
    if (paper.handoff_stage === "excluded" || state?.evidenceMap?.[rawKey]?.stage === "excluded") {
      return null;
    }
    const normKey = normalizeGraphNodeId(rawKey);
    const existingId =
      aliasToNodeId.get(normKey) ||
      (paper.identifiers?.doi?.[0] ? aliasToNodeId.get(`doi:${String(paper.identifiers.doi[0]).toLowerCase()}`) : null) ||
      normKey;

    const firstAuthor = paper.authors?.[0]?.name || "Unknown";
    const shortAuthor = firstAuthor.split(/\s+/).pop() || firstAuthor.slice(0, 8);
    const shortLabel = `${shortAuthor}${paper.year ? ` (${paper.year})` : ""}`;
    const normalizedPaper = { ...paper, paper_key: rawKey };

    if (nodeMap.has(existingId)) {
      const existing = nodeMap.get(existingId);
      if (priority > existing.priority) {
        existing.role = role;
        existing.priority = priority;
      }
      const existingIsStub =
        !existing.paper?.title ||
        String(existing.paper.title).startsWith("种子文献 (") ||
        existing.firstAuthor === "Seed Paper" ||
        existing.firstAuthor === "Unknown";
      const incomingIsReal =
        Boolean(normalizedPaper.title && !String(normalizedPaper.title).startsWith("种子文献 ("));

      if (existingIsStub && incomingIsReal) {
        existing.title = normalizedPaper.title;
        existing.shortLabel = shortLabel;
        existing.year = normalizedPaper.year || existing.year;
        existing.journal = normalizedPaper.journal || normalizedPaper.venue || existing.journal;
        existing.firstAuthor = firstAuthor;
        existing.citationCount = normalizedPaper.citation_count ?? existing.citationCount;
        existing.paper = {
          ...existing.paper,
          ...normalizedPaper,
          in_basket: Boolean(existing.inBasket || normalizedPaper.in_basket),
          zotero_match: normalizedPaper.zotero_match || existing.paper?.zotero_match || null,
          local_pdf_path: normalizedPaper.local_pdf_path || existing.paper?.local_pdf_path || null
        };
      } else {
        existing.paper = {
          ...normalizedPaper,
          ...existing.paper,
          paper_key: existing.paper?.paper_key || rawKey,
          abstract: existing.paper?.abstract || normalizedPaper.abstract || null,
          abstract_available: Boolean(existing.paper?.abstract || normalizedPaper.abstract),
          tldr: existing.paper?.tldr || normalizedPaper.tldr || null,
          journal: existing.paper?.journal || normalizedPaper.journal || "",
          venue: existing.paper?.venue || normalizedPaper.venue || "",
          authors: existing.paper?.authors?.length ? existing.paper.authors : normalizedPaper.authors || [],
          clickable_links: existing.paper?.clickable_links?.length ? existing.paper.clickable_links : normalizedPaper.clickable_links || [],
          candidate_pdf_urls: existing.paper?.candidate_pdf_urls?.length ? existing.paper.candidate_pdf_urls : normalizedPaper.candidate_pdf_urls || [],
          primary_pdf_url: existing.paper?.primary_pdf_url || normalizedPaper.primary_pdf_url || null,
          oa_locations: existing.paper?.oa_locations?.length ? existing.paper.oa_locations : normalizedPaper.oa_locations || [],
          in_basket: Boolean(existing.inBasket || normalizedPaper.in_basket),
          zotero_match: existing.paper?.zotero_match || normalizedPaper.zotero_match || null,
          local_pdf_path: existing.paper?.local_pdf_path || normalizedPaper.local_pdf_path || null
        };
      }
      if (normalizedPaper.in_basket) existing.inBasket = true;
      if (normalizedPaper.zotero_match?.matched) existing.inZotero = true;
      if (normalizedPaper.local_pdf_path) existing.hasPdf = true;
      registerAliases(existingId, existing.paper);
      if (sourceTag) recordNodeSource(existingId, sourceTag);
      return existingId;
    }

    if (nodeMap.size >= 80) return null;

    const node = {
      id: existingId,
      paperKey: rawKey,
      seedId: normalizedPaper.resolvable_seed_id || normalizedPaper.identifiers?.doi?.[0] || "",
      title: normalizedPaper.title || existingId,
      shortLabel,
      year: normalizedPaper.year || null,
      journal: normalizedPaper.journal || normalizedPaper.venue || "",
      firstAuthor,
      citationCount: normalizedPaper.citation_count ?? 0,
      role,
      priority,
      inBasket: Boolean(normalizedPaper.in_basket),
      inZotero: Boolean(normalizedPaper.zotero_match?.matched || zoteroMatches[rawKey]?.matched),
      hasPdf: Boolean(normalizedPaper.local_pdf_path),
      paper: normalizedPaper
    };
    nodeMap.set(existingId, node);
    registerAliases(existingId, normalizedPaper);
    if (sourceTag) recordNodeSource(existingId, sourceTag);
    return existingId;
  };

  const addEdge = (source, target, type, label = "") => {
    if (!source || !target || source === target) return;
    if (!nodeMap.has(source) || !nodeMap.has(target)) return;
    const directedKey = type === "cites" ? `${source}->${target}:${type}` : [source, target].sort().join("<->") + `:${type}`;
    if (edgeKeys.has(directedKey)) return;
    edgeKeys.add(directedKey);
    edges.push({ source, target, type, label });
  };

  // Pre-scan recent lookup/search runs and basket to build a lookup table of known seed papers
  const knownSeedPapers = new Map();
  for (const bp of basket || []) {
    const k = normalizeGraphNodeId(bp.paper_key || computePaperKey(bp));
    knownSeedPapers.set(k, bp);
    if (bp.identifiers?.doi?.[0]) knownSeedPapers.set(`doi:${String(bp.identifiers.doi[0]).toLowerCase()}`, bp);
  }
  for (const rMeta of (recentRuns || []).slice(0, 15)) {
    const rPapers = readJsonSafe(join(RUNS_DIR, basename(rMeta.runId), "papers.json"), []);
    if (Array.isArray(rPapers)) {
      const enriched = enrichPapersList(rPapers.slice(0, 15), state, zoteroMatches, null, ctx);
      for (const ep of enriched) {
        const k = normalizeGraphNodeId(ep.paper_key);
        if (!knownSeedPapers.has(k)) knownSeedPapers.set(k, ep);
        for (const d of ep.identifiers?.doi || []) {
          const dk = `doi:${String(d).toLowerCase()}`;
          if (!knownSeedPapers.has(dk)) knownSeedPapers.set(dk, ep);
        }
      }
    }
  }

  let centerNodeId = null;

  // 1. Populate from activeBundle
  if (activeBundle) {
    const cmd = activeBundle.run?.command || activeBundle.run?.action || "search";
    const q = activeBundle.run?.query || {};
    const activeRunTag = `run:${activeBundle.runId || "active"}`;
    const activePapers =
      activeBundle.papers?.length > 0
        ? activeBundle.papers
        : (activeBundle.results || []).map((r) => r.candidate_paper).filter(Boolean);

    if (cmd === "related" && q.id) {
      const normSeed = normalizeGraphNodeId(q.id);
      const knownSeed =
        knownSeedPapers.get(normSeed) ||
        knownSeedPapers.get(`doi:${String(q.id).toLowerCase()}`) || {
          title: `种子文献 (${q.id})`,
          year: null,
          identifiers: q.id.startsWith("10.") ? { doi: [q.id.toLowerCase()] } : {},
          authors: [{ name: "Seed Paper" }],
          resolvable_seed_id: q.id,
          paper_key: normSeed
        };
      centerNodeId = upsertPaperNode(knownSeed, "seed", 10, `seed:${normSeed}`);
    }

    for (const p of activePapers) {
      const allRels = (p.relations || []).filter((r) => r && ["references", "citations", "recommendations"].includes(r.type));
      const rel = allRels[0] || null;
      const role =
        rel?.type === "references"
          ? "reference"
          : rel?.type === "citations"
            ? "citation"
            : rel?.type === "recommendations"
              ? "recommendation"
              : cmd === "lookup"
                ? "seed"
                : "search_hit";
      const prio = role === "seed" ? 10 : rel ? 5 : 2;
      const nid = upsertPaperNode(p, role, prio, activeRunTag);
      if (!centerNodeId && role === "seed") centerNodeId = nid;

      for (const rItem of allRels) {
        if (!nid || !rItem?.seed) continue;
        const seedNorm = normalizeGraphNodeId(rItem.seed);
        let seedNodeId = aliasToNodeId.get(seedNorm);
        if (!seedNodeId) {
          seedNodeId = upsertPaperNode(
            knownSeedPapers.get(seedNorm) || {
              title: `种子文献 (${rItem.seed})`,
              year: null,
              identifiers: String(rItem.seed).startsWith("10.") ? { doi: [String(rItem.seed).toLowerCase()] } : {},
              authors: [{ name: "Seed Paper" }],
              resolvable_seed_id: rItem.seed,
              paper_key: seedNorm
            },
            "seed",
            9,
            `seed:${seedNorm}`
          );
        }
        if (seedNodeId) {
          recordNodeSource(nid, `seed:${seedNodeId}`);
          if (rItem.type === "references") {
            addEdge(seedNodeId, nid, "cites", "引用参考文献");
          } else if (rItem.type === "citations") {
            addEdge(nid, seedNodeId, "cites", "施引");
          } else if (rItem.type === "recommendations") {
            addEdge(seedNodeId, nid, "similar", "相似推荐");
          }
        }
      }
    }
  }

  // 2. Also include other related runs that share the same seed (or multi-hop seeds / bridge papers) so references + citations + recommendations appear together!
  const relatedRuns = (recentRuns || [])
    .slice(0, 15)
    .filter((rMeta) => rMeta.runId !== activeBundle?.runId && rMeta.action === "related" && rMeta.query?.id);
  const integratedRunIds = new Set();
  for (let pass = 0; pass < 3; pass++) {
    let addedInPass = false;
    for (const rMeta of relatedRuns) {
      if (integratedRunIds.has(rMeta.runId)) continue;
      const qId = rMeta.query.id;
      const normSeed = normalizeGraphNodeId(qId);
      const matchedSeedNodeId = aliasToNodeId.get(normSeed);
      const rPapersRaw = readJsonSafe(join(RUNS_DIR, basename(rMeta.runId), "papers.json"), []);
      const sharesBridgePaper =
        !matchedSeedNodeId &&
        nodeMap.size > 0 &&
        Array.isArray(rPapersRaw) &&
        rPapersRaw.slice(0, 12).some((rp) => {
          const pk = normalizeGraphNodeId(rp?.paper_key || computePaperKey(rp));
          const doiKey = rp?.identifiers?.doi?.[0] ? `doi:${String(rp.identifiers.doi[0]).toLowerCase()}` : "";
          return (pk && aliasToNodeId.has(pk)) || (doiKey && aliasToNodeId.has(doiKey));
        });
      if (!matchedSeedNodeId && !sharesBridgePaper && nodeMap.size > 0) continue;

      integratedRunIds.add(rMeta.runId);
      addedInPass = true;
      const seedNid =
        matchedSeedNodeId ||
        upsertPaperNode(
          knownSeedPapers.get(normSeed) || {
            title: `种子文献 (${qId})`,
            year: null,
            identifiers: qId.startsWith("10.") ? { doi: [qId.toLowerCase()] } : {},
            authors: [{ name: "Seed Paper" }],
            resolvable_seed_id: qId,
            paper_key: normSeed
          },
          "seed",
          9,
          `seed:${normSeed}`
        );
      if (!centerNodeId) centerNodeId = seedNid;

      const rPapers = enrichPapersList((Array.isArray(rPapersRaw) ? rPapersRaw : []).slice(0, 12), state, zoteroMatches, null, ctx);
      for (const p of rPapers) {
        const relType = rMeta.query?.relation || "references";
        const role =
          relType === "references"
            ? "reference"
            : relType === "citations"
              ? "citation"
              : "recommendation";
        const nid = upsertPaperNode(p, role, 4, `run:${rMeta.runId}`);
        if (nid && seedNid) {
          recordNodeSource(nid, `seed:${seedNid}`);
          if (relType === "references") addEdge(seedNid, nid, "cites", "引用参考文献");
          else if (relType === "citations") addEdge(nid, seedNid, "cites", "施引");
          else addEdge(seedNid, nid, "similar", "相似推荐");
        }
      }
    }
    if (!addedInPass) break;
  }

  // 3. Include basket items if space remains
  for (const bp of basket || []) {
    upsertPaperNode(bp, "basket", 3, "basket");
  }

  // 4. Compute Co-Author Edges across all nodes in the graph!
  const authorToNodes = new Map();
  for (const node of nodeMap.values()) {
    for (const a of node.paper?.authors || []) {
      const oaId = a.identifiers?.openalex?.[0];
      const s2Id = a.identifiers?.semantic_scholar?.[0];
      const orc = a.identifiers?.orcid?.[0];
      const cleanName = String(a.name || "").trim();
      if (!cleanName || cleanName === "Unknown" || cleanName === "Seed Paper") continue;
      const authorKey = oaId
        ? `oa:${oaId}`
        : orc
          ? `orcid:${orc}`
          : s2Id
            ? `s2:${s2Id}`
            : cleanName.length >= 5
              ? `name:${cleanName.toLowerCase()}`
              : null;
      if (!authorKey) continue;
      if (!authorToNodes.has(authorKey)) {
        authorToNodes.set(authorKey, { name: cleanName, nodeIds: new Set() });
      }
      authorToNodes.get(authorKey).nodeIds.add(node.id);
    }
  }

  for (const { name, nodeIds } of authorToNodes.values()) {
    const arr = [...nodeIds];
    if (arr.length < 2 || arr.length > 10) continue;
    for (let i = 0; i < arr.length; i++) {
      for (let j = i + 1; j < arr.length; j++) {
        addEdge(arr[i], arr[j], "coauthor", `共同作者: ${name}`);
      }
    }
  }

  // 5. Compute topological intelligence metrics: inDegree, outDegree, connectedSourcesCount, isBridgeNode, isCoCitedHub
  const inDegreeMap = new Map();
  const outDegreeMap = new Map();
  const neighborSetMap = new Map();
  const connectedSeedsMap = new Map();
  for (const nid of nodeMap.keys()) {
    inDegreeMap.set(nid, 0);
    outDegreeMap.set(nid, 0);
    neighborSetMap.set(nid, new Set());
    connectedSeedsMap.set(nid, new Set());
  }
  for (const e of edges) {
    neighborSetMap.get(e.source)?.add(e.target);
    neighborSetMap.get(e.target)?.add(e.source);
    const sNode = nodeMap.get(e.source);
    const tNode = nodeMap.get(e.target);
    if (sNode?.role === "seed") connectedSeedsMap.get(e.target)?.add(e.source);
    if (tNode?.role === "seed") connectedSeedsMap.get(e.source)?.add(e.target);
    if (e.type === "cites") {
      outDegreeMap.set(e.source, (outDegreeMap.get(e.source) || 0) + 1);
      inDegreeMap.set(e.target, (inDegreeMap.get(e.target) || 0) + 1);
    }
  }

  const activePaperKeys = new Set([
    ...((activeBundle?.papers || []).map((p) => p.paper_key)),
    ...((basket || []).map((p) => p.paper_key))
  ]);
  const slimGraphPaper = (p) => {
    if (!p || typeof p !== "object") return p;
    const { versions, conflicts, provenance, relations, comment_corrections, ...rest } = p;
    const isAlreadyInActiveOrBasket = activePaperKeys.has(p.paper_key);
    return {
      ...rest,
      ...(isAlreadyInActiveOrBasket && typeof rest.abstract === "string" && rest.abstract.length > 360
        ? { abstract: rest.abstract.slice(0, 360) + "..." }
        : {})
    };
  };
  const nodes = [...nodeMap.values()].map((n) => {
    const inDegree = inDegreeMap.get(n.id) || 0;
    const outDegree = outDegreeMap.get(n.id) || 0;
    const totalDegree = neighborSetMap.get(n.id)?.size || 0;
    const seedConns = connectedSeedsMap.get(n.id)?.size || 0;
    const rawSources = nodeSourcesMap.get(n.id)?.size || 0;
    const connectedSourcesCount = seedConns > 0 ? seedConns : rawSources;
    const isCoCitedHub = inDegree >= 2;
    const isBridgeNode = Boolean(
      n.role !== "seed" &&
        (seedConns >= 2 || (connectedSourcesCount >= 2 && totalDegree >= 2) || (inDegree >= 1 && outDegree >= 1 && totalDegree >= 3))
    );
    return {
      ...n,
      inDegree,
      outDegree,
      totalDegree,
      connectedSourcesCount,
      isBridgeNode,
      isCoCitedHub,
      paper: slimGraphPaper(n.paper)
    };
  });
  return {
    centerNodeId: centerNodeId || nodes[0]?.id || null,
    nodes,
    edges,
    counts: {
      nodes: nodes.length,
      citesEdges: edges.filter((e) => e.type === "cites").length,
      similarEdges: edges.filter((e) => e.type === "similar").length,
      coauthorEdges: edges.filter((e) => e.type === "coauthor").length,
      bridgeNodes: nodes.filter((n) => n.isBridgeNode).length,
      coCitedHubs: nodes.filter((n) => n.isCoCitedHub).length
    }
  };
}

function resolveTargetDoc2mdDir(customDir) {
  const { settings } = getEffectiveConfig();
  const raw = String(customDir || settings.doc2mdSubdir || "00-收件箱-待整理/文献解析").trim();
  const destDir = isAbsolutePath(raw)
    ? resolve(raw)
    : resolve(WORKSPACE_ROOT, raw.replace(/^[/\\]+/, ""));
  mkdirSync(destDir, { recursive: true });
  return destDir;
}

function sanitizeDoc2mdTitle(title, maxLen = 100) {
  let t = String(title || "").replace(/:/g, " - ");
  t = t.replace(/[\\/*?"<>|\r\n\t]+/g, "_").replace(/\s+/g, " ").trim();
  if (t.length > maxLen) {
    t = t.slice(0, maxLen).replace(/[ .\-_]+$/, "");
  }
  return t;
}

function formatZoteroBasenameJs(metadata, fallbackStem = "document") {
  let firstCreator = "";
  let year = "";
  let rawTitle = "";
  if (metadata && typeof metadata === "object") {
    let data = metadata.metadata || metadata;
    if (data && typeof data === "object" && data.data && typeof data.data === "object") {
      data = data.data;
    }
    const creators =
      (Array.isArray(data.creators)
        ? data.creators.filter((c) => c && typeof c === "object" && (c.creatorType || "author") === "author")
        : null) ||
      (Array.isArray(data.authors) ? data.authors : null) ||
      (Array.isArray(metadata.authors) ? metadata.authors : []);
    if (Array.isArray(creators) && creators.length > 0) {
      const first = creators[0];
      const multi = creators.length > 1;
      let name = "";
      if (first && typeof first === "object") {
        name = first.lastName || first.name || first.firstName || "";
        if (!first.lastName && name && !/[\u4e00-\u9fff]/.test(name)) {
          const s = String(name).trim();
          name = s.includes(",") ? s.split(",")[0].trim() : s.split(/\s+/).pop() || s;
        }
      } else if (typeof first === "string") {
        const s = first.trim();
        if (s.includes(",")) {
          name = s.split(",")[0].trim();
        } else if (/[\u4e00-\u9fff]/.test(s)) {
          name = s;
        } else {
          const parts = s.split(/\s+/);
          name = parts[parts.length - 1] || s;
        }
      }
      name = String(name || "").trim();
      if (name) {
        firstCreator = multi ? `${name} 等` : name;
      }
    }
    const dateStr = String(data.date || metadata.date || data.year || metadata.year || "");
    const mYear = dateStr.match(/(?:19|20)\d{2}/);
    if (mYear) year = mYear[0];
    rawTitle = String(data.title || metadata.title || "").trim();
  }
  if (!rawTitle) {
    rawTitle = String(fallbackStem || "document").trim();
  }
  const title = rawTitle ? sanitizeDoc2mdTitle(rawTitle, 68) : "";
  const parts = [firstCreator, year, title].filter(Boolean);
  if (!parts.length) return sanitizeDoc2mdTitle(fallbackStem || "document", 68);
  return sanitizeDoc2mdTitle(parts.join(" - "), 84);
}

function isSupplementalFilename(name) {
  const stem = basename(String(name || ""), extname(String(name || ""))).toLowerCase();
  return [
    "-si",
    "_si",
    ".si",
    "si.",
    "supporting",
    "supplement",
    "suppl",
    "supp",
    "appendix",
    "mmc1",
    "moesm",
    "_esm",
    "-esm",
    "补充",
    "附图"
  ].some((m) => stem.includes(m));
}

function inspectLocalDocumentFile(rawPath) {
  if (!rawPath || typeof rawPath !== "string" || !rawPath.trim()) return null;
  const fullPath = isAbsolutePath(rawPath.trim())
    ? resolve(rawPath.trim())
    : resolve(WORKSPACE_ROOT, rawPath.trim());
  assertReadablePath(fullPath);
  if (!existsSync(fullPath)) return null;
  const st = statSync(fullPath);
  if (st.isDirectory()) {
    return {
      path: fullPath,
      name: basename(fullPath),
      isDirectory: true,
      size: 0
    };
  }
  const ext = extname(fullPath).toLowerCase();
  const stem = basename(fullPath, ext);
  const dir = dirname(fullPath);
  let siblingPdfPath = null;
  let siblingPptxPath = null;
  let siblingSiPdfPath = null;
  try {
    const entries = readdirSync(dir, { withFileTypes: true });
    const stemLower = stem.toLowerCase().trim();
    const selfIsSi = isSupplementalFilename(basename(fullPath));
    const exactSiCandidates = [];
    const prefixSiCandidates = [];
    for (const entry of entries) {
      if (!entry.isFile()) continue;
      const candPath = join(dir, entry.name);
      if (candPath === fullPath) continue;
      const candExt = extname(entry.name).toLowerCase();
      const candStem = basename(entry.name, candExt);
      const candStemLower = candStem.toLowerCase().trim();
      if (ext === ".pptx" && candExt === ".pdf" && candStemLower === stemLower) {
        siblingPdfPath = candPath;
      } else if (ext === ".pdf" && candExt === ".pptx" && candStemLower === stemLower) {
        siblingPptxPath = candPath;
      } else if (ext === ".pdf" && candExt === ".pdf" && !selfIsSi && isSupplementalFilename(entry.name)) {
        const strippedPrefix = candStemLower
          .replace(/[-_.\s]*(?:si|suppl|supporting|supplement|appendix|mmc\d*|moesm\d*|esm|补充|附图).*$/i, "")
          .trim();
        // Reject empty stripped prefix (e.g., "Supplement.pdf" -> "") so startsWith("") never matches everything
        if (!strippedPrefix) continue;
        if (strippedPrefix === stemLower) {
          exactSiCandidates.push({ path: candPath, candStemLower });
        } else if (
          candStemLower.startsWith(stemLower) &&
          /^[-_.\s]/.test(candStemLower.slice(stemLower.length))
        ) {
          prefixSiCandidates.push({ path: candPath, candStemLower });
        } else if (
          strippedPrefix.length >= 6 &&
          stemLower.startsWith(strippedPrefix) &&
          /^[-_.\s]/.test(stemLower.slice(strippedPrefix.length))
        ) {
          prefixSiCandidates.push({ path: candPath, candStemLower });
        }
      }
    }
    if (exactSiCandidates.length === 1) {
      siblingSiPdfPath = exactSiCandidates[0].path;
    } else if (exactSiCandidates.length > 1) {
      const canonicalHit = exactSiCandidates.filter(
        (c) => c.candStemLower === `${stemLower}-si` || c.candStemLower === `${stemLower}_si`
      );
      if (canonicalHit.length === 1) {
        siblingSiPdfPath = canonicalHit[0].path;
      }
    } else if (prefixSiCandidates.length === 1) {
      siblingSiPdfPath = prefixSiCandidates[0].path;
    }
  } catch {}
  return {
    path: fullPath,
    name: basename(fullPath),
    stem,
    ext,
    size: st.size,
    isDirectory: false,
    dir,
    siblingPdfPath,
    siblingPptxPath,
    siblingSiPdfPath
  };
}

function pickSystemPath(input = {}) {
  const mode = ["file", "files", "folder"].includes(input.mode) ? input.mode : "file";
  const filterKey = input.filter || "pdf_pptx";
  const filterMap = {
    pdf_pptx: "Academic Documents (*.pdf;*.pptx)|*.pdf;*.pptx|PDF Files (*.pdf)|*.pdf|PowerPoint Slides (*.pptx)|*.pptx|All Files (*.*)|*.*",
    pdf: "PDF Documents (*.pdf)|*.pdf|All Files (*.*)|*.*",
    pptx: "PowerPoint Slides (*.pptx)|*.pptx|All Files (*.*)|*.*",
    json: "JSON Files (*.json)|*.json|All Files (*.*)|*.*",
    all: "All Files (*.*)|*.*"
  };
  const winFilter = filterMap[filterKey] || filterMap.pdf_pptx;
  const dialogTitle = String(
    input.title || (mode === "folder" ? "选择存放文件夹" : "选择待解析的学术文档 (PDF / PPTX)")
  ).slice(0, 120);
  const rawInitDir = input.initialDir ? String(input.initialDir).trim() : "";
  const initialDir = rawInitDir
    ? isAbsolutePath(rawInitDir)
      ? resolve(rawInitDir)
      : resolve(WORKSPACE_ROOT, rawInitDir)
    : WORKSPACE_ROOT;

  let chosenPaths = [];
  let canceled = false;

  const testOverride = resolveEnvPath(process.env.SANMU_TEST_PICK_PATH);
  if (testOverride) {
    if (testOverride === "__CANCEL__") {
      canceled = true;
    } else if (testOverride.startsWith("[")) {
      try {
        chosenPaths = JSON.parse(testOverride).map((p) => String(p));
      } catch {
        chosenPaths = [testOverride];
      }
    } else {
      chosenPaths = [testOverride];
    }
  } else if (process.platform === "win32") {
    const psScript = [
      "[Console]::OutputEncoding = [System.Text.Encoding]::UTF8",
      "Add-Type -AssemblyName System.Windows.Forms",
      "$top = New-Object System.Windows.Forms.Form",
      "$top.TopMost = $true",
      "$top.ShowInTaskbar = $false",
      "$top.WindowState = 'Minimized'",
      "$mode = $env:SANMU_DLG_MODE",
      "if ($mode -eq 'folder') {",
      "  $dlg = New-Object System.Windows.Forms.FolderBrowserDialog",
      "  $dlg.Description = $env:SANMU_DLG_TITLE",
      "  $dlg.ShowNewFolderButton = $true",
      "  if ($env:SANMU_DLG_INIT_DIR -and (Test-Path -LiteralPath $env:SANMU_DLG_INIT_DIR)) {",
      "    $dlg.SelectedPath = $env:SANMU_DLG_INIT_DIR",
      "  }",
      "  $res = $dlg.ShowDialog($top)",
      "  if ($res -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.SelectedPath) {",
      "    Write-Output (@{ canceled = $false; paths = @($dlg.SelectedPath) } | ConvertTo-Json -Compress)",
      "  } else {",
      "    Write-Output '{\"canceled\":true,\"paths\":[]}'",
      "  }",
      "} else {",
      "  $dlg = New-Object System.Windows.Forms.OpenFileDialog",
      "  $dlg.Title = $env:SANMU_DLG_TITLE",
      "  $dlg.Multiselect = ($mode -eq 'files')",
      "  $dlg.Filter = $env:SANMU_DLG_FILTER",
      "  if ($env:SANMU_DLG_INIT_DIR -and (Test-Path -LiteralPath $env:SANMU_DLG_INIT_DIR)) {",
      "    $dlg.InitialDirectory = $env:SANMU_DLG_INIT_DIR",
      "  }",
      "  $res = $dlg.ShowDialog($top)",
      "  if ($res -eq [System.Windows.Forms.DialogResult]::OK -and $dlg.FileNames.Count -gt 0) {",
      "    Write-Output (@{ canceled = $false; paths = @($dlg.FileNames) } | ConvertTo-Json -Compress)",
      "  } else {",
      "    Write-Output '{\"canceled\":true,\"paths\":[]}'",
      "  }",
      "}",
      "$top.Dispose()"
    ].join("\n");

    const res = spawnSync("powershell.exe", ["-NoProfile", "-STA", "-Command", psScript], {
      encoding: "utf8",
      windowsHide: true,
      timeout: 180000,
      env: {
        ...process.env,
        SANMU_DLG_MODE: mode,
        SANMU_DLG_FILTER: winFilter,
        SANMU_DLG_TITLE: dialogTitle,
        SANMU_DLG_INIT_DIR: existsSync(initialDir) ? initialDir : WORKSPACE_ROOT
      }
    });
    const outText = String(res.stdout || "").trim().split(/\r?\n/).pop() || "";
    if (outText) {
      try {
        const parsed = JSON.parse(outText);
        canceled = Boolean(parsed.canceled);
        chosenPaths = Array.isArray(parsed.paths) ? parsed.paths.map((p) => String(p)).filter(Boolean) : [];
      } catch {
        canceled = true;
      }
    } else {
      canceled = true;
    }
  } else if (process.platform === "darwin") {
    const osa =
      mode === "folder"
        ? `POSIX path of (choose folder with prompt ${JSON.stringify(dialogTitle)})`
        : `POSIX path of (choose file with prompt ${JSON.stringify(dialogTitle)})`;
    const res = spawnSync("osascript", ["-e", osa], { encoding: "utf8", timeout: 180000 });
    if (res.status === 0 && res.stdout && res.stdout.trim()) {
      chosenPaths = [res.stdout.trim()];
    } else {
      canceled = true;
    }
  } else {
    const zArgs =
      mode === "folder"
        ? ["--file-selection", "--directory", `--title=${dialogTitle}`]
        : ["--file-selection", `--title=${dialogTitle}`];
    const res = spawnSync("zenity", zArgs, { encoding: "utf8", timeout: 180000 });
    if (res.status === 0 && res.stdout && res.stdout.trim()) {
      chosenPaths = res.stdout.trim().split("\n").filter(Boolean);
    } else {
      canceled = true;
    }
  }

  if (canceled || chosenPaths.length === 0) {
    return {
      canceled: true,
      mode,
      path: null,
      paths: [],
      inspected: null
    };
  }

  const resolvedPaths = chosenPaths.map((p) => (isAbsolutePath(p) ? resolve(p) : resolve(WORKSPACE_ROOT, p)));
  for (const rp of resolvedPaths) {
    assertReadablePath(rp);
  }
  const primary = resolvedPaths[0];
  const inspected = mode === "folder"
    ? { path: primary, name: basename(primary), isDirectory: true, size: 0 }
    : inspectLocalDocumentFile(primary);

  let secondPdf = resolvedPaths[1] || null;
  if (!secondPdf && inspected && !inspected.isDirectory && inspected.siblingSiPdfPath) {
    secondPdf = inspected.siblingSiPdfPath;
  }

  return {
    canceled: false,
    mode,
    path: primary,
    paths: resolvedPaths,
    inspected,
    siblingPdfPath: inspected?.siblingPdfPath || null,
    siblingPptxPath: inspected?.siblingPptxPath || null,
    siblingSiPdfPath: secondPdf || null
  };
}

function resolveZoteroRedirectLocation(locationHeader) {
  if (!locationHeader || typeof locationHeader !== "string") return null;
  const trimmed = locationHeader.trim();
  if (!trimmed.toLowerCase().startsWith("file://")) return null;
  try {
    const filePath = fileURLToPath(trimmed);
    if (filePath && existsSync(filePath)) return resolve(filePath);
  } catch {}
  return null;
}

async function listZoteroPdfItems(input = {}) {
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");
  const colKey = input?.collectionKey && /^[23456789A-Z]{8}$/.test(String(input.collectionKey).trim())
    ? String(input.collectionKey).trim()
    : "";
  const query = input?.query ? String(input.query).trim() : "";
  const limit = Math.min(Math.max(Number(input?.limit) || 20, 1), 50);
  const onlyWithPdf = input?.onlyWithPdf !== false;

  let zoteroOnline = false;
  try {
    const ping = await fetch(`${baseUrl}/api/`, {
      headers: { "Zotero-Allowed-Request": "1" },
      signal: AbortSignal.timeout(1500)
    });
    zoteroOnline = ping.ok && Boolean(ping.headers.get("zotero-server-id") || ping.headers.get("zotero-api-version"));
  } catch {
    zoteroOnline = false;
  }
  if (!zoteroOnline) {
    return {
      zoteroOnline: false,
      collections: [],
      items: [],
      message: "未检测到本机 Zotero 桌面端在线（请确认 Zotero 已启动且开启本地通信）。"
    };
  }

  const { colMap, collections } = await fetchZoteroCollectionsMap(baseUrl);
  const endpoint = colKey
    ? `${baseUrl}/api/users/0/collections/${colKey}/items/top`
    : `${baseUrl}/api/users/0/items/top`;
  const params = new URLSearchParams({
    limit: String(Math.min(limit * 2, 60)),
    sort: "dateModified",
    direction: "desc"
  });
  if (query) {
    params.set("q", query);
    params.set("qmode", "everything");
  }

  const listResp = await fetch(`${endpoint}?${params.toString()}`, {
    headers: { "Zotero-Allowed-Request": "1" },
    signal: AbortSignal.timeout(4000)
  });
  if (!listResp.ok) {
    throw new Error(`读取 Zotero 条目列表失败 (HTTP ${listResp.status})`);
  }
  const rawItems = await listResp.json();
  const topPapers = (Array.isArray(rawItems) ? rawItems : []).filter((it) => {
    const d = it?.data || {};
    return !d.deleted && !["attachment", "note", "annotation"].includes(d.itemType);
  });

  const results = [];
  for (const item of topPapers) {
    if (results.length >= limit) break;
    const data = item?.data || {};
    const itemKey = item.key;
    if (!itemKey || !/^[A-Z0-9]{8}$/.test(itemKey)) continue;

    let children = [];
    try {
      const childResp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}/children?limit=25`, {
        headers: { "Zotero-Allowed-Request": "1" },
        signal: AbortSignal.timeout(2000)
      });
      if (childResp.ok) {
        const rawChildren = await childResp.json();
        if (Array.isArray(rawChildren)) children = rawChildren;
      }
    } catch {}

    const pdfAttachments = [];
    for (const child of children) {
      const cd = child?.data || {};
      if (cd.deleted || cd.itemType !== "attachment") continue;
      const isPdf =
        String(cd.contentType || "").toLowerCase() === "application/pdf" ||
        String(cd.filename || cd.title || "").toLowerCase().endsWith(".pdf");
      if (!isPdf || !child.key) continue;

      let localPath = null;
      if (typeof cd.path === "string" && isAbsolutePath(cd.path) && existsSync(cd.path)) {
        localPath = resolve(cd.path);
      } else {
        try {
          const fileResp = await fetch(`${baseUrl}/api/users/0/items/${child.key}/file`, {
            method: "GET",
            headers: { "Zotero-Allowed-Request": "1" },
            redirect: "manual",
            signal: AbortSignal.timeout(1500)
          });
          if (fileResp.status === 302 || fileResp.status === 301) {
            localPath = resolveZoteroRedirectLocation(fileResp.headers.get("location"));
          }
        } catch {}
      }

      const labelName = cd.filename || cd.title || `${child.key}.pdf`;
      const isSi = isSupplementalFilename(labelName) || isSupplementalFilename(cd.title || "");
      pdfAttachments.push({
        attachmentKey: child.key,
        title: cd.title || labelName,
        filename: labelName,
        localPath,
        role: isSi ? "si" : "main"
      });
    }

    const readyPdfs = pdfAttachments.filter((a) => a.localPath && existsSync(a.localPath));
    if (onlyWithPdf && readyPdfs.length === 0) continue;

    const mainAtt = readyPdfs.find((a) => a.role === "main") || null;
    const siAtt =
      readyPdfs.find((a) => a !== mainAtt && a.role === "si") ||
      (mainAtt && readyPdfs.length > 1 ? readyPdfs.find((a) => a !== mainAtt) : null);

    const authors = [];
    for (const c of data.creators || []) {
      if ((c.creatorType || "author") !== "author") continue;
      const full = c.name || [c.firstName, c.lastName].filter(Boolean).join(" ").trim();
      if (full) authors.push(full);
    }
    const yearMatch = String(data.date || "").match(/\b(19|20)\d{2}\b/);
    const year = yearMatch ? Number(yearMatch[0]) : null;
    const doi = data.DOI ? normalizeDoi(data.DOI) : "";
    const colNames = (data.collections || []).map((ck) => colMap.get(ck) || ck).filter(Boolean);
    const tags = (data.tags || []).map((t) => t?.tag).filter(Boolean);

    const metadata = {
      title: data.title || "",
      authors,
      date: data.date || (year ? String(year) : ""),
      year,
      publication_title: data.publicationTitle || "",
      volume: data.volume ? String(data.volume) : "",
      issue: data.issue ? String(data.issue) : "",
      pages: data.pages ? String(data.pages) : "",
      doi,
      abstract: data.abstractNote || "",
      url: data.url || (doi ? `https://doi.org/${doi}` : ""),
      zotero_item_uri: `zotero://select/library/items/${itemKey}`
    };

    results.push({
      itemKey,
      title: data.title || `Zotero Item ${itemKey}`,
      itemType: data.itemType || "journalArticle",
      authors,
      firstAuthor: authors[0] || "",
      year,
      date: data.date || "",
      journal: data.publicationTitle || "",
      doi,
      collections: colNames,
      tags,
      selectUri: `zotero://select/library/items/${itemKey}`,
      mainPdfPath: mainAtt?.localPath || null,
      siPdfPath: siAtt?.localPath || null,
      attachments: pdfAttachments,
      suggestedFolderName: formatZoteroBasenameJs(metadata, data.title || itemKey),
      metadata,
      rawZoteroItem: item
    });
  }

  return {
    zoteroOnline: true,
    collections,
    items: results
  };
}

const activeDoc2mdPromises = new Map();
const activePreviewServers = new Map();

function resolveDoc2mdJobDir(jobId, mustExist = true) {
  if (typeof jobId !== "string" || !/^[A-Za-z0-9][A-Za-z0-9._-]{0,199}$/.test(jobId)) {
    throw new Error("无效的解析任务 ID。");
  }
  const target = join(DOC2MD_JOBS_DIR, jobId);
  if (mustExist && (!existsSync(target) || !statSync(target).isDirectory())) {
    throw new Error(`未找到指定的文档解析任务: ${jobId}`);
  }
  if (existsSync(target) && !isWithin(realpathSync(DOC2MD_JOBS_DIR), realpathSync(target))) {
    throw new Error("解析任务路径越出插件数据目录。");
  }
  return target;
}

function readDoc2mdLiveProgress(jobDir, baseProgress = {}) {
  const ocrWorkDir = join(jobDir, "ocr-work");
  const jobJson = readJsonSafe(join(ocrWorkDir, "job.json"), null);
  const statusList = readJsonSafe(join(ocrWorkDir, "status.json"), null);
  if (!jobJson && !statusList) return baseProgress;
  const totalFiles = Array.isArray(jobJson?.files) ? jobJson.files.length : baseProgress.totalFiles || 1;
  let doneFiles = baseProgress.doneFiles || 0;
  let extractedPages = baseProgress.extractedPages || 0;
  let totalPages =
    (Array.isArray(jobJson?.files) ? jobJson.files.reduce((acc, f) => acc + (Number(f?.pages) || 0), 0) : 0) ||
    baseProgress.totalPages ||
    0;
  if (Array.isArray(statusList) && statusList.length > 0) {
    doneFiles = statusList.filter((s) => s?.state === "done").length;
    const ep = statusList.reduce((acc, s) => acc + (Number(s?.extract_progress?.extracted_pages) || 0), 0);
    const tp = statusList.reduce((acc, s) => acc + (Number(s?.extract_progress?.total_pages) || 0), 0);
    if (ep > 0) extractedPages = ep;
    if (tp > 0) totalPages = tp;
  } else if (jobJson?.state === "complete") {
    doneFiles = totalFiles;
    extractedPages = totalPages;
  }
  return {
    ...baseProgress,
    batchId: jobJson?.batch_id || baseProgress.batchId || null,
    ocrState: jobJson?.state || baseProgress.ocrState || null,
    totalFiles,
    doneFiles,
    extractedPages,
    totalPages,
    canResume: Boolean(jobJson?.batch_id && jobJson?.state !== "complete")
  };
}

function loadDoc2mdJob(jobId) {
  const jobDir = resolveDoc2mdJobDir(jobId, true);
  const meta = readJsonSafe(join(jobDir, "job-meta.json"), null);
  if (!meta) throw new Error(`解析任务元数据损坏或不存在: ${jobId}`);
  const progress = readDoc2mdLiveProgress(jobDir, meta.progress || {});
  const previewEntry = meta.outDir ? activePreviewServers.get(resolve(meta.outDir)) : null;
  const previewAlive = Boolean(previewEntry && previewEntry.child && previewEntry.child.exitCode === null);
  return {
    ...meta,
    progress,
    previewUrl: previewAlive ? previewEntry.url : null
  };
}

function saveDoc2mdJob(jobId, meta) {
  const jobDir = resolveDoc2mdJobDir(jobId, false);
  mkdirSync(jobDir, { recursive: true });
  const next = {
    ...meta,
    jobId,
    updatedAt: new Date().toISOString()
  };
  writeJsonSafe(join(jobDir, "job-meta.json"), next);
  return next;
}

function listRecentDoc2mdJobs(limit = 25) {
  if (!existsSync(DOC2MD_JOBS_DIR)) return [];
  const entries = [];
  for (const d of readdirSync(DOC2MD_JOBS_DIR, { withFileTypes: true })) {
    if (!d.isDirectory() || d.name.startsWith(".")) continue;
    const metaPath = join(DOC2MD_JOBS_DIR, d.name, "job-meta.json");
    if (!existsSync(metaPath)) continue;
    try {
      entries.push(loadDoc2mdJob(d.name));
    } catch {}
  }
  entries.sort((a, b) => String(b.updatedAt || b.createdAt || "").localeCompare(String(a.updatedAt || a.createdAt || "")));
  return entries.slice(0, limit);
}

async function buildLocalNativeRaw(ocrWorkDir, filesSpec, mode = "research") {
  const pyCode = [
    "import hashlib, json, os, re, sys",
    "from pathlib import Path",
    "cfg = json.loads(sys.argv[1])",
    "out_root = Path(cfg['ocrWorkDir']).resolve()",
    "raw_root = out_root / 'raw'",
    "raw_root.mkdir(parents=True, exist_ok=True)",
    "CAP_RE = re.compile(r'^\\s*(?:图|表|(?:Extended\\s+Data\\s+)?(?:Fig(?:ure)?\\.?|Table|Scheme)|(?:Supplemental|Supplementary)\\s+(?:Figure|Table|Note)|附图)\\s*[S]?[\\d一二三四五六七八九十]+', re.I)",
    "HEAD_RE = re.compile(r'^(?:Abstract|Introduction|Results?(?:\\s+and\\s+Discussion)?|Discussion|Conclusions?|Methods?|Experimental(?:\\s+Section)?|Supporting\\s+Information|Supplementary\\s+Information|References|Acknowledgments?|摘要|引言|实验部分|结果与讨论|结论|参考文献|\\d+(?:\\.\\d+)*\\s+\\S.{1,70})$', re.I)",
    "def norm_box(x0, y0, x1, y1, w, h):",
    "    if w <= 0 or h <= 0: return [0, 0, 1000, 1000]",
    "    clamp = lambda v: max(0, min(1000, int(round(v))))",
    "    return [clamp(x0 * 1000.0 / w), clamp(y0 * 1000.0 / h), clamp(x1 * 1000.0 / w), clamp(y1 * 1000.0 / h)]",
    "manifest_parts = []",
    "total_pages_all = 0",
    "for item in cfg['files']:",
    "    fpath = Path(item['path']).resolve()",
    "    did = item['data_id']",
    "    ext = fpath.suffix.lower()",
    "    item_dir = raw_root / did",
    "    img_dir = item_dir / 'images'",
    "    img_dir.mkdir(parents=True, exist_ok=True)",
    "    content_list = []",
    "    if ext == '.pdf':",
    "        import fitz",
    "        with fitz.open(fpath) as doc:",
    "            total_pages_all = max(total_pages_all, len(doc))",
    "            for pno, page in enumerate(doc):",
    "                pw, ph = float(page.rect.width or 612), float(page.rect.height or 792)",
    "                page_imgs = []",
    "                for idx_img, img_info in enumerate(page.get_images(full=True)):",
    "                    xref = img_info[0]",
    "                    try:",
    "                        base_img = doc.extract_image(xref)",
    "                        raw_bytes = base_img.get('image')",
    "                        img_ext = base_img.get('ext', 'png')",
    "                        if not raw_bytes or base_img.get('width', 0) < 36 or base_img.get('height', 0) < 36: continue",
    "                        rects = page.get_image_rects(xref)",
    "                        r = rects[0] if rects else fitz.Rect(pw*0.1, ph*0.1, pw*0.9, ph*0.5)",
    "                        fname = f'p{pno+1}_img{idx_img+1}.{img_ext}'",
    "                        (img_dir / fname).write_bytes(raw_bytes)",
    "                        blk = {'type': 'image', 'img_path': f'images/{fname}', 'image_caption': [], 'image_footnote': [], 'page_idx': pno, 'bbox': norm_box(r.x0, r.y0, r.x1, r.y1, pw, ph)}",
    "                        page_imgs.append(blk)",
    "                        content_list.append(blk)",
    "                    except Exception:",
    "                        pass",
    "                for b in page.get_text('blocks'):",
    "                    if len(b) >= 7 and b[6] != 0: continue",
    "                    raw_blk = re.sub(r'[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f]+', ' ', str(b[4] or ''))",
    "                    txt = re.sub(r'[ \\t]+', ' ', raw_blk).strip()",
    "                    if not txt: continue",
    "                    txt_flat = re.sub(r'\\s*\\n\\s*', ' ', txt).strip()",
    "                    bx = norm_box(b[0], b[1], b[2], b[3], pw, ph)",
    "                    if CAP_RE.match(txt_flat) and page_imgs:",
    "                        nearest = min(page_imgs, key=lambda im: abs(im['bbox'][3] - bx[1]))",
    "                        if not nearest['image_caption']:",
    "                            nearest['image_caption'].append(txt_flat)",
    "                            continue",
    "                    entry = {'type': 'text', 'text': txt_flat, 'page_idx': pno, 'bbox': bx}",
    "                    if (pno == 0 and len(content_list) == 0 and len(txt_flat) < 220) or HEAD_RE.match(txt_flat):",
    "                        entry['text_level'] = 1",
    "                    content_list.append(entry)",
    "            if cfg.get('mode') == 'book':",
    "                manifest_parts.append({'id': did, 'start_page': 1, 'end_page': len(doc), 'pages': len(doc), 'bytes': fpath.stat().st_size, 'path': str(fpath)})",
    "    elif ext == '.pptx':",
    "        from pptx import Presentation",
    "        from pptx.enum.shapes import MSO_SHAPE_TYPE",
    "        prs = Presentation(fpath)",
    "        sw, sh = float(prs.slide_width or 9144000), float(prs.slide_height or 5143500)",
    "        for sno, slide in enumerate(prs.slides):",
    "            for idx_sh, shape in enumerate(slide.shapes):",
    "                bx = norm_box(float(shape.left or 0), float(shape.top or 0), float((shape.left or 0) + (shape.width or 0)), float((shape.top or 0) + (shape.height or 0)), sw, sh)",
    "                if shape.shape_type == MSO_SHAPE_TYPE.PICTURE:",
    "                    try:",
    "                        ext_img = shape.image.ext or 'png'",
    "                        fname = f's{sno+1}_img{idx_sh+1}.{ext_img}'",
    "                        (img_dir / fname).write_bytes(shape.image.blob)",
    "                        content_list.append({'type': 'image', 'img_path': f'images/{fname}', 'image_caption': [], 'image_footnote': [], 'page_idx': sno, 'bbox': bx})",
    "                    except Exception:",
    "                        pass",
    "                elif getattr(shape, 'has_text_frame', False) and shape.text_frame.text.strip():",
    "                    clean_tf = re.sub(r'[\\x00-\\x08\\x0b\\x0c\\x0e-\\x1f]+', ' ', shape.text_frame.text)",
    "                    lines = [re.sub(r'[ \\t]+', ' ', ln).strip() for ln in clean_tf.splitlines() if ln.strip()]",
    "                    if not lines: continue",
    "                    is_title = (shape == getattr(slide.shapes, 'title', None)) or (bx[1] < 160 and len(lines) == 1 and len(lines[0]) <= 60)",
    "                    entry = {'type': 'text', 'text': '\\n'.join(lines), 'page_idx': sno, 'bbox': bx}",
    "                    if is_title: entry['text_level'] = 1",
    "                    content_list.append(entry)",
    "    cl_path = item_dir / 'content_list.json'",
    "    cl_bytes = (json.dumps(content_list, ensure_ascii=False, indent=2) + '\\n').encode('utf-8')",
    "    cl_path.write_bytes(cl_bytes)",
    "    hashes = {'content_list.json': hashlib.sha256(cl_bytes).hexdigest()}",
    "    for im_f in sorted(img_dir.glob('*')):",
    "        if im_f.is_file(): hashes[f'images/{im_f.name}'] = hashlib.sha256(im_f.read_bytes()).hexdigest()",
    "    (item_dir / '_ok').write_text(json.dumps({'sha256': hashes}, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')",
    "if cfg.get('mode') == 'book' and manifest_parts:",
    "    (out_root / 'manifest.json').write_text(json.dumps({'source': cfg['files'][0]['path'], 'pages': total_pages_all, 'chunk_size': total_pages_all, 'parts': manifest_parts}, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')",
    "print(json.dumps({'status': 'ok', 'items': len(cfg['files'])}))"
  ].join("\n");

  const payload = JSON.stringify({ ocrWorkDir, files: filesSpec, mode });
  const res = await runPython("-c", [pyCode, payload], 120000);
  if (res.exitCode !== 0) {
    throw new Error(pythonFailure(res, "本地原生文字/图片提取失败"));
  }
  return res.parsed;
}

async function autoGenerateBookChapters(pdfPath, manifestPath, outChaptersPath) {
  const pyCode = [
    "import json, re, sys",
    "from pathlib import Path",
    "import fitz",
    "pdf_path, manifest_path, out_path = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])",
    "manifest = json.loads(manifest_path.read_text(encoding='utf-8')) if manifest_path.is_file() else {}",
    "with fitz.open(pdf_path) as doc:",
    "    total = len(doc)",
    "    toc = doc.get_toc() or []",
    "top_entries = [t for t in toc if len(t) >= 3 and t[0] == 1 and 1 <= int(t[2]) <= total]",
    "chapters = []",
    "if len(top_entries) >= 2:",
    "    for idx, entry in enumerate(top_entries):",
    "        start = int(entry[2])",
    "        end = int(top_entries[idx + 1][2]) - 1 if idx + 1 < len(top_entries) else total",
    "        if end < start: end = start",
    "        cid = f'ch{idx+1:02d}'",
    "        raw_title = re.sub(r'[\\\\/*?\"<>|:\\r\\n\\t]+', ' ', str(entry[1] or cid)).strip()[:60] or cid",
    "        chapters.append({'id': cid, 'title': raw_title, 'file': f'{cid}-{raw_title}.md', 'start': start, 'end': end})",
    "elif manifest.get('parts'):",
    "    for idx, part in enumerate(manifest['parts']):",
    "        cid = part.get('id') or f'part{idx+1:02d}'",
    "        s, e = int(part.get('start_page', 1)), int(part.get('end_page', total))",
    "        title = f'第 {idx+1} 部分 (P.{s}-{e})'",
    "        chapters.append({'id': cid, 'title': title, 'file': f'{cid}.md', 'start': s, 'end': e})",
    "else:",
    "    chapters.append({'id': 'ch01', 'title': pdf_path.stem, 'file': f'01-{pdf_path.stem[:60]}.md', 'start': 1, 'end': total})",
    "out_path.write_text(json.dumps(chapters, ensure_ascii=False, indent=2) + '\\n', encoding='utf-8')",
    "print(json.dumps({'status': 'ok', 'chapters': chapters}, ensure_ascii=False))"
  ].join("\n");
  const res = await runPython("-c", [pyCode, pdfPath, manifestPath, outChaptersPath], 30000);
  if (res.exitCode !== 0) {
    throw new Error(pythonFailure(res, "自动生成教材章节表失败"));
  }
  return res.parsed?.chapters || [];
}

function collectDoc2mdOutputStats(outDir, siOutDir, stateMainDir, jobDir, stateSiDir = null, explicitReports = null) {
  const mdFiles = [];
  let primaryMarkdownPath = null;
  let siMarkdownPath = null;
  let imageCount = 0;
  let siImageCount = 0;

  const countFilesRec = (dir, extFilter = null) => {
    if (!dir || !existsSync(dir)) return [];
    const found = [];
    const walk = (cur) => {
      for (const entry of readdirSync(cur, { withFileTypes: true })) {
        if (entry.name.startsWith(".")) continue;
        const full = join(cur, entry.name);
        if (entry.isDirectory()) walk(full);
        else if (!extFilter || extFilter(entry.name)) found.push(full);
      }
    };
    try { walk(dir); } catch {}
    return found;
  };

  if (outDir && existsSync(outDir)) {
    const allMds = countFilesRec(outDir, (n) => n.toLowerCase().endsWith(".md") && !isWithin(join(outDir, "agent"), join(outDir, n)));
    for (const f of allMds) {
      const rel = relative(outDir, f).replace(/\\/g, "/");
      if (rel.startsWith("agent/")) continue;
      mdFiles.push(f);
      if (basename(f) !== "00-目录.md" && !primaryMarkdownPath) {
        primaryMarkdownPath = f;
      }
    }
    if (!primaryMarkdownPath && mdFiles.length > 0) primaryMarkdownPath = mdFiles[0];
    imageCount = countFilesRec(join(outDir, "images")).length;
  }

  if (siOutDir && existsSync(siOutDir)) {
    const siMds = countFilesRec(siOutDir, (n) => n.toLowerCase().endsWith(".md"));
    for (const f of siMds) {
      const rel = relative(siOutDir, f).replace(/\\/g, "/");
      if (rel.startsWith("agent/")) continue;
      mdFiles.push(f);
      if (basename(f) !== "00-目录.md" && !siMarkdownPath) {
        siMarkdownPath = f;
      }
    }
    siImageCount = countFilesRec(join(siOutDir, "images")).length;
  }

  const useExplicit = Boolean(explicitReports && typeof explicitReports === "object");
  const effectiveStateSiDir = useExplicit ? null : (stateSiDir || (jobDir ? join(jobDir, "state-si") : null));
  const researchReport = !useExplicit && stateMainDir ? readJsonSafe(join(stateMainDir, "research", "research-report.json"), null) : null;
  const siResearchReport = !useExplicit && effectiveStateSiDir ? readJsonSafe(join(effectiveStateSiDir, "research", "research-report.json"), null) : null;
  const buildReport = readJsonSafe(join(outDir, "build-report.json"), null);
  const formulasPath = useExplicit && explicitReports.formulasPath
    ? explicitReports.formulasPath
    : stateMainDir && existsSync(join(stateMainDir, "research", "formulas.json"))
      ? join(stateMainDir, "research", "formulas.json")
      : join(jobDir, "formulas.json");
  const siFormulasPath = useExplicit && explicitReports.siFormulasPath
    ? explicitReports.siFormulasPath
    : effectiveStateSiDir && existsSync(join(effectiveStateSiDir, "research", "formulas.json"))
      ? join(effectiveStateSiDir, "research", "formulas.json")
      : null;
  const mathReportPath = useExplicit && explicitReports.mathReportPath
    ? explicitReports.mathReportPath
    : stateMainDir && existsSync(join(stateMainDir, "research", "math-report.json"))
      ? join(stateMainDir, "research", "math-report.json")
      : join(jobDir, "math-report.json");
  const siMathReportPath = useExplicit && explicitReports.siMathReportPath
    ? explicitReports.siMathReportPath
    : effectiveStateSiDir && existsSync(join(effectiveStateSiDir, "research", "math-report.json"))
      ? join(effectiveStateSiDir, "research", "math-report.json")
      : null;
  const valOutPath = useExplicit && explicitReports.validateOutputPath
    ? explicitReports.validateOutputPath
    : join(jobDir, "validate-output.json");
  const siValOutPath = useExplicit && explicitReports.siValidateOutputPath
    ? explicitReports.siValidateOutputPath
    : null;

  const formulas = readJsonSafe(formulasPath, []);
  const siFormulas = siFormulasPath ? readJsonSafe(siFormulasPath, []) : [];
  const mathReport = readJsonSafe(mathReportPath, null);
  const siMathReport = siMathReportPath ? readJsonSafe(siMathReportPath, null) : null;
  const valOut = readJsonSafe(valOutPath, null);
  const siValOut = siValOutPath ? readJsonSafe(siValOutPath, null) : null;

  const mainFormulaCount = Array.isArray(formulas) ? formulas.length : researchReport?.formula_count ?? 0;
  const siFormulaCount = Array.isArray(siFormulas) && siFormulasPath ? siFormulas.length : siResearchReport?.formula_count ?? 0;
  const formulaCount = mainFormulaCount + siFormulaCount;

  const countMathFailures = (mr) =>
    typeof mr?.failed === "number"
      ? mr.failed
      : Array.isArray(mr?.failures)
        ? mr.failures.length
        : 0;
  const mathErrors = countMathFailures(mathReport) + countMathFailures(siMathReport);
  const mathPassed = mathErrors === 0;

  const isValReportOk = (vo) => Boolean(vo.ok ?? (vo.status === "pass" && (!Array.isArray(vo.errors) || vo.errors.length === 0)));
  const mainValPassed = useExplicit
    ? (valOut ? isValReportOk(valOut) : true)
    : researchReport
      ? researchReport.automatic_checks === "pass"
      : valOut
        ? isValReportOk(valOut)
        : true;
  const siValPassed = useExplicit
    ? (siValOut ? isValReportOk(siValOut) : true)
    : siResearchReport
      ? siResearchReport.automatic_checks === "pass"
      : true;
  const outputValidationPassed = mainValPassed && siValPassed && mathPassed;

  const warnings = [];
  if (buildReport?.review && typeof buildReport.review === "object") {
    for (const [chId, revList] of Object.entries(buildReport.review)) {
      if (Array.isArray(revList)) {
        for (const item of revList.slice(0, 10)) {
          warnings.push(typeof item === "string" ? `[${chId}] ${item}` : `[${chId}] ${JSON.stringify(item)}`);
        }
      }
    }
  }
  const collectValidateIssuesFromReport = (rep, tag = "") => {
    if (!rep || !Array.isArray(rep.commands)) return;
    for (const cmd of rep.commands) {
      if (!Array.isArray(cmd?.command) || !cmd.command.some((c) => String(c).includes("validate_output.py"))) continue;
      if (typeof cmd.stdout === "string" && cmd.stdout.trim().startsWith("{")) {
        try {
          const parsedVal = JSON.parse(cmd.stdout.trim());
          for (const err of (parsedVal.errors || []).slice(0, 8)) {
            warnings.push(`${tag ? `[${tag}] ` : ""}结构校验错误: ${err}`);
          }
          for (const w of (parsedVal.warnings || []).slice(0, 6)) {
            warnings.push(`${tag ? `[${tag}] ` : ""}结构校验提示: ${w}`);
          }
        } catch {}
      }
    }
  };
  if (!useExplicit) {
    collectValidateIssuesFromReport(researchReport, siResearchReport ? "主文" : "");
    collectValidateIssuesFromReport(siResearchReport, "SI");
  }
  for (const [vo, tag] of [[valOut, siValOut ? "主文" : ""], [siValOut, "SI"]]) {
    if (!vo) continue;
    for (const err of (vo.errors || []).slice(0, 8)) {
      warnings.push(`${tag ? `[${tag}] ` : ""}结构校验错误: ${err}`);
    }
    for (const w of (vo.warnings || []).slice(0, 6)) {
      warnings.push(`${tag ? `[${tag}] ` : ""}结构校验提示: ${w}`);
    }
  }
  for (const mr of [mathReport, siMathReport]) {
    if (Array.isArray(mr?.failures)) {
      for (const f of mr.failures.slice(0, 8)) {
        warnings.push(`公式校验告警 (${f.file || "md"}:${f.line || "?"}): ${f.error || (f.issues || []).join("; ")}`);
      }
    }
  }

  let previewExcerpt = "";
  if (primaryMarkdownPath && existsSync(primaryMarkdownPath)) {
    try {
      const rawMd = readFileSync(primaryMarkdownPath, "utf8");
      previewExcerpt = rawMd.slice(0, 1200);
    } catch {}
  }

  return {
    markdownFiles: mdFiles,
    markdownCount: mdFiles.length,
    primaryMarkdownPath,
    indexMarkdownPath: outDir && existsSync(join(outDir, "00-目录.md")) ? join(outDir, "00-目录.md") : null,
    siMarkdownPath,
    imageCount,
    siImageCount,
    totalImageCount: imageCount + siImageCount,
    formulaCount,
    mathPassed,
    mathErrors,
    outputValidationPassed,
    warnings: warnings.slice(0, 20),
    chapters: Array.isArray(buildReport?.chapters) ? buildReport.chapters : [],
    previewExcerpt
  };
}

function extractResearchBuildFailureDetail(pyRes, fallback) {
  const rep = pyRes?.parsed;
  if (rep && Array.isArray(rep.commands)) {
    for (const cmd of rep.commands) {
      if (cmd && cmd.returncode && cmd.returncode !== 0) {
        const detail = String(cmd.stderr || cmd.stdout || "").trim();
        if (detail) return `${fallback}: ${detail}`;
      }
    }
  }
  return pythonFailure(pyRes, fallback);
}

function assertResearchBuildSucceeded(pyRes, fallbackMsg, requirePass = false) {
  const checks = pyRes?.parsed?.automatic_checks;
  if (pyRes.exitCode !== 0) {
    if (!checks || checks === "build_failed" || checks === "not_run" || requirePass) {
      throw new Error(extractResearchBuildFailureDetail(pyRes, fallbackMsg));
    }
  }
  if (checks === "build_failed" || checks === "not_run") {
    throw new Error(extractResearchBuildFailureDetail(pyRes, fallbackMsg));
  }
}

function runDoc2mdPythonWithProgress(scriptPath, args, jobId, phaseLabelPrefix, timeoutMs = 1800000) {
  const jobDir = resolveDoc2mdJobDir(jobId, true);
  const pollTimer = setInterval(() => {
    try {
      const cur = readJsonSafe(join(jobDir, "job-meta.json"), null);
      if (!cur || cur.status !== "running") return;
      const liveProg = readDoc2mdLiveProgress(jobDir, cur.progress || {});
      const pageStr =
        liveProg.totalPages > 0
          ? ` (${liveProg.extractedPages || 0}/${liveProg.totalPages} 页 · 分片 ${liveProg.doneFiles || 0}/${liveProg.totalFiles || 1})`
          : liveProg.totalFiles > 0
            ? ` (已完成 ${liveProg.doneFiles || 0}/${liveProg.totalFiles} 份文档)`
            : "";
      saveDoc2mdJob(jobId, {
        ...cur,
        progress: liveProg,
        phaseLabel: `${phaseLabelPrefix}${pageStr}`
      });
    } catch {}
  }, 1500);

  return runPython(scriptPath, args, timeoutMs).finally(() => {
    clearInterval(pollTimer);
  });
}

async function executeDoc2mdJob(jobId, options = {}) {
  const scriptsDir = resolveDoc2mdScriptsDir();
  const mineruPy = join(scriptsDir, "mineru_ocr.py");
  const buildResearchPy = join(scriptsDir, "build_research_document.py");
  const buildMarkdownPy = join(scriptsDir, "build_markdown.py");
  const validateOutputPy = join(scriptsDir, "validate_output.py");
  const extractFormulasPy = join(scriptsDir, "extract_formulas.py");
  const validateMathCjs = join(scriptsDir, "validate_math.cjs");

  let job = loadDoc2mdJob(jobId);
  const jobDir = resolveDoc2mdJobDir(jobId, true);
  const ocrWorkDir = join(jobDir, "ocr-work");
  const stateMainDir = join(jobDir, "state-main");
  const stateSiDir = join(jobDir, "state-si");
  const sourceStagingDir = join(jobDir, "source-staging");
  mkdirSync(ocrWorkDir, { recursive: true });

  const input = job.input || {};
  const mode = input.mode || "research";
  const ocrEngine = input.ocrEngine || "mineru";
  const lang = input.language || (mode === "research" ? "en" : "ch");
  const outDir = resolve(job.outDir);
  const siOutDir = job.siOutDir ? resolve(job.siOutDir) : null;

  try {
    await prepareDocumentRuntime();
    // Prepare safe source PDF paths outside outDir so build_research_document containment check always passes
    let mainPdf = input.pdfPath ? resolve(input.pdfPath) : null;
    let siPdf = input.siPdfPath ? resolve(input.siPdfPath) : null;
    const pptxFile = input.pptxPath ? resolve(input.pptxPath) : null;

    if (mainPdf && isWithin(outDir, mainPdf)) {
      mkdirSync(sourceStagingDir, { recursive: true });
      const stagedMain = join(sourceStagingDir, basename(mainPdf));
      copyFileSync(mainPdf, stagedMain);
      mainPdf = stagedMain;
    }
    if (siPdf && siOutDir && (isWithin(outDir, siPdf) || isWithin(siOutDir, siPdf))) {
      mkdirSync(sourceStagingDir, { recursive: true });
      const stagedSi = join(sourceStagingDir, `si-${basename(siPdf)}`);
      copyFileSync(siPdf, stagedSi);
      siPdf = stagedSi;
    }

    let rawRootForBuild = join(ocrWorkDir, "raw");

    // Step 1: Obtain OCR / Raw content_list.json unless rebuildOnly or existing_raw
    if (options.rebuildOnly) {
      if (ocrEngine === "existing_raw" && input.existingRawDir) {
        rawRootForBuild = resolve(input.existingRawDir);
      }
    } else if (ocrEngine === "existing_raw") {
      if (!input.existingRawDir || !existsSync(input.existingRawDir)) {
        throw new Error("未找到指定的已有 OCR 结果目录 (existingRawDir)。");
      }
      rawRootForBuild = resolve(input.existingRawDir);
    } else if (ocrEngine === "local_fast" || (mode === "slides" && !mainPdf && pptxFile)) {
      job = saveDoc2mdJob(jobId, {
        ...job,
        status: "running",
        phase: "ocr_polling",
        phaseLabel: "⚡ 正在提取本地原生文字层与内嵌高清图表..."
      });
      const filesSpec = [];
      if (mode === "research") {
        filesSpec.push({ path: mainPdf, data_id: "main" });
        if (siPdf) filesSpec.push({ path: siPdf, data_id: "si" });
      } else if (mode === "slides") {
        const slideId = sanitizeDoc2mdTitle(basename(pptxFile || mainPdf, extname(pptxFile || mainPdf)), 40).replace(/[^A-Za-z0-9_-]+/g, "_") || "slides01";
        filesSpec.push({ path: mainPdf || pptxFile, data_id: slideId });
      } else {
        filesSpec.push({ path: mainPdf, data_id: "part01" });
      }
      await buildLocalNativeRaw(ocrWorkDir, filesSpec, mode);
    } else {
      // Cloud MinerU OCR
      const eff = getEffectiveConfig();
      if (!eff.status.mineru_api_token?.configured) {
        throw new Error(
          "尚未配置 MINERU_API_TOKEN：请在右上角「⚙ 设置与API」中填入 MinerU Token，或将解析引擎切换为「⚡ 本地原生提取 (免Token)」。"
        );
      }
      if (!mainPdf || !existsSync(mainPdf)) {
        throw new Error("MinerU 云端 OCR 需要提供有效的 PDF 文件路径。");
      }

      if (mode === "book" && !existsSync(join(ocrWorkDir, "manifest.json"))) {
        job = saveDoc2mdJob(jobId, {
          ...job,
          status: "running",
          phase: "splitting",
          phaseLabel: `✂ 正在按每片 ${input.chunkSize || 96} 页拆分教材 PDF...`
        });
        const splitRes = await runPython(
          mineruPy,
          ["split", mainPdf, "--out-root", ocrWorkDir, "--chunk-size", String(input.chunkSize || 96)],
          180000
        );
        if (splitRes.exitCode !== 0) {
          throw new Error(pythonFailure(splitRes, "教材 PDF 切片失败"));
        }
      } else if (mode !== "book" && !existsSync(join(ocrWorkDir, "ids.tsv"))) {
        const idLines = [];
        if (mode === "research") {
          idLines.push(`${mainPdf}\tmain`);
          if (siPdf && existsSync(siPdf)) idLines.push(`${siPdf}\tsi`);
        } else {
          const slideStem = basename(mainPdf, extname(mainPdf))
            .replace(/[^A-Za-z0-9_-]+/g, "_")
            .replace(/^[-_]+|[-_]+$/g, "")
            .slice(0, 48) || "slide01";
          const safeId = /^[A-Za-z0-9]/.test(slideStem) ? slideStem : `s_${slideStem}`;
          idLines.push(`${mainPdf}\t${safeId}`);
        }
        writeFileSync(join(ocrWorkDir, "ids.tsv"), idLines.join("\n") + "\n", "utf8");
      }

      const hasExistingJob = existsSync(join(ocrWorkDir, "job.json"));
      const ocrCmdArgs = hasExistingJob
        ? ["resume", "--out-root", ocrWorkDir, "--interval", "5"]
        : ["submit", "--id-file", join(ocrWorkDir, "ids.tsv"), "--language", lang, "--out-root", ocrWorkDir, "--interval", "5"];

      job = saveDoc2mdJob(jobId, {
        ...job,
        status: "running",
        phase: "ocr_polling",
        phaseLabel: hasExistingJob
          ? "🔄 正在断点续传 MinerU 云端 OCR 批次..."
          : "☁ 正在上传并执行 MinerU 云端公式/图表 OCR..."
      });

      const ocrRes = await runDoc2mdPythonWithProgress(
        mineruPy,
        ocrCmdArgs,
        jobId,
        "☁ MinerU 云端 OCR 识别中"
      );
      if (ocrRes.exitCode !== 0) {
        throw new Error(pythonFailure(ocrRes, "MinerU 云端 OCR 未完成（可点击「🔄 断点续传」继续原批次）"));
      }

      if (mode === "book" && input.bookSplitOnly && !options.rebuildOnly) {
        const draftChaptersPath = join(jobDir, "chapters-draft.json");
        const chapters = await autoGenerateBookChapters(mainPdf, join(ocrWorkDir, "manifest.json"), draftChaptersPath);
        const doneJob = saveDoc2mdJob(jobId, {
          ...loadDoc2mdJob(jobId),
          status: "ocr_completed",
          phase: "ocr_completed",
          phaseLabel: `✅ 教材切片与云端 OCR 已完成（预生成 ${chapters.length} 章，可一键装配或让 AI 核对章节表）`,
          chaptersDraftPath: draftChaptersPath,
          chaptersPreview: chapters
        });
        return doneJob;
      }
    }

    // Step 2: Assembly & Validation
    job = saveDoc2mdJob(jobId, {
      ...loadDoc2mdJob(jobId),
      status: "running",
      phase: "assembling",
      phaseLabel: "🔧 正在装配 Markdown、提取高清图表、归一化 LaTeX 公式并执行 KaTeX 校验..."
    });

    if (mode === "research") {
      let metadataJsonPath = null;
      if (input.metadata && typeof input.metadata === "object" && Object.keys(input.metadata).length > 0) {
        metadataJsonPath = join(jobDir, "metadata-input.json");
        const cleanMeta = {};
        if (input.metadata.title) cleanMeta.title = String(input.metadata.title).trim();
        if (Array.isArray(input.metadata.authors) && input.metadata.authors.length) {
          cleanMeta.authors = input.metadata.authors
            .map((a) => (typeof a === "string" ? a.trim() : String(a?.name || "").trim()))
            .filter((s) => s && !/\bet\s+al\.?\s*$|等\s*$/i.test(s));
        }
        if (input.metadata.date || input.metadata.year) {
          cleanMeta.date = String(input.metadata.date || input.metadata.year).trim();
        }
        if (input.metadata.publication_title || input.metadata.journal || input.metadata.venue) {
          cleanMeta.publication_title = String(
            input.metadata.publication_title || input.metadata.journal || input.metadata.venue
          ).trim();
        }
        if (input.metadata.doi) {
          const d = normalizeDoi(input.metadata.doi);
          if (/^10\.\d{4,9}\/\S+$/.test(d)) cleanMeta.doi = d;
        }
        if (input.metadata.abstract) cleanMeta.abstract = String(input.metadata.abstract);
        if (input.metadata.url) cleanMeta.url = String(input.metadata.url).trim();
        if (input.metadata.zotero_item_uri && /^zotero:\/\/select\/(?:library|groups\/\d+)\/items\/[A-Z0-9]{8}$/.test(input.metadata.zotero_item_uri)) {
          cleanMeta.zotero_item_uri = input.metadata.zotero_item_uri;
        }
        writeJsonSafe(metadataJsonPath, cleanMeta);
      }

      const buildMainArgs = (withMeta) => {
        const a = [
          "--pdf",
          mainPdf,
          "--raw",
          rawRootForBuild,
          "--out",
          outDir,
          "--file",
          basename(outDir),
          "--state-dir",
          stateMainDir,
          "--reading-order",
          input.readingOrder || "source"
        ];
        if (existsSync(join(rawRootForBuild, "main"))) {
          a.push("--id", "main");
        }
        if (input.autoRepairs === false) {
          a.push("--no-auto-repairs");
        } else {
          a.push("--auto-repairs");
        }
        if (input.keepTableImages === false) {
          a.push("--no-keep-table-images");
        } else {
          a.push("--keep-table-images");
        }
        if (input.agentIndex) {
          a.push("--agent-index");
        }
        if (options.repairsJsonPath && existsSync(options.repairsJsonPath)) {
          a.push("--repairs", resolve(options.repairsJsonPath));
        }
        if (withMeta && metadataJsonPath && existsSync(metadataJsonPath)) {
          a.push("--metadata", metadataJsonPath);
        }
        if (siPdf && siOutDir) {
          a.push("--si-doc", basename(siOutDir));
        }
        return a;
      };

      const hasSi = Boolean(siPdf && siOutDir && existsSync(siPdf));
      let siArgs = null;
      if (hasSi) {
        siArgs = [
          "--pdf",
          siPdf,
          "--raw",
          rawRootForBuild,
          "--out",
          siOutDir,
          "--file",
          basename(siOutDir),
          "--state-dir",
          stateSiDir,
          "--reading-order",
          input.readingOrder || "source",
          "--supplement-to",
          basename(outDir)
        ];
        if (existsSync(join(rawRootForBuild, "si"))) {
          siArgs.push("--id", "si");
        }
        if (input.autoRepairs === false) {
          siArgs.push("--no-auto-repairs");
        } else {
          siArgs.push("--auto-repairs");
        }
        if (input.keepTableImages === false) {
          siArgs.push("--no-keep-table-images");
        } else {
          siArgs.push("--keep-table-images");
        }
        if (input.agentIndex) {
          siArgs.push("--agent-index");
        }
        // Build SI first so all #Figure S* / #Table S* anchors exist on disk when main paper runs validate_output.py
        const siRes = await runPython(buildResearchPy, siArgs, 300000);
        assertResearchBuildSucceeded(siRes, "补充材料 (SI) Markdown 装配失败", false);
      }

      let mainRes = await runPython(buildResearchPy, buildMainArgs(true), 300000);
      if (
        mainRes.exitCode !== 0 &&
        metadataJsonPath &&
        /not corroborated|differ|mismatch/i.test(String(mainRes.stderr || mainRes.stdout || ""))
      ) {
        // Fallback without uncorroborated external metadata so custom/cropped PDFs still build cleanly
        mainRes = await runPython(buildResearchPy, buildMainArgs(false), 300000);
      }
      assertResearchBuildSucceeded(mainRes, "科研论文 Markdown 装配失败", false);

      if (hasSi && siArgs) {
        // Re-run SI build & validation now that main paper markdown exists on disk, and verify its result
        const siFinalRes = await runPython(buildResearchPy, siArgs, 120000);
        assertResearchBuildSucceeded(siFinalRes, "补充材料 (SI) 最终复核与装配失败", false);
      }
    } else if (mode === "slides") {
      const slideArgs = [
        "--raw",
        rawRootForBuild,
        "--out",
        outDir,
        "--mode",
        "slides",
        "--state-dir",
        stateMainDir
      ];
      if (mainPdf && existsSync(mainPdf)) {
        slideArgs.push("--pdf", mainPdf);
      }
      if (pptxFile && existsSync(pptxFile)) {
        slideArgs.push("--source-dir", dirname(pptxFile), "--source-glob", basename(pptxFile));
      }
      if (input.keepTableImages !== false) {
        slideArgs.push("--keep-table-images");
      }
      if (input.agentIndex) {
        slideArgs.push("--agent-index");
      }
      const bRes = await runPython(buildMarkdownPy, slideArgs, 300000);
      if (bRes.exitCode !== 0) {
        throw new Error(pythonFailure(bRes, "课件 Markdown 装配失败"));
      }
      await runStandaloneDoc2mdValidators(outDir, jobDir, validateOutputPy, extractFormulasPy, validateMathCjs);
    } else if (mode === "book") {
      const manifestPath = existsSync(join(ocrWorkDir, "manifest.json"))
        ? join(ocrWorkDir, "manifest.json")
        : join(rawRootForBuild, "..", "manifest.json");
      let chaptersPath =
        (options.chaptersJsonPath && existsSync(options.chaptersJsonPath) ? resolve(options.chaptersJsonPath) : null) ||
        (input.chaptersJsonPath && existsSync(input.chaptersJsonPath) ? resolve(input.chaptersJsonPath) : null) ||
        (existsSync(join(jobDir, "chapters-draft.json")) ? join(jobDir, "chapters-draft.json") : null);
      if (!chaptersPath) {
        chaptersPath = join(jobDir, "chapters-auto.json");
        await autoGenerateBookChapters(mainPdf, manifestPath, chaptersPath);
      }
      const bookArgs = [
        "--raw",
        rawRootForBuild,
        "--out",
        outDir,
        "--mode",
        "book",
        "--state-dir",
        stateMainDir,
        "--keep-table-images"
      ];
      if (existsSync(manifestPath) && existsSync(chaptersPath)) {
        bookArgs.push("--manifest", manifestPath, "--chapters", chaptersPath);
      }
      if (mainPdf && existsSync(mainPdf)) {
        bookArgs.push("--pdf", mainPdf);
      }
      if (options.repairsJsonPath && existsSync(options.repairsJsonPath)) {
        bookArgs.push("--repairs", resolve(options.repairsJsonPath));
      } else if (input.autoRepairs !== false) {
        bookArgs.push("--auto-repairs");
      }
      if (input.agentIndex) {
        bookArgs.push("--agent-index");
      }
      const bRes = await runPython(buildMarkdownPy, bookArgs, 300000);
      if (bRes.exitCode !== 0) {
        throw new Error(pythonFailure(bRes, "教材 Markdown 装配失败"));
      }
      await runStandaloneDoc2mdValidators(outDir, jobDir, validateOutputPy, extractFormulasPy, validateMathCjs);
    }

    const stats = collectDoc2mdOutputStats(outDir, siOutDir, stateMainDir, jobDir, stateSiDir);
    const completedJob = saveDoc2mdJob(jobId, {
      ...loadDoc2mdJob(jobId),
      status: "completed",
      phase: "completed",
      phaseLabel: `✅ 解析完成：产出 ${stats.markdownCount} 份 Markdown · 提取图片 ${stats.totalImageCount} 张 · 公式 ${stats.formulaCount} 处`,
      completedAt: new Date().toISOString(),
      error: null,
      stats
    });

    // Sync back to paper evidenceMap & basket if linked to a paper
    const linkedKey =
      input.paperKey ||
      (input.metadata?.doi ? `doi:${normalizeDoi(input.metadata.doi)}` : null);
    if (linkedKey) {
      updateStateTransaction((state) => {
        const prev = state.evidenceMap[linkedKey] || {};
        state.evidenceMap[linkedKey] = {
          ...prev,
          stage: prev.stage === "read" ? "read" : "identity_checked",
          doc2md_out_dir: outDir,
          doc2md_md_path: stats.primaryMarkdownPath || null,
          doc2md_si_out_dir: siOutDir || null,
          doc2md_job_id: jobId,
          updatedAt: new Date().toISOString()
        };
        if (Array.isArray(state.basket)) {
          state.basket = state.basket.map((bp) =>
            (bp.paper_key || computePaperKey(bp)) === linkedKey
              ? {
                  ...bp,
                  doc2md_out_dir: outDir,
                  doc2md_md_path: stats.primaryMarkdownPath || null,
                  doc2md_si_out_dir: siOutDir || null,
                  doc2md_job_id: jobId
                }
              : bp
          );
        }
      });
    }

    return completedJob;
  } catch (err) {
    const msg = redactSecrets(err instanceof Error ? err.message : String(err));
    const cur = readJsonSafe(join(jobDir, "job-meta.json"), job) || job;
    const liveProg = readDoc2mdLiveProgress(jobDir, cur.progress || {});
    const failedJob = saveDoc2mdJob(jobId, {
      ...cur,
      status: "failed",
      phase: "failed",
      phaseLabel: `❌ 解析中断：${msg}`,
      progress: liveProg,
      error: msg
    });
    throw Object.assign(new Error(msg), { jobId, job: failedJob });
  }
}

async function runStandaloneDoc2mdValidators(
  outDir,
  jobDir,
  validateOutputPy,
  extractFormulasPy,
  validateMathCjs,
  resourceRoot = null,
  prefix = ""
) {
  const valArgs = [outDir, "--images-dir", "images", "--json"];
  if (resourceRoot) {
    valArgs.push("--resource-root", resourceRoot);
  }
  const valOutPath = join(jobDir, `${prefix}validate-output.json`);
  const formulasJson = join(jobDir, `${prefix}formulas.json`);
  const mathReportJson = join(jobDir, `${prefix}math-report.json`);
  // Clear previous reports first: a crashed validator must not leave an older
  // report that a later run would read as its own result.
  for (const stalePath of [valOutPath, formulasJson, mathReportJson]) {
    rmSync(stalePath, { force: true });
  }
  const valRes = await runPython(validateOutputPy, valArgs, 60000);
  if (!valRes.parsed || typeof valRes.parsed.rawText === "string") {
    throw new Error(
      `结构校验未产出报告（exit ${valRes.exitCode}）：${String(valRes.stderr || "").trim().slice(0, 400) || "无输出"}`
    );
  }
  writeJsonSafe(valOutPath, valRes.parsed);
  const extRes = await runPython(extractFormulasPy, [outDir, "--out", formulasJson], 60000);
  // Exit 1 = 发现定界/解析错误，但清单已写出，属可报告结果；无清单或其它退出码才算执行失败。
  if (!existsSync(formulasJson) || ![0, 1].includes(extRes.exitCode)) {
    throw new Error(
      `公式提取失败（exit ${extRes.exitCode}）：${String(extRes.stderr || "").trim().slice(0, 400) || "无输出"}`
    );
  }
  {
    const formulas = readJsonSafe(formulasJson, []);
    const { childEnv } = getEffectiveConfig();
    const mathArgs = [validateMathCjs, formulasJson, "--report", mathReportJson];
    if (!Array.isArray(formulas) || formulas.length === 0) {
      mathArgs.push("--allow-empty");
    }
    const mathRes = spawnSync("node", mathArgs, {
      cwd: dirname(validateMathCjs),
      env: childEnv,
      encoding: "utf8",
      timeout: 30000,
      windowsHide: true
    });
    // Exit 1 =公式编译错误（报告仍有效）；崩溃/超时/无报告才算执行失败。
    if (!existsSync(mathReportJson) || ![0, 1].includes(mathRes.status)) {
      throw new Error(
        `KaTeX 校验未产出报告（exit ${mathRes.status}）：${String(mathRes.stderr || mathRes.error?.message || "").trim().slice(0, 400) || "无输出"}`
      );
    }
  }
  return {
    validateOutputPath: valOutPath,
    formulasPath: formulasJson,
    mathReportPath: mathReportJson
  };
}

async function startKatexPreviewServer(outDir, requestedPort = 8766, openInBrowser = true) {
  await prepareDocumentRuntime();
  const resolvedOut = isAbsolutePath(outDir) ? resolve(outDir) : resolve(WORKSPACE_ROOT, outDir);
  assertReadablePath(resolvedOut);
  if (!existsSync(resolvedOut) || !statSync(resolvedOut).isDirectory()) {
    throw new Error(`预览目录不存在: ${resolvedOut}`);
  }
  const existing = activePreviewServers.get(resolvedOut);
  if (existing && existing.child && existing.child.exitCode === null) {
    if (openInBrowser && !process.env.SANMU_TEST_NO_BROWSER) {
      openFileInSystem(existing.url);
    }
    return { url: existing.url, port: existing.port, outDir: resolvedOut, reused: true };
  }

  const scriptsDir = resolveDoc2mdScriptsDir();
  const previewPy = join(scriptsDir, "preview_server.py");
  const katexInfo = resolveKatexNodeModules(scriptsDir);
  const { childEnv } = getEffectiveConfig();
  const pyRt = resolvePythonRuntime();
  if (!pyRt.supported) {
    throw new Error("未检测到 Python 3.10+ 运行环境，无法启动本地 KaTeX 预览服务。");
  }

  const usedPorts = new Set([...activePreviewServers.values()].map((v) => v.port));
  let port = Number(requestedPort) || 8766;
  while (usedPorts.has(port)) port++;

  const args = [...pyRt.prefixArgs, previewPy, resolvedOut, "--port", String(port)];
  if (katexInfo.katexDistDir) {
    args.push("--katex-dir", katexInfo.katexDistDir);
  }

  const child = spawn(pyRt.cmd, args, {
    cwd: scriptsDir,
    env: childEnv,
    stdio: ["ignore", "pipe", "pipe"],
    windowsHide: true
  });
  let stderr = "";
  child.stderr.on("data", (d) => {
    stderr += d.toString("utf8");
  });

  await new Promise((r) => setTimeout(r, 350));
  if (child.exitCode !== null) {
    throw new Error(`启动 KaTeX 预览服务失败: ${stderr.trim() || `退出码 ${child.exitCode}`}`);
  }

  const url = `http://127.0.0.1:${port}/`;
  activePreviewServers.set(resolvedOut, {
    child,
    port,
    url,
    outDir: resolvedOut,
    startedAt: new Date().toISOString()
  });
  child.on("close", () => {
    if (activePreviewServers.get(resolvedOut)?.child === child) {
      activePreviewServers.delete(resolvedOut);
    }
  });

  if (openInBrowser && !process.env.SANMU_TEST_NO_BROWSER) {
    openFileInSystem(url);
  }
  return { url, port, outDir: resolvedOut, reused: false };
}

function stopKatexPreviewServer(outDir = null) {
  let stopped = 0;
  for (const [key, entry] of [...activePreviewServers.entries()]) {
    if (!outDir || key === resolve(outDir)) {
      try {
        entry.child.kill();
      } catch {}
      activePreviewServers.delete(key);
      stopped++;
    }
  }
  return stopped;
}

async function startOrRunDoc2mdJob(input = {}) {
  const mode = ["research", "slides", "book"].includes(input.mode) ? input.mode : "research";
  const ocrEngine = ["mineru", "local_fast", "existing_raw"].includes(input.ocrEngine)
    ? input.ocrEngine
    : "mineru";

  let pdfPath = input.pdfPath ? String(input.pdfPath).trim() : "";
  let siPdfPath = input.siPdfPath ? String(input.siPdfPath).trim() : "";
  let pptxPath = input.pptxPath ? String(input.pptxPath).trim() : "";
  const existingRawDir = input.existingRawDir ? String(input.existingRawDir).trim() : "";

  if (pdfPath) {
    const info = inspectLocalDocumentFile(pdfPath);
    if (!info || info.isDirectory) throw new Error(`未找到有效的主文档文件: ${pdfPath}`);
    if (info.ext === ".pptx" && !pptxPath) {
      pptxPath = info.path;
      pdfPath = info.siblingPdfPath || "";
    } else {
      pdfPath = info.path;
      if (mode === "slides" && !pptxPath && info.siblingPptxPath) {
        pptxPath = info.siblingPptxPath;
      }
      if (mode === "research" && !siPdfPath && info.siblingSiPdfPath && input.autoDetectSi !== false) {
        siPdfPath = info.siblingSiPdfPath;
      }
    }
  }
  if (pptxPath) {
    const pInfo = inspectLocalDocumentFile(pptxPath);
    if (!pInfo || pInfo.isDirectory) throw new Error(`未找到有效的 PPTX 课件文件: ${pptxPath}`);
    pptxPath = pInfo.path;
    if (!pdfPath && pInfo.siblingPdfPath) {
      pdfPath = pInfo.siblingPdfPath;
    }
  }
  if (siPdfPath) {
    const sInfo = inspectLocalDocumentFile(siPdfPath);
    if (!sInfo || sInfo.isDirectory) throw new Error(`未找到有效的补充材料 (SI) PDF 文件: ${siPdfPath}`);
    siPdfPath = sInfo.path;
  }

  if (ocrEngine === "existing_raw") {
    if (!existingRawDir) throw new Error("选择「复用已有 OCR 目录」时，请提供 existingRawDir 路径。");
    const resolvedRaw = isAbsolutePath(existingRawDir) ? resolve(existingRawDir) : resolve(WORKSPACE_ROOT, existingRawDir);
    assertReadablePath(resolvedRaw);
    if (!existsSync(resolvedRaw)) throw new Error(`指定的 OCR 目录不存在: ${resolvedRaw}`);
  } else if (!pdfPath && !(mode === "slides" && pptxPath)) {
    throw new Error("请先选择或提供要解析的本地 PDF（或课件 PPTX）文件路径。");
  }

  const baseSaveDir = resolveTargetDoc2mdDir(input.saveDir);
  const primarySource = pdfPath || pptxPath || existingRawDir;
  const fallbackStem = basename(primarySource, extname(primarySource)) || "document";
  const metadata = input.metadata && typeof input.metadata === "object" ? input.metadata : {};

  const maxSafeStemLen = Math.max(24, Math.min(84, Math.floor((236 - baseSaveDir.length - 16) / 2)));
  let folderStem = input.outputFolderName ? sanitizeDoc2mdTitle(input.outputFolderName, maxSafeStemLen) : "";
  if (!folderStem) {
    if (mode === "research") {
      folderStem = sanitizeDoc2mdTitle(formatZoteroBasenameJs(metadata, fallbackStem), maxSafeStemLen);
    } else if (mode === "slides") {
      folderStem = `${sanitizeDoc2mdTitle(metadata.title || fallbackStem, Math.max(20, maxSafeStemLen - 6))}-课件笔记`;
    } else {
      folderStem = `${sanitizeDoc2mdTitle(metadata.title || fallbackStem, Math.max(20, maxSafeStemLen - 5))}-知识库`;
    }
  }

  const jobId = makeRunId(`doc2md-${mode}`);
  let outDir = join(baseSaveDir, folderStem);
  let priorJobToSeedStateFrom = null;
  if (existsSync(outDir)) {
    const recentJobs = listRecentDoc2mdJobs(50);
    const matchingPriorJob = recentJobs.find((pj) => pj?.outDir && resolve(pj.outDir) === resolve(outDir)) || null;
    const isSameSource = Boolean(
      matchingPriorJob &&
        ((pdfPath && matchingPriorJob.input?.pdfPath && resolve(matchingPriorJob.input.pdfPath) === resolve(pdfPath)) ||
          (pptxPath && matchingPriorJob.input?.pptxPath && resolve(matchingPriorJob.input.pptxPath) === resolve(pptxPath)) ||
          (existingRawDir &&
            matchingPriorJob.input?.existingRawDir &&
            resolve(matchingPriorJob.input.existingRawDir) ===
              resolve(isAbsolutePath(existingRawDir) ? existingRawDir : resolve(WORKSPACE_ROOT, existingRawDir))))
    );
    if (matchingPriorJob && (isSameSource || Boolean(input.outputFolderName))) {
      priorJobToSeedStateFrom = matchingPriorJob;
    } else if (!input.outputFolderName) {
      outDir = uniqueDestination(baseSaveDir, folderStem, input.paperKey || metadata.doi || jobId);
      folderStem = basename(outDir);
    }
  }
  const siOutDir = mode === "research" && siPdfPath ? `${outDir} - SI` : null;

  if (priorJobToSeedStateFrom) {
    try {
      const prevJobDir = resolveDoc2mdJobDir(priorJobToSeedStateFrom.jobId, false);
      const nextJobDir = resolveDoc2mdJobDir(jobId, false);
      for (const stateSub of ["state-main", "state-si"]) {
        for (const stateFile of [".build-ownership.json", "build-report.json"]) {
          const prevFile = join(prevJobDir, stateSub, stateFile);
          if (existsSync(prevFile)) {
            const nextStateSubDir = join(nextJobDir, stateSub);
            mkdirSync(nextStateSubDir, { recursive: true });
            copyFileSync(prevFile, join(nextStateSubDir, stateFile));
          }
        }
      }
    } catch {}
  }

  const displayTitle = metadata.title || fallbackStem;
  const initialJob = saveDoc2mdJob(jobId, {
    jobId,
    mode,
    ocrEngine,
    title: displayTitle,
    status: "running",
    phase: "preflight",
    phaseLabel: "🚀 正在预检源文件与初始化解析任务...",
    createdAt: new Date().toISOString(),
    outDir,
    siOutDir,
    saveDir: baseSaveDir,
    input: {
      mode,
      ocrEngine,
      pdfPath: pdfPath || null,
      siPdfPath: siPdfPath || null,
      pptxPath: pptxPath || null,
      existingRawDir: existingRawDir ? (isAbsolutePath(existingRawDir) ? resolve(existingRawDir) : resolve(WORKSPACE_ROOT, existingRawDir)) : null,
      chaptersJsonPath: input.chaptersJsonPath || null,
      language: input.language || (mode === "research" ? "en" : "ch"),
      autoRepairs: input.autoRepairs !== false,
      keepTableImages: input.keepTableImages !== false,
      agentIndex: Boolean(input.agentIndex),
      readingOrder: input.readingOrder || "source",
      chunkSize: Number(input.chunkSize) || 96,
      bookSplitOnly: Boolean(input.bookSplitOnly),
      saveDir: baseSaveDir,
      outputFolderName: folderStem,
      paperKey: input.paperKey || null,
      zoteroItemKey: input.zoteroItemKey || null,
      metadata
    },
    progress: {
      totalFiles: siPdfPath ? 2 : 1,
      doneFiles: 0,
      extractedPages: 0,
      totalPages: 0,
      canResume: false
    },
    stats: null,
    error: null
  });

  const execPromise = executeDoc2mdJob(jobId).finally(() => {
    activeDoc2mdPromises.delete(jobId);
  });
  activeDoc2mdPromises.set(jobId, execPromise);

  if (input.async) {
    // Wait briefly so fast local extractions or immediate preflight errors settle right away
    let settled = false;
    let firstErr = null;
    await Promise.race([
      execPromise.then(
        () => { settled = true; },
        (err) => { settled = true; firstErr = err; }
      ),
      new Promise((r) => setTimeout(r, 1200))
    ]);
    if (settled && firstErr) {
      throw firstErr;
    }
    const latestJob = loadDoc2mdJob(jobId);
    const panelState = await getFullPanelState();
    return {
      text:
        latestJob.status === "completed"
          ? `✅ 文档《${latestJob.title}》已完成解析并存入：\`${latestJob.outDir}\`\n- 主 Markdown: \`${latestJob.stats?.primaryMarkdownPath || latestJob.outDir}\`\n- 提取高清图: ${latestJob.stats?.totalImageCount || 0} 张 · 公式: ${latestJob.stats?.formulaCount || 0} 处 (KaTeX校验: ${latestJob.stats?.mathPassed ? "通过" : "含告警"})`
          : `⏳ 已启动文档解析任务 \`${jobId}\`（${latestJob.phaseLabel}），目标目录：\`${latestJob.outDir}\`。`,
      structured: {
        ...panelState,
        activeDoc2mdJob: latestJob
      }
    };
  }

  const finishedJob = await execPromise;
  const panelState = await getFullPanelState();
  return {
    text:
      finishedJob.status === "ocr_completed"
        ? `✅ 教材《${finishedJob.title}》已完成切片与云端 OCR（预生成章节表：\`${finishedJob.chaptersDraftPath}\`）。可核对章节表后执行装配。`
        : `✅ 文档《${finishedJob.title}》已完成 Markdown 解析与 KaTeX 校验！\n- 输出目录: \`${finishedJob.outDir}\`${finishedJob.siOutDir ? `\n- 补充材料 (SI) 目录: \`${finishedJob.siOutDir}\`` : ""}\n- 主文档: \`${finishedJob.stats?.primaryMarkdownPath || finishedJob.outDir}\`\n- 提取高清图片: ${finishedJob.stats?.totalImageCount || 0} 张 · 公式: ${finishedJob.stats?.formulaCount || 0} 处 (KaTeX校验: ${finishedJob.stats?.mathPassed ? "通过" : "存在告警"})`,
    structured: {
      ...panelState,
      activeDoc2mdJob: finishedJob
    }
  };
}

async function manageDoc2mdAction(input = {}) {
  const act = input.action || "status";
  if (act === "doctor") {
    const scriptsDir = resolveDoc2mdScriptsDir();
    const res = await runPython(join(scriptsDir, "doctor.py"), ["--json"], 20000);
    const panelState = await getFullPanelState();
    return {
      text: `文档解析环境自检 (${res.parsed?.status || "unknown"})：\n${(res.parsed?.checks || []).map((c) => `- [${c.status}] ${c.name}: ${c.detail}`).join("\n")}`,
      structured: {
        ...panelState,
        doc2mdDoctor: res.parsed
      }
    };
  }
  if (act === "status") {
    const job = input.jobId ? loadDoc2mdJob(input.jobId) : listRecentDoc2mdJobs(1)[0] || null;
    const panelState = await getFullPanelState();
    return {
      text: job ? `解析任务 \`${job.jobId}\` 状态：${job.phaseLabel || job.status}` : "当前暂无文档解析任务。",
      structured: {
        ...panelState,
        activeDoc2mdJob: job
      }
    };
  }
  if (act === "resume" || act === "rebuild") {
    if (!input.jobId) throw new Error(`操作 ${act} 需要提供 jobId。`);
    const cur = loadDoc2mdJob(input.jobId);
    if (activeDoc2mdPromises.has(cur.jobId)) {
      const panelState = await getFullPanelState();
      return {
        text: `任务 \`${cur.jobId}\` 正在后台运行中：${cur.phaseLabel}`,
        structured: { ...panelState, activeDoc2mdJob: cur }
      };
    }
    const execPromise = executeDoc2mdJob(cur.jobId, {
      rebuildOnly: act === "rebuild",
      chaptersJsonPath: input.chaptersJsonPath || null,
      repairsJsonPath: input.repairsJsonPath || null
    }).finally(() => {
      activeDoc2mdPromises.delete(cur.jobId);
    });
    activeDoc2mdPromises.set(cur.jobId, execPromise);

    if (input.async) {
      let settled = false;
      let firstErr = null;
      await Promise.race([
        execPromise.then(
          () => { settled = true; },
          (e) => { settled = true; firstErr = e; }
        ),
        new Promise((r) => setTimeout(r, 1200))
      ]);
      if (settled && firstErr) throw firstErr;
      const updated = loadDoc2mdJob(cur.jobId);
      const panelState = await getFullPanelState();
      return {
        text: `🔄 已启动${act === "rebuild" ? "重新装配与校验" : "断点续传"}任务 \`${cur.jobId}\`（${updated.phaseLabel}）。`,
        structured: { ...panelState, activeDoc2mdJob: updated }
      };
    }
    const done = await execPromise;
    const panelState = await getFullPanelState();
    return {
      text: `✅ 任务 \`${done.jobId}\` 已完成：${done.phaseLabel}\n- 输出目录：\`${done.outDir}\``,
      structured: { ...panelState, activeDoc2mdJob: done }
    };
  }
  if (act === "validate") {
    await prepareDocumentRuntime();
    const job = input.jobId ? loadDoc2mdJob(input.jobId) : null;
    const targetOut = input.outDir
      ? isAbsolutePath(input.outDir)
        ? resolve(input.outDir)
        : resolve(WORKSPACE_ROOT, input.outDir)
      : job?.outDir;
    if (!targetOut || !existsSync(targetOut)) {
      throw new Error("未找到要校验的知识库输出目录。");
    }
    assertReadablePath(targetOut);
    const scriptsDir = resolveDoc2mdScriptsDir();
    const isTempValDir = !job;
    const jobDir = job ? resolveDoc2mdJobDir(job.jobId, true) : mkdtempSync(join(DOC2MD_JOBS_DIR, ".val-"));
    try {
      const siOutDir = job?.siOutDir && existsSync(job.siOutDir) ? resolve(job.siOutDir) : null;
      const resourceRoot = siOutDir ? dirname(targetOut) : null;
      const mainReports = await runStandaloneDoc2mdValidators(
        targetOut,
        jobDir,
        join(scriptsDir, "validate_output.py"),
        join(scriptsDir, "extract_formulas.py"),
        join(scriptsDir, "validate_math.cjs"),
        resourceRoot,
        ""
      );
      let siReports = null;
      if (siOutDir) {
        siReports = await runStandaloneDoc2mdValidators(
          siOutDir,
          jobDir,
          join(scriptsDir, "validate_output.py"),
          join(scriptsDir, "extract_formulas.py"),
          join(scriptsDir, "validate_math.cjs"),
          dirname(targetOut),
          "si-"
        );
      }
      const stats = collectDoc2mdOutputStats(
        targetOut,
        siOutDir,
        null,
        jobDir,
        null,
        {
          ...mainReports,
          ...(siReports
            ? {
                siValidateOutputPath: siReports.validateOutputPath,
                siFormulasPath: siReports.formulasPath,
                siMathReportPath: siReports.mathReportPath
              }
            : {})
        }
      );
      let updatedJob = job;
      if (job) {
        updatedJob = saveDoc2mdJob(job.jobId, { ...job, stats });
      }
      const panelState = await getFullPanelState();
      return {
        text: `🔍 校验完成（\`${targetOut}\`）：Markdown ${stats.markdownCount} 份 · 图片 ${stats.totalImageCount} 张 · 公式 ${stats.formulaCount} 处（结构校验: ${stats.outputValidationPassed ? "通过" : "未通过"} · KaTeX: ${stats.mathPassed ? "100% 通过" : `${stats.mathErrors} 处报错`}）`,
        structured: { ...panelState, activeDoc2mdJob: updatedJob, validationStats: stats }
      };
    } finally {
      if (isTempValDir && existsSync(jobDir)) {
        rmSync(jobDir, { recursive: true, force: true });
      }
    }
  }
  if (act === "preview") {
    const job = input.jobId ? loadDoc2mdJob(input.jobId) : null;
    const targetOut = input.outDir || job?.outDir;
    if (!targetOut) throw new Error("请提供要预览的知识库目录 (outDir) 或任务 ID (jobId)。");
    const prev = await startKatexPreviewServer(targetOut, input.port || 8766, input.openInBrowser !== false);
    const panelState = await getFullPanelState();
    return {
      text: `🌐 KaTeX 本地渲染预览服务已就绪：[${prev.url}](${prev.url})（目录：\`${prev.outDir}\`）`,
      structured: { ...panelState, previewServer: prev, activeDoc2mdJob: job ? loadDoc2mdJob(job.jobId) : null }
    };
  }
  if (act === "stop_preview") {
    const job = input.jobId ? loadDoc2mdJob(input.jobId) : null;
    const targetOut = input.outDir || job?.outDir || null;
    const count = stopKatexPreviewServer(targetOut);
    const panelState = await getFullPanelState();
    return {
      text: `⏹ 已停止 ${count} 个本地 KaTeX 预览服务。`,
      structured: { ...panelState, stoppedPreviews: count }
    };
  }
  if (act === "delete") {
    if (!input.jobId) throw new Error("删除解析任务需要提供 jobId。");
    const jobDir = resolveDoc2mdJobDir(input.jobId, true);
    rmSync(jobDir, { recursive: true, force: true });
    const panelState = await getFullPanelState();
    return {
      text: `🗑 已移除解析任务记录 \`${input.jobId}\`（已产出的知识库 Markdown 文件保持不变）。`,
      structured: { ...panelState, deletedDoc2mdJobId: input.jobId }
    };
  }
  if (act === "clear_all") {
    let count = 0;
    if (existsSync(DOC2MD_JOBS_DIR)) {
      for (const d of readdirSync(DOC2MD_JOBS_DIR, { withFileTypes: true })) {
        if (d.isDirectory() && !activeDoc2mdPromises.has(d.name)) {
          rmSync(join(DOC2MD_JOBS_DIR, d.name), { recursive: true, force: true });
          count++;
        }
      }
    }
    const panelState = await getFullPanelState();
    return {
      text: `🧹 已清空 ${count} 条历史解析任务记录（已产出的知识库 Markdown 文件保持不变）。`,
      structured: { ...panelState, clearedDoc2mdCount: count }
    };
  }
  throw new Error(`未知的 manage_doc2md 操作: ${act}`);
}

async function getFullPanelState(activeRunId = null) {
  const state = loadState();
  const eff = getEffectiveConfig();
  const enrichCtx = createEnrichContext(state);
  const recentRuns = listRecentRuns(35);
  const targetRunId = activeRunId || state.lastRunId || recentRuns[0]?.runId || null;

  let activeBundle = null;
  let zoteroProbe = { zoteroOnline: false, collections: [], matches: {} };
  if (targetRunId) {
    try {
      const rawPapers = readJsonSafe(join(RUNS_DIR, basename(targetRunId), "papers.json"), []);
      const combinedForProbe = [...(Array.isArray(rawPapers) ? rawPapers : []), ...(state.basket || [])];
      zoteroProbe = await probeLocalZotero(combinedForProbe);
      activeBundle = loadRunBundle(targetRunId, zoteroProbe.matches, enrichCtx);
      eff.status.zotero_local = {
        online: zoteroProbe.zoteroOnline,
        url: eff.settings.zoteroUrl
      };
    } catch {
      activeBundle = null;
    }
  } else {
    zoteroProbe = await probeLocalZotero(state.basket || []);
    eff.status.zotero_local = {
      online: zoteroProbe.zoteroOnline,
      url: eff.settings.zoteroUrl
    };
  }

  const enrichedBasket = enrichPapersList(state.basket || [], state, zoteroProbe.matches, null, enrichCtx);
  const graphData = buildRelationshipGraph(
    activeBundle,
    recentRuns,
    enrichedBasket,
    state,
    zoteroProbe.matches,
    enrichCtx
  );

  const runById = new Map(recentRuns.map((r) => [r.runId, r]));
  const navHistory = (state.runNavHistory || [])
    .map((id) => runById.get(id))
    .filter(Boolean)
    .map((r) => ({
      runId: r.runId,
      action: r.action,
      modeLabel: r.modeLabel,
      humanTitle: r.humanTitle,
      count: r.paperCount || r.resultCount || 0
    }));
  if (activeBundle && !navHistory.some((h) => h.runId === activeBundle.runId)) {
    const curMeta = runById.get(activeBundle.runId);
    if (curMeta) {
      navHistory.push({
        runId: curMeta.runId,
        action: curMeta.action,
        modeLabel: curMeta.modeLabel,
        humanTitle: curMeta.humanTitle,
        count: curMeta.paperCount || curMeta.resultCount || 0
      });
    }
  }

  const slimEvidenceMap = {};
  for (const [k, ev] of Object.entries(state.evidenceMap || {})) {
    if (!ev || typeof ev !== "object") continue;
    const enr = ev.enriched;
    slimEvidenceMap[k] = {
      ...(ev.stage ? { stage: ev.stage } : {}),
      ...(ev.basis ? { basis: ev.basis } : {}),
      ...(ev.reason ? { reason: ev.reason } : {}),
      ...(Array.isArray(ev.user_tags) ? { user_tags: ev.user_tags } : {}),
      ...(ev.user_note !== undefined ? { user_note: ev.user_note } : {}),
      ...(ev.pdfPath ? { pdfPath: ev.pdfPath } : {}),
      ...(ev.epmc_saved_dir ? { epmc_saved_dir: ev.epmc_saved_dir } : {}),
      ...(Array.isArray(ev.epmc_suppl_files) ? { epmc_suppl_files: ev.epmc_suppl_files } : {}),
      ...(ev.sciverse_saved_dir ? { sciverse_saved_dir: ev.sciverse_saved_dir } : {}),
      ...(Array.isArray(ev.sciverse_figure_files) ? { sciverse_figure_files: ev.sciverse_figure_files } : {}),
      ...(ev.sciverse_excerpt_path ? { sciverse_excerpt_path: ev.sciverse_excerpt_path } : {}),
      ...(ev.sciverse_materials_path ? { sciverse_materials_path: ev.sciverse_materials_path } : {}),
      ...(ev.doc2md_out_dir ? { doc2md_out_dir: ev.doc2md_out_dir } : {}),
      ...(ev.doc2md_md_path ? { doc2md_md_path: ev.doc2md_md_path } : {}),
      ...(ev.doc2md_si_out_dir ? { doc2md_si_out_dir: ev.doc2md_si_out_dir } : {}),
      ...(ev.doc2md_job_id ? { doc2md_job_id: ev.doc2md_job_id } : {}),
      ...(ev.zoteroMatch ? { zoteroMatch: ev.zoteroMatch } : {}),
      ...(ev.zoteroUndoneKey ? { zoteroUndoneKey: ev.zoteroUndoneKey, zoteroUndoneAt: ev.zoteroUndoneAt } : {}),
      ...(enr
        ? {
            enriched: {
              ...(enr.abstract ? { abstract: enr.abstract } : {}),
              ...(enr.journal ? { journal: enr.journal, venue: enr.venue } : {}),
              ...(enr.tldr ? { tldr: enr.tldr } : {}),
              ...(enr.pdfUrl ? { pdfUrl: enr.pdfUrl } : {})
            }
          }
        : {})
    };
  }

  return {
    configStatus: eff.status,
    settings: {
      ...eff.settings,
      zoteroCollections: zoteroProbe.collections || []
    },
    activeRun: activeBundle,
    recentRuns,
    runNavHistory: navHistory.slice(-8),
    lastZoteroImport: state.lastZoteroImport || null,
    evidenceMap: slimEvidenceMap,
    zoteroImports: Object.keys(state.zoteroImports || {}),
    basket: enrichedBasket,
    citationsPreview: generateCitations(enrichedBasket),
    graphData,
    doc2mdJobs: listRecentDoc2mdJobs(25)
  };
}

const TOOLS = [
  {
    name: "open_panel",
    description:
      "打开「三木文献检索 (Sanmu Paper Search)」工作台，查看检索、关系图谱、收藏篮、API 配置和文档解析。支持原生侧栏；终端宿主返回本机浏览器工作台链接。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        runId: {
          type: "string",
          description: "可选。指定要在面板中打开展示的历史运行批次 ID。"
        }
      }
    },
    execute: async (input) => {
      const panelState = await getFullPanelState(input?.runId);
      const portableHost = resolveEnvPath(process.env.SANMU_HOST) && process.env.SANMU_HOST !== "zcode";
      const panel = portableHost && process.env.SANMU_PANEL_WORKER !== "1" ? await ensureBrowserPanel() : null;
      return {
        text: panel
          ? `三木科研工作台已就绪，请在本机浏览器打开：${panel.url}\n${panelState.activeRun ? summarizeRunForModel(panelState.activeRun) : "可使用完整检索、关系图谱、收藏篮、API 配置和文档解析功能。"}`
          : panelState.activeRun
            ? summarizeRunForModel(panelState.activeRun)
            : "三木文献检索工作台已就绪。可选择文献源、配置 API 或直接发起检索。",
        structured: { ...panelState, ...(panel ? { panelUrl: panel.url } : {}) }
      };
    }
  },
  {
    name: "doctor_status",
    description:
      "运行三木文献检索环境与 API 凭证自检（doctor），检查 OpenAlex、Semantic Scholar、Crossref、Europe PMC、Exa、Serper 及本机 Zotero 状态，仅返回布尔配置状态与来源通道，不泄露密钥值。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {}
    },
    execute: async () => {
      const pyRes = await runPython(PAPER_SEARCH_PY, ["doctor"]);
      if (pyRes.exitCode !== 0 || pyRes.parsed?.status === "failed") throw new Error(pythonFailure(pyRes, "Python 环境自检失败"));
      const zoteroProbe = await probeLocalZotero([]);
      const panelState = await getFullPanelState();
      const summary = {
        doctor: pyRes.parsed,
        channels: panelState.configStatus,
        zoteroOnline: zoteroProbe.zoteroOnline,
        settings: panelState.settings
      };
      return {
        text: `环境自检完成：Python 支持=${Boolean(pyRes.parsed?.python_supported)}，Zotero 本地在线=${zoteroProbe.zoteroOnline}。\n凭证通道状态：${JSON.stringify(panelState.configStatus, null, 2)}`,
        structured: { ...panelState, doctorReport: summary }
      };
    }
  },
  {
    name: "copy_clipboard",
    description: "将文本写入本机系统剪贴板（供面板 UI 在沙箱受限环境下直接复制参考文献、DOI 或文件内容）。",
    visibility: ["app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        text: { type: "string", description: "要复制到系统剪贴板的文本内容" }
      },
      required: ["text"]
    },
    execute: async (input) => {
      const ok = copyTextToSystemClipboard(input?.text || "");
      if (!ok) {
        throw new Error("系统剪贴板写入失败，请手动选中文本按 Ctrl+C 复制。");
      }
      return {
        text: "已复制到系统剪贴板。",
        structured: { copied: true }
      };
    }
  },
  {
    name: "save_config",
    description:
      "在插件私有数据区保存或更新 API 凭证（OpenAlex、Semantic Scholar、Exa、Serper、Polite Pool 邮箱）及默认保存/导出路径配置，保存后无需重启立即生效。",
    visibility: ["app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        politeEmail: { type: "string" },
        openalexApiKey: { type: "string" },
        s2ApiKey: { type: "string" },
        exaApiKey: { type: "string" },
        serperApiKey: { type: "string" },
        easyscholarSecretKey: { type: "string" },
        ai4scholarApiKey: { type: "string" },
        sciverseApiKey: { type: "string" },
        mineruApiToken: { type: "string" },
        mineruApiKey: { type: "string" },
        zoteroUrl: { type: "string" },
        exportSubdir: { type: "string" },
        doc2mdSubdir: { type: "string" },
        defaultSources: { type: "array", items: { type: "string" } },
        defaultLimit: { type: "integer", minimum: 1, maximum: 20 },
        clearKeys: { type: "array", items: { type: "string" } }
      }
    },
    execute: async (input) => {
      savePanelConfig(input || {});
      invalidateZoteroProbeCache();
      const panelState = await getFullPanelState();
      return {
        text: "配置已更新并立即生效。",
        structured: panelState
      };
    }
  },
  {
    name: "search_papers",
    description:
      "执行多源学术主题检索（支持 openalex、semantic-scholar、crossref 自由组合、年份范围过滤与单源候选预算 1..20），自动合并去重、核对 Zotero 本地库并同步到侧边栏面板。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        query: { type: "string", description: "英文学术检索词（建议保留化学式、反应名与核心术语）" },
        sources: {
          type: "array",
          items: { type: "string", enum: ["openalex", "semantic-scholar", "crossref"] },
          description: "选择的文献源列表，默认 ['openalex', 'semantic-scholar', 'crossref']"
        },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 10 },
        yearFrom: { type: "integer", description: "起始年份（如 2022）" },
        yearTo: { type: "integer", description: "结束年份（如 2026）" },
        sort: {
          type: "string",
          enum: ["relevance", "cited", "date"],
          default: "relevance",
          description: "可选。上游召回排序方式：relevance（默认相关性）、cited（历史高被引优先）、date（最新发表优先）"
        }
      },
      required: ["query"]
    },
    execute: async (input) => {
      const runId = makeRunId("search");
      const outDir = join(RUNS_DIR, runId);
      const srcs =
        Array.isArray(input.sources) && input.sources.length
          ? input.sources.join(",")
          : "openalex,semantic-scholar,crossref";
      const args = [
        "search",
        String(input.query).trim(),
        "--sources",
        srcs,
        "--limit",
        String(input.limit || 10),
        "--out",
        outDir
      ];
      if (input.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (input.yearTo) args.push("--year-to", String(input.yearTo));
      if (input.sort && ["relevance", "cited", "date"].includes(input.sort) && input.sort !== "relevance") {
        args.push("--sort", input.sort);
      }
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Search failed");
      updateStateTransaction((state) => {
        recordMainRunNavigation(state, runId);
      });

      const panelState = await getFullPanelState(runId);
      return {
        text: summarizeRunForModel(panelState.activeRun),
        structured: panelState
      };
    }
  },
  {
    name: "lookup_paper",
    description:
      "通过稳定标识符（DOI、openalex:W...、s2:...、pmid:...、pmcid:...、arxiv:...）精确核验单篇论文题录，支持可选期望标题核对（title_verified）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        id: { type: "string", description: "论文稳定标识符，如 10.1002/adma.202313513 或 arxiv:2401.00001" },
        title: { type: "string", description: "可选。期望匹配的完整论文标题，用于核验是否张冠李戴" },
        sources: {
          type: "array",
          items: { type: "string", enum: ["openalex", "semantic-scholar", "crossref"] }
        },
        preserveActiveRun: {
          type: "boolean",
          description: "可选。设为 true 时保持当前主检索结果页不跳转，将本次结果作为子结果返回"
        }
      },
      required: ["id"]
    },
    execute: async (input) => {
      const cleanId = String(input.id).trim();
      const runId = makeRunId("lookup");
      const outDir = join(RUNS_DIR, runId);
      const srcs =
        Array.isArray(input.sources) && input.sources.length
          ? input.sources.join(",")
          : pickCompatibleSources(cleanId, true).join(",");
      const args = ["lookup", cleanId, "--sources", srcs, "--out", outDir];
      if (input.title && String(input.title).trim()) {
        args.push("--title", String(input.title).trim());
      }
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }
      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Lookup failed");
      let activeTargetRunId = runId;
      if (!input.preserveActiveRun) {
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }
      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input.preserveActiveRun) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      return {
        text: summarizeRunForModel(subRun || panelState.activeRun),
        structured: subRun ? { ...panelState, subRun } : panelState
      };
    }
  },
  {
    name: "explore_related",
    description:
      "从种子论文出发扩展引文网络：支持查询参考文献（references，向前溯源）、施引文献（citations，向后追踪最新工作）或相似推荐（recommendations，横向拓展）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        id: { type: "string", description: "种子论文标识符（如 DOI、openalex:W...、s2:...）" },
        relation: {
          type: "string",
          enum: ["references", "citations", "recommendations"],
          default: "references"
        },
        sources: {
          type: "array",
          items: { type: "string", enum: ["openalex", "semantic-scholar"] }
        },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 10 },
        preserveActiveRun: {
          type: "boolean",
          description: "可选。设为 true 时在当前论文卡片内联展开关联文献，不顶掉当前主检索结果列表"
        }
      },
      required: ["id"]
    },
    execute: async (input) => {
      const state = loadState();
      const rawId = String(input.id).trim();
      const { resolvedId } = resolveBestSeedForRelated(rawId, state);
      const cleanId = resolvedId || rawId;
      const rel = input.relation || "references";
      const runId = makeRunId(`rel-${rel}`);
      const outDir = join(RUNS_DIR, runId);
      const srcs =
        Array.isArray(input.sources) && input.sources.length
          ? input.sources.join(",")
          : pickCompatibleSources(cleanId, false).join(",");
      const args = [
        "related",
        cleanId,
        "--relation",
        rel,
        "--sources",
        srcs,
        "--limit",
        String(input.limit || 10),
        "--out",
        outDir
      ];
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }
      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Related exploration failed");
      let activeTargetRunId = runId;
      if (!input.preserveActiveRun) {
        updateStateTransaction((latestState) => {
          recordMainRunNavigation(latestState, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }

      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input.preserveActiveRun) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      return {
        text: summarizeRunForModel(subRun || panelState.activeRun),
        structured: subRun ? { ...panelState, subRun } : panelState
      };
    }
  },
  {
    name: "search_author",
    description:
      "学者消歧与作品追踪：支持 (1) 按姓名搜候选学者；(2) 通过 --from-paper 从种子论文直接锚定同名学者的稳定 ID；(3) 按已核准的 authorId 获取该学者的代表作。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        provider: {
          type: "string",
          enum: ["openalex", "semantic-scholar"],
          default: "openalex"
        },
        query: { type: "string", description: "学者姓名（可选，配合 fromPaper 时作为姓名过滤）" },
        authorId: { type: "string", description: "已核准的稳定学者 ID（如 OpenAlex 的 A... 或 S2 数字 ID）" },
        fromPaper: { type: "string", description: "种子论文 DOI 或稳定 ID，从该论文作者列表直接锚定学者 ID" },
        topic: {
          type: "string",
          description: "可选。课题关键词过滤（如 photocatalytic / single atom），用于同名学者消歧并筛选特定课题方向代表作"
        },
        sort: {
          type: "string",
          enum: ["cited", "date"],
          description: "可选。拉取学者代表作时的排序方式：cited（高被引优先）或 date（最新发表优先）"
        },
        yearFrom: { type: "integer" },
        yearTo: { type: "integer" },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 10 },
        preserveActiveRun: {
          type: "boolean",
          description: "可选。设为 true 时在当前论文卡片内联展开学者代表作，不顶掉当前主检索结果列表"
        }
      },
      required: ["provider"]
    },
    execute: async (input) => {
      let runId = makeRunId("author");
      let outDir = join(RUNS_DIR, runId);
      const args = ["author"];
      const hasAuthorId = Boolean(input.authorId && String(input.authorId).trim());
      const hasFromPaper = Boolean(!hasAuthorId && input.fromPaper && String(input.fromPaper).trim());
      if (!hasAuthorId && input.query && String(input.query).trim()) {
        args.push(String(input.query).trim());
      }
      args.push("--provider", input.provider || "openalex", "--limit", String(input.limit || 10), "--out", outDir);
      if (hasAuthorId) {
        const cleanAid = String(input.authorId).trim().replace(/^https?:\/\/openalex\.org\//i, "");
        args.push("--author-id", cleanAid);
      } else if (hasFromPaper) {
        args.push("--from-paper", String(input.fromPaper).trim());
      }
      if (input.topic && String(input.topic).trim()) {
        args.push("--topic", String(input.topic).trim());
      }
      if (input.sort && ["cited", "date"].includes(input.sort)) {
        args.push("--sort", input.sort);
      }
      if (input.yearFrom && (hasAuthorId || hasFromPaper)) args.push("--year-from", String(input.yearFrom));
      if (input.yearTo && (hasAuthorId || hasFromPaper)) args.push("--year-to", String(input.yearTo));
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      // A confirmed S2 author ID must never silently become an OpenAlex name search.
      requirePythonBundle(pyRes, outDir, "Author query failed");
      let activeTargetRunId = runId;
      if (!input.preserveActiveRun) {
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }

      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input.preserveActiveRun) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      const targetBundle = subRun || panelState.activeRun;
      const candidates = targetBundle?.run?.author_candidates || [];
      return {
        text:
          summarizeRunForModel(targetBundle) +
          (candidates.length
            ? `\n\n#### 学者候选列表（共 ${candidates.length} 人）：\n` +
              candidates
                .map(
                  (c, i) =>
                    `${i + 1}. **${c.name}** (\`${c.provider}:${c.author_id}\`) — 机构: ${(c.affiliations || []).join("; ") || "未注明"} | 作品数: ${c.works_count ?? "n/a"} | 被引: ${c.cited_by_count ?? "n/a"}`
                )
                .join("\n")
            : ""),
        structured: subRun ? { ...panelState, subRun } : panelState
      };
    }
  },
  {
    name: "search_epmc",
    description:
      "调用 Europe PMC 公开接口检索论文、预印本、撤稿/勘误记录，或按需下载补充材料（SI PDF 及高清原图压缩包）到指定保存目录。无需 API Key。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        query: { type: "string", description: "检索词，或 DOI / PMID / PMCID" },
        id: { type: "string", description: "可选。显式指定 DOI、PMID 或 PMCID（如 PMC12710600）" },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 10 },
        yearFrom: { type: "integer" },
        yearTo: { type: "integer" },
        sort: { type: "string", enum: ["relevance", "cited", "date"], default: "relevance" },
        hasSuppl: { type: "boolean", description: "仅筛选含有补充材料 (SI) 的论文" },
        openAccess: { type: "boolean", description: "仅筛选开放获取论文" },
        hasData: { type: "boolean", description: "仅筛选含关联数据集的论文" },
        preprints: { type: "boolean", description: "仅筛选预印本" },
        hasFulltext: { type: "boolean", description: "仅筛选 Europe PMC 收录全文的论文" },
        fetchMode: {
          type: "string",
          enum: ["none", "all", "xml", "suppl"],
          default: "none",
          description: "是否按需拉取补充材料包 (none/suppl/all/xml，默认 none)"
        },
        saveDir: {
          type: "string",
          description: "可选。当下载 SI 补充材料包时，同步保存到的目标文件夹路径（支持工作区相对路径或本地绝对路径）"
        },
        preserveActiveRun: {
          type: "boolean",
          description: "可选。设为 true 时保持当前主检索结果不跳转，仅在详情页按需抓取 SI 补充材料包"
        }
      }
    },
    execute: async (input) => {
      const runId = makeRunId("epmc");
      const outDir = join(RUNS_DIR, runId);
      const args = ["epmc"];
      if (input.query && String(input.query).trim()) args.push(String(input.query).trim());
      if (input.id && String(input.id).trim()) args.push("--id", String(input.id).trim());
      const fetchMode = input.fetchMode || "none";
      args.push(
        "--limit",
        String(input.limit || 10),
        "--sort",
        input.sort || "relevance",
        "--fetch",
        fetchMode,
        "--out",
        outDir
      );
      if (input.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (input.yearTo) args.push("--year-to", String(input.yearTo));
      if (input.hasSuppl) args.push("--has-suppl");
      if (input.openAccess) args.push("--open-access");
      if (input.hasData) args.push("--has-data");
      if (input.preprints) args.push("--preprints");
      if (input.hasFulltext) args.push("--has-fulltext");
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args, 240000);
      requirePythonBundle(pyRes, outDir, "Europe PMC query failed");

      // Only copy extracted SI supplementary files (never copy fulltext.xml into user's save directory)
      let exportedSaveDir = null;
      if (fetchMode !== "none") {
        const supplSrcDir = join(outDir, "supplementary");
        const hasSuppl = existsSync(supplSrcDir) && statSync(supplSrcDir).isDirectory();
        if (hasSuppl) {
          const baseSaveDir = resolveTargetSaveDir(input.saveDir);
          const papers = readJsonSafe(join(outDir, "papers.json"), []);
          const firstPaper = papers[0] || {};
          const folderName = `${makeSafeFileStem(firstPaper.year, firstPaper.title, input.id || input.query || runId)}_EPMC`;
          exportedSaveDir = uniqueDestination(baseSaveDir, folderName);
          mkdirSync(exportedSaveDir, { recursive: true });
          const rootReal = realpathSync(supplSrcDir);
          const copySupplTree = (src, dst) => {
            if (!isWithin(rootReal, realpathSync(src))) throw new Error("补充材料中存在越界文件链接，已停止保存。");
            assertReadablePath(src);
            mkdirSync(dst, { recursive: true });
            for (const entry of readdirSync(src, { withFileTypes: true })) {
              const from = join(src, entry.name);
              const to = join(dst, entry.name);
              if (entry.isSymbolicLink()) throw new Error("补充材料中存在文件链接，已停止保存。");
              if (entry.isDirectory()) copySupplTree(from, to);
              else if (entry.isFile()) {
                assertReadablePath(from);
                copyFileSync(from, to, fsConstants.COPYFILE_EXCL);
              } else throw new Error("补充材料中存在不支持的文件类型，已停止保存。");
            }
          };
          copySupplTree(supplSrcDir, join(exportedSaveDir, "supplementary"));
          const copiedSupplFiles = [];
          const collectSupplFiles = (dir) => {
            for (const entry of readdirSync(dir, { withFileTypes: true })) {
              const full = join(dir, entry.name);
              if (entry.isDirectory()) collectSupplFiles(full);
              else if (entry.isFile()) copiedSupplFiles.push(full);
            }
          };
          collectSupplFiles(join(exportedSaveDir, "supplementary"));
          const firstKey = firstPaper.paper_key || (firstPaper.title || firstPaper.identifiers ? computePaperKey(firstPaper) : null);
          updateStateTransaction((exportState) => {
            exportState.runExports = { ...(exportState.runExports || {}), [runId]: exportedSaveDir };
            if (firstKey) {
              const prevEv = exportState.evidenceMap[firstKey] || {};
              exportState.evidenceMap[firstKey] = {
                ...prevEv,
                epmc_saved_dir: join(exportedSaveDir, "supplementary"),
                epmc_suppl_files: copiedSupplFiles,
                updatedAt: new Date().toISOString()
              };
            }
          });
        }
      }

      let activeTargetRunId = runId;
      if (!input.preserveActiveRun) {
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }

      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input.preserveActiveRun) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      return {
        text:
          summarizeRunForModel(subRun || panelState.activeRun) +
          (exportedSaveDir ? `\n\n✅ SI 补充材料包已自动保存至目录：\`${exportedSaveDir}\`` : ""),
        structured: { ...panelState, ...(subRun ? { subRun } : {}), exportedSaveDir }
      };
    }
  },
  {
    name: "search_exa",
    description:
      "使用 Exa 进行自然语言语义学术补搜（固定 category: publication），适合用一段话描述实验现象、反应机理或性能指标来寻找相关论文。需配置 EXA_API_KEY。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        query: { type: "string", description: "自然语言检索描述" },
        type: { type: "string", enum: ["auto", "lite"], default: "auto" },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 5 },
        yearFrom: { type: "integer" },
        yearTo: { type: "integer" },
        textChars: { type: "integer", minimum: 0, maximum: 10000, default: 3000 }
      },
      required: ["query"]
    },
    execute: async (input) => {
      const runId = makeRunId("exa");
      const outDir = join(RUNS_DIR, runId);
      const args = [
        "exa",
        String(input.query).trim(),
        "--type",
        input.type || "auto",
        "--limit",
        String(input.limit || 5),
        "--text-chars",
        String(input.textChars ?? 3000),
        "--out",
        outDir
      ];
      if (input.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (input.yearTo) args.push("--year-to", String(input.yearTo));
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Exa search failed");
      updateStateTransaction((state) => {
        recordMainRunNavigation(state, runId);
      });

      const panelState = await getFullPanelState(runId);
      return {
        text: summarizeRunForModel(panelState.activeRun),
        structured: panelState
      };
    }
  },
  {
    name: "search_serper",
    description:
      "使用 Serper 接口执行 Google Scholar（学术）、Patents（专利）、Search（网页）或 Webpage（页面正文提取）补搜。需配置 SERPER_API_KEY。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        query: { type: "string", description: "检索词（webpage 模式下可为空并传 url）" },
        type: {
          type: "string",
          enum: [
            "scholar",
            "patents",
            "search",
            "webpage"
          ],
          default: "scholar"
        },
        url: { type: "string", description: "当 type 为 webpage 时的目标公开网页 URL" },
        params: {
          type: "object",
          description: "路由专用参数。支持共享 CLI 对当前学术/专利/网页 type 允许的参数。不要与 query/url/year/page 重复。",
          properties: {
            gl: { type: "string" }, hl: { type: "string" },
            sortBy: { type: "string", enum: ["mostRelevant", "newest", "highestRating", "lowestRating"] },
            topicId: { type: "string" }, nextPageToken: { type: "string" }
          }
        },
        limit: { type: "integer", minimum: 1, maximum: 20, default: 10 },
        page: { type: "integer", minimum: 1, maximum: 100 },
        pages: { type: "integer", minimum: 1, maximum: 3, default: 1 },
        yearFrom: { type: "integer", description: "仅 scholar 模式支持" },
        yearTo: { type: "integer", description: "仅 scholar 模式支持" }
      }
    },
    execute: async (input) => {
      const mode = input.type || "scholar";
      const runId = makeRunId(`serper-${mode}`);
      const outDir = join(RUNS_DIR, runId);
      const args = ["serper"];
      if (input.query && String(input.query).trim()) args.push(String(input.query).trim());
      args.push(
        "--type",
        mode,
        "--limit",
        String(input.limit || 10),
        "--pages",
        String(input.pages || 1),
        "--out",
        outDir
      );
      if (input.url && String(input.url).trim()) args.push("--url", String(input.url).trim());
      if (input.params) args.push("--params", JSON.stringify(input.params));
      if (input.page) args.push("--page", String(input.page));
      if (mode === "scholar" && input.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (mode === "scholar" && input.yearTo) args.push("--year-to", String(input.yearTo));

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Serper search failed");
      updateStateTransaction((state) => {
        recordMainRunNavigation(state, runId);
      });

      const panelState = await getFullPanelState(runId);
      return {
        text: summarizeRunForModel(panelState.activeRun),
        structured: panelState
      };
    }
  },
  {
    name: "resolve_oa_and_pdf",
    description:
      "定位开放获取（OA）全文位置（含 OpenAlex、S2、Unpaywall），从已核实的公开 HTTPS PDF 链接安全下载 PDF 文件，或一键认领浏览器刚下载到本机 Downloads 的 PDF（claim_browser_pdf，自动校验首屏 DOI/标题并按规范重命名归档到 saveDir）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["resolve_oa", "download_pdf", "claim_browser_pdf"],
          default: "resolve_oa"
        },
        inputIdOrRun: {
          type: "string",
          description: "当 action=resolve_oa 时：传入论文 DOI/稳定 ID，或历史运行批次 ID"
        },
        preserveActiveRun: {
          type: "boolean",
          description: "设为 true 时保持当前主结果，将 OA 定位结果作为 subRun 返回。"
        },
        unpaywall: {
          type: "string",
          enum: ["auto", "never", "required"],
          default: "auto"
        },
        pdfUrl: {
          type: "string",
          description: "当 action=download_pdf 时：公开 HTTPS PDF 直链"
        },
        fallbackUrls: {
          type: "array",
          items: { type: "string" },
          description: "可选。当主链接需要登录或被反爬拦截时，自动依次尝试的备选 OA PDF 链接（如 arXiv / 机构库）"
        },
        doi: { type: "string", description: "可选。用于下载后首屏文字层身份比对的 DOI" },
        title: { type: "string", description: "可选。用于文件命名与下载后首屏文字层身份比对的标题" },
        year: { type: ["integer", "string"], description: "可选。论文发表年份，用于文件命名" },
        paperKey: { type: "string", description: "可选。关联的论文键值，下载成功后自动更新阅读状态" },
        saveDir: {
          type: "string",
          description: "可选。PDF 保存的目标文件夹路径（支持工作区相对路径或本地绝对路径，未传时默认保存到配置的导出目录）"
        }
      }
    },
    execute: async (input) => {
      if (input.action === "claim_browser_pdf") {
        return claimBrowserPdf(input);
      }
      if (input.action === "download_pdf") {
        const cleanUrl = input.pdfUrl ? String(input.pdfUrl).trim() : "";
        const cleanDoi = input.doi ? String(input.doi).trim() : "";
        if (!cleanUrl && !cleanDoi) throw new Error("pdfUrl or doi is required for download_pdf");
        const runId = makeRunId("pdf");
        const outDir = join(RUNS_DIR, runId);
        const args = [];
        if (cleanUrl) args.push(cleanUrl);
        args.push("--out", outDir);
        if (cleanDoi) {
          args.push("--doi", cleanDoi, "--resolve-doi");
        }
        if (input.title) args.push("--title", String(input.title).trim());
        if (Array.isArray(input.fallbackUrls)) {
          for (const fb of input.fallbackUrls) {
            if (fb && String(fb).trim() && String(fb).trim() !== cleanUrl) {
              args.push("--fallback-url", String(fb).trim());
            }
          }
        }
        const pyRes = await runPython(FETCH_PDF_PY, args, 120000);
        if (pyRes.exitCode !== 0 || !existsSync(join(outDir, "acquisition.json"))) {
          throw new Error(
            pyRes.parsed?.error === "not_pdf: response does not start with a PDF header" || pyRes.parsed?.error === "http_error"
              ? "该出版商链接存在反爬验证或仅提供网页预览（非直接 PDF 数据流）。建议点击「🔓 查找免费 OA 全文 (+Unpaywall)」寻找 arXiv/仓储版本，或点击「🌐 原文」在浏览器中下载。"
              : pyRes.parsed?.error || pyRes.stderr || "PDF 下载失败或该链接需要登录/非直接 PDF 流，请点击「🌐 原文」在浏览器中下载"
          );
        }

        // Copy verified PDF to user's chosen saveDir with a clean, readable filename!
        const destDir = resolveTargetSaveDir(input.saveDir);
        const fileStem = makeSafeFileStem(input.year, input.title, input.doi || runId, input.doi || input.paperKey || "");
        const savedPdfPath = uniqueDestination(destDir, `${fileStem}.pdf`, input.doi || input.paperKey || runId);
        copyFileSync(join(outDir, "paper.pdf"), savedPdfPath, fsConstants.COPYFILE_EXCL);

        const acqPath = join(outDir, "acquisition.json");
        const acq = readJsonSafe(acqPath, {});
        acq.saved_pdf_path = savedPdfPath;
        acq.saved_dir = destDir;
        writeJsonSafe(acqPath, acq);

        const key = input.paperKey || (input.doi ? `doi:${String(input.doi).toLowerCase()}` : null);
        updateStateTransaction((state) => {
          if (key) {
            const prev = state.evidenceMap[key] || {};
            state.evidenceMap[key] = {
              ...prev,
              stage: prev.stage === "read" ? "read" : acq.identity?.status === "matched" ? "identity_checked" : "file_obtained",
              pdfPath: savedPdfPath,
              acquisition: acq,
              updatedAt: new Date().toISOString()
            };
          }
          // Also update basket item if present
          if (key && Array.isArray(state.basket)) {
            state.basket = state.basket.map((bp) =>
              (bp.paper_key || computePaperKey(bp)) === key
                ? { ...bp, local_pdf_path: savedPdfPath }
                : bp
            );
          }
        });

        const panelState = await getFullPanelState();
        return {
          text: `PDF 已下载保存至：\`${savedPdfPath}\`\n- 首屏身份核验: \`${acq.identity?.status || "未执行"}\``,
          structured: {
            ...panelState,
            lastDownload: {
              runId,
              outDir,
              pdfPath: savedPdfPath,
              savedDir: destDir,
              acquisition: acq
            }
          }
        };
      }

      const target = String(input.inputIdOrRun || "").trim();
      if (!target) throw new Error("inputIdOrRun is required for resolve_oa");
      const candidateRunPath = join(RUNS_DIR, basename(target), "papers.json");
      const isRunInput = existsSync(candidateRunPath);
      const actualInput = isRunInput ? candidateRunPath : target;
      const runId = makeRunId("oa");
      const outDir = join(RUNS_DIR, runId);
      const args = ["oa", actualInput];
      if (!isRunInput) {
        const compatSrcs = pickCompatibleSources(target, false);
        args.push("--sources", compatSrcs.join(","));
      }
      args.push("--unpaywall", input.unpaywall || "auto", "--out", outDir);

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "OA resolution failed");
      const resolvedRaw = readJsonSafe(join(outDir, "papers.json"), []);
      let activeTargetRunId = runId;
      updateStateTransaction((state) => {
        if (Array.isArray(resolvedRaw) && resolvedRaw.length > 0) {
          for (const rp of resolvedRaw) {
            const pKey = (resolvedRaw.length === 1 ? input.paperKey : null) || rp.paper_key || computePaperKey(rp);
            if (!pKey) continue;
            const prevEv = state.evidenceMap[pKey] || {};
            const prevEnriched = prevEv.enriched || {};
            const mergedOa = [...(prevEnriched.oa_locations || []), ...(rp.oa_locations || [])];
            state.evidenceMap[pKey] = {
              ...prevEv,
              enriched: {
                ...prevEnriched,
                ...(rp.pdfUrl ? { pdfUrl: rp.pdfUrl } : {}),
                identifiers: { ...(prevEnriched.identifiers || {}), ...(rp.identifiers || {}) },
                oa_locations: mergedOa
              },
              updatedAt: new Date().toISOString()
            };
            if (Array.isArray(state.basket)) {
              state.basket = state.basket.map((bp) =>
                (bp.paper_key || computePaperKey(bp)) === pKey
                  ? {
                      ...bp,
                      ...(rp.pdfUrl ? { pdfUrl: rp.pdfUrl } : {}),
                      oa_locations: [...(bp.oa_locations || []), ...(rp.oa_locations || [])]
                    }
                  : bp
              );
            }
          }
        }
        if (!input.preserveActiveRun) {
          recordMainRunNavigation(state, runId);
        } else {
          activeTargetRunId = state.lastRunId;
        }
      });
      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input.preserveActiveRun) {
        const subProbe = await probeLocalZotero(Array.isArray(resolvedRaw) ? resolvedRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      return {
        text: summarizeRunForModel(subRun || panelState.activeRun),
        structured: subRun ? { ...panelState, subRun } : panelState
      };
    }
  },
  {
    name: "manage_runs",
    description:
      "管理与操作历史检索批次：支持切换查看指定批次（load）、删除指定历史记录（delete）、一键清空全部历史记录（clear_all）、多批次合并去重（merge）以及前后批次增量对比（diff）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        operation: {
          type: "string",
          enum: ["load", "delete", "clear_all", "merge", "diff"],
          default: "load"
        },
        runId: { type: "string", description: "当 operation=load / delete 时的批次 ID" },
        runIds: {
          type: "array",
          items: { type: "string" },
          description: "当 operation=merge 或批量 delete 时传入多个批次 ID"
        },
        beforeRunId: { type: "string", description: "当 operation=diff 时的旧批次 ID" },
        afterRunId: { type: "string", description: "当 operation=diff 时的新批次 ID" },
        zoteroJsonPath: { type: "string", description: "可选。merge 时附带的本地 Zotero 导出 JSON 路径" }
      },
      required: ["operation"]
    },
    execute: async (input) => {
      const op = input.operation || "load";
      if (op === "load") {
        if (input.runId) {
          resolveRunDir(input.runId);
          updateStateTransaction((state) => {
            recordMainRunNavigation(state, input.runId);
          });
        } else {
          loadState();
        }
        const panelState = await getFullPanelState(input.runId);
        return {
          text: panelState.activeRun ? summarizeRunForModel(panelState.activeRun) : "未找到指定批次。",
          structured: panelState
        };
      }
      if (op === "delete") {
        const targets = [...new Set([
          ...(input.runId ? [input.runId] : []),
          ...(Array.isArray(input.runIds) ? input.runIds : [])
        ])];
        if (!targets.length) throw new Error("请指定要删除的批次 ID。");
        const directories = targets.map((id) => resolveRunDir(id, false));
        let deletedCount = 0;
        for (const dir of directories) {
          if (existsSync(dir)) {
            rmSync(dir, { recursive: true, force: true });
            deletedCount++;
          }
        }
        updateStateTransaction((state) => {
          state.runNavHistory = (state.runNavHistory || []).filter((id) => !targets.includes(id));
          if (state.lastRunId && targets.includes(state.lastRunId)) {
            const remaining = listRecentRuns(1);
            state.lastRunId = remaining[0]?.runId || null;
          }
        });
        const panelState = await getFullPanelState();
        return {
          text: `已删除 ${deletedCount} 条历史记录。`,
          structured: { ...panelState, deletedCount }
        };
      }
      if (op === "clear_all") {
        let deletedCount = 0;
        if (existsSync(RUNS_DIR)) {
          for (const entry of readdirSync(RUNS_DIR, { withFileTypes: true })) {
            if (entry.isDirectory()) {
              rmSync(join(RUNS_DIR, entry.name), { recursive: true, force: true });
              deletedCount++;
            }
          }
        }
        updateStateTransaction((state) => {
          state.lastRunId = null;
          state.runNavHistory = [];
        });
        const panelState = await getFullPanelState();
        return {
          text: `已清空全部 ${deletedCount} 条搜索历史记录（收藏篮中的文献已保留）。`,
          structured: { ...panelState, deletedCount }
        };
      }
      if (op === "merge") {
        const ids = [...new Set(input.runIds || [])].map((id) => resolveRunDir(id));
        if (ids.length < 1) throw new Error("请至少选择一条记录进行合并");
        const runId = makeRunId("merged");
        const outDir = join(RUNS_DIR, runId);
        const args = ["merge", ...ids, "--out", outDir];
        if (input.zoteroJsonPath) args.push("--zotero", input.zoteroJsonPath);
        const pyRes = await runPython(PAPER_SEARCH_PY, args);
        requirePythonBundle(pyRes, outDir, "Merge failed");
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
        const panelState = await getFullPanelState(runId);
        return {
          text: summarizeRunForModel(panelState.activeRun),
          structured: panelState
        };
      }
      if (op === "diff") {
        if (!input.beforeRunId || !input.afterRunId) {
          throw new Error("请选择两条历史记录进行对比");
        }
        const runId = makeRunId("diff");
        const outDir = join(RUNS_DIR, runId);
        const args = [
          "diff",
          resolveRunDir(input.beforeRunId),
          resolveRunDir(input.afterRunId),
          "--out",
          outDir
        ];
        const pyRes = await runPython(PAPER_SEARCH_PY, args);
        requirePythonBundle(pyRes, outDir, "Diff failed");
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
        const panelState = await getFullPanelState(runId);
        return {
          text: summarizeRunForModel(panelState.activeRun),
          structured: panelState
        };
      }
      throw new Error(`Unsupported operation: ${op}`);
    }
  },
  {
    name: "read_run_file",
    description:
      "读取或在本机默认程序/资源管理器中打开指定文件或保存目录（支持打开已下载的 PDF、SI 补充材料文件夹或批次产物）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        runId: { type: "string" },
        relativePath: { type: "string", description: "批次目录内的相对路径，或已保存的本地文件/文件夹路径" },
        openInSystem: {
          type: "boolean",
          description: "是否直接用本机系统默认阅读器/资源管理器打开该文件或目录"
        }
      },
      required: ["relativePath"]
    },
    execute: async (input) => {
      let target;
      const rawRel = String(input.relativePath || "").trim();
      if (input.runId === "__custom_dir__") {
        target = resolveTargetSaveDir(rawRel);
      } else if (isAbsolutePath(rawRel)) {
        target = resolve(rawRel);
      } else if (input.runId && input.runId !== "local") {
        const runDir = resolveRunDir(input.runId);
        target = rawRel === "." ? runDir : resolve(runDir, rawRel);
        if (!isWithin(runDir, target) || (existsSync(target) && !isWithin(realpathSync(runDir), realpathSync(target)))) {
          throw new Error("文件路径不能越出指定批次目录。");
        }
      } else {
        target = resolve(WORKSPACE_ROOT, rawRel);
        if (!isWithin(WORKSPACE_ROOT, target)) throw new Error("相对文件路径不能越出工作区；请提供明确的本地绝对路径。");
      }

      assertReadablePath(target);
      if (!existsSync(target)) {
        throw new Error(`未找到目标文件或目录: ${target}`);
      }
      if (input.openInSystem) {
        const opened = openFileInSystem(target);
        return {
          text: opened ? `已在本机打开：\`${target}\`` : `无法自动打开，路径：\`${target}\``,
          structured: {
            runId: input.runId,
            relativePath: input.relativePath,
            fullPath: target,
            openedInSystem: opened
          }
        };
      }
      const st = statSync(target);
      if (st.isDirectory()) {
        const entries = readdirSync(target);
        return {
          text: `文件夹 \`${target}\`（共 ${entries.length} 项）`,
          structured: {
            runId: input.runId,
            relativePath: input.relativePath,
            fullPath: target,
            size: 0,
            isText: true,
            content: `目录路径: ${target}\n包含文件:\n${entries.map((e) => `- ${e}`).join("\n")}`
          }
        };
      }
      const ext = extname(target).toLowerCase();
      const isText = [".json", ".md", ".xml", ".txt", ".csv", ".html", ".bib", ".ris"].includes(ext);
      let content = "";
      let truncated = false;
      if (isText) {
        const raw = readFileSync(target, "utf8");
        if (raw.length > 120000) {
          content = raw.slice(0, 120000);
          truncated = true;
        } else {
          content = raw;
        }
      } else {
        content = `[文件: ${basename(target)}, 大小=${st.size.toLocaleString()} 字节]\n本地保存路径: ${target}\n可点击上方「🖥 用本机默认程序打开」直接查看此文件。`;
      }
      return {
        text: content,
        structured: {
          runId: input.runId,
          relativePath: input.relativePath,
          fullPath: target,
          size: st.size,
          isText,
          truncated,
          content
        }
      };
    }
  },
  {
    name: "manage_basket",
    description:
      "管理文献收藏篮（Basket）、阅读进度状态（discovered -> metadata_checked -> file_obtained -> identity_checked -> read）、一键跨源补全缺失摘要（enrich_abstract）及本机 Zotero 双向联动（save_to_zotero / undo_zotero_save / open_in_zotero）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: [
            "add",
            "remove",
            "clear",
            "update_evidence",
            "set_note_tags",
            "enrich_abstract",
            "save_to_zotero",
            "undo_zotero_save",
            "open_in_zotero"
          ],
          default: "add"
        },
        paper: { type: "object", description: "当 action=add / enrich_abstract / save_to_zotero 时传入的论文对象" },
        papers: {
          type: "array",
          items: { type: "object" },
          description: "可选。当 action=add 批量加入收藏篮时传入的多篇论文数组"
        },
        paperKey: { type: "string", description: "当 action=remove / update_evidence / set_note_tags / undo_zotero_save 时的 paper_key" },
        collectionKey: { type: "string", description: "可选。当 action=save_to_zotero 时指定存入的 Zotero 分类 Key" },
        zoteroItemKey: {
          type: "string",
          description: "当 action=open_in_zotero 或 undo_zotero_save 时指定的 Zotero 条目 Key"
        },
        stage: {
          type: "string",
          enum: ["discovered", "metadata_checked", "file_obtained", "identity_checked", "read", "excluded"]
        },
        basis: { type: "string", description: "筛选依据层级，如 metadata_only、abstract、fulltext" },
        reason: { type: "string", description: "入选/排除理由或阅读备注" },
        user_tags: {
          type: "array",
          items: { type: "string" },
          description: "可选。为该文献保存的课题标签列表（如 ['单原子Ni', '光催化析氢']），存入 Zotero 时自动同步为标签"
        },
        user_note: {
          type: "string",
          description: "可选。为该文献保存的一句话批注或笔记，存入 Zotero 时自动同步创建为子笔记"
        }
      },
      required: ["action"]
    },
    execute: async (input) => {
      let state = loadState();
      const act = input.action;
      if (act === "open_in_zotero") {
        const itemKey = String(input.zoteroItemKey || input.paper?.zotero_match?.itemKey || "").trim();
        if (!itemKey) throw new Error("未指定 Zotero 条目 Key");
        const uri = `zotero://select/library/items/${itemKey}`;
        const opened = openZoteroUriInSystem(uri);
        const panelState = await getFullPanelState();
        return {
          text: opened ? `已在 Zotero 桌面端定位打开条目 \`${itemKey}\`` : `请打开链接：${uri}`,
          structured: { ...panelState, zoteroOpened: { itemKey, uri, opened } }
        };
      }
      if (act === "save_to_zotero") {
        if (!input.paper) throw new Error("未提供要存入 Zotero 的论文数据");
        const zRes = await savePaperToLocalZotero(input.paper, input.collectionKey || "");
        const key = input.paper.paper_key || computePaperKey(input.paper);
        updateStateTransaction((latestState) => {
          const prev = latestState.evidenceMap[key] || {};
          latestState.evidenceMap[key] = {
            ...prev,
            zoteroUndoneKey: null,
            zoteroUndoneAt: null,
            zoteroMatch: {
              matched: true,
              itemKey: zRes.itemKey,
              attachmentKey: zRes.attachmentKey || null,
              noteKey: zRes.noteKey || null,
              createdZoteroItems: zRes.createdZoteroItems || [zRes.itemKey],
              createdByPlugin: Boolean(latestState.zoteroImports?.[zRes.itemKey]),
              undoAvailable: Boolean(latestState.zoteroImports?.[zRes.itemKey]),
              selectUri: zRes.selectUri,
              collections: zRes.collections || []
            },
            updatedAt: new Date().toISOString()
          };
          if (!zRes.alreadyExists && zRes.undoAvailable) {
            latestState.lastZoteroImport = {
              paperKey: key,
              itemKey: zRes.itemKey,
              attachmentKey: zRes.attachmentKey || null,
              noteKey: zRes.noteKey || null,
              createdZoteroItems: zRes.createdZoteroItems || [zRes.itemKey],
              title: input.paper.title || key,
              timestamp: new Date().toISOString()
            };
          }
        });
        const panelState = await getFullPanelState();
        return {
          text: zRes.alreadyExists
            ? `该论文已存在于本机 Zotero 库中（Key: \`${zRes.itemKey}\`）。`
            : `✅ 已成功将论文存入本机 Zotero（Key: \`${zRes.itemKey}\`${zRes.pdfAttached ? "，并已自动上传附加本地 PDF 全文" : ""}${zRes.noteCreated ? "，并已同步创建批注子笔记" : ""}）。支持随时点击「↩ 撤销导入」撤回。`,
          structured: { ...panelState, zoteroSaveResult: { ...zRes, paperKey: key } }
        };
      }
      if (act === "undo_zotero_save") {
        const targetKey = String(
          input.zoteroItemKey ||
            (input.paperKey && state.evidenceMap[input.paperKey]?.zoteroMatch?.itemKey) ||
            state.lastZoteroImport?.itemKey ||
            ""
        ).trim();
        const importRecord = state.zoteroImports?.[targetKey];
        if (!importRecord || (input.paperKey && importRecord.paperKey !== input.paperKey)) {
          throw new Error("该条目没有对应的插件创建核验记录，拒绝删除既有 Zotero 文献。");
        }
        const pKey = importRecord.paperKey;
        if (!targetKey) {
          throw new Error("未找到可撤销的 Zotero 导入记录（请传入 zoteroItemKey 或先执行一次存入 Zotero）。");
        }
        const delRes = await deletePaperFromLocalZotero(targetKey, importRecord);
        invalidateZoteroProbeCache(pKey);
        updateStateTransaction((latestState) => {
          delete latestState.zoteroImports[targetKey];
          if (pKey && latestState.evidenceMap[pKey]) {
            delete latestState.evidenceMap[pKey].zoteroMatch;
            latestState.evidenceMap[pKey].zoteroUndoneKey = targetKey;
            latestState.evidenceMap[pKey].zoteroUndoneAt = Date.now();
            latestState.evidenceMap[pKey].updatedAt = new Date().toISOString();
          }
          for (const [k, ev] of Object.entries(latestState.evidenceMap || {})) {
            if (ev?.zoteroMatch?.itemKey === targetKey) {
              delete latestState.evidenceMap[k].zoteroMatch;
              latestState.evidenceMap[k].zoteroUndoneKey = targetKey;
              latestState.evidenceMap[k].zoteroUndoneAt = Date.now();
            }
          }
          if (latestState.lastZoteroImport?.itemKey === targetKey) {
            latestState.lastZoteroImport = null;
          }
        });
        const panelState = await getFullPanelState();
        return {
          text: `↩ 已撤销导入并从本机 Zotero 库中移除条目 \`${targetKey}\`。`,
          structured: { ...panelState, zoteroUndoResult: { ...delRes, paperKey: pKey } }
        };
      }
      if (act === "enrich_abstract") {
        if (!input.paper) throw new Error("未提供要补全摘要的论文数据");
        const key = input.paper.paper_key || computePaperKey(input.paper);
        const enriched = await enrichPaperAbstractAndMetadata(input.paper);
        updateStateTransaction((latestState) => {
          const prev = latestState.evidenceMap[key] || {};
          latestState.evidenceMap[key] = {
            ...prev,
            stage:
              prev.stage && prev.stage !== "discovered"
                ? prev.stage
                : enriched.abstract
                  ? "metadata_checked"
                  : "discovered",
            enriched: { ...(prev.enriched || {}), ...enriched },
            updatedAt: new Date().toISOString()
          };
          // Also update basket if present
          if (Array.isArray(latestState.basket)) {
            latestState.basket = latestState.basket.map((bp) =>
              (bp.paper_key || computePaperKey(bp)) === key ? { ...bp, ...enriched } : bp
            );
          }
        });
        const panelState = await getFullPanelState();
        return {
          text: enriched.abstract
            ? `✅ 已跨源补全《${input.paper.title}》的完整摘要与题录信息！`
            : `已跨源补全题录信息（当前公开源暂未收录该文摘要文本）。`,
          structured: { ...panelState, enrichedPaperKey: key, enrichedData: enriched }
        };
      }
      updateStateTransaction((latestState) => {
        if (act === "add" && (input.paper || (Array.isArray(input.papers) && input.papers.length))) {
          const itemsToAdd = Array.isArray(input.papers) && input.papers.length ? input.papers : [input.paper];
          for (const pItem of itemsToAdd) {
            if (!pItem || typeof pItem !== "object") continue;
            const key = pItem.paper_key || computePaperKey(pItem);
            const mergedTags = input.user_tags !== undefined
              ? normalizeUserTags(input.user_tags)
              : pItem.user_tags !== undefined
                ? normalizeUserTags(pItem.user_tags)
                : undefined;
            const mergedNote = input.user_note !== undefined
              ? String(input.user_note ?? "").trim()
              : pItem.user_note !== undefined
                ? String(pItem.user_note ?? "").trim()
                : undefined;
            if (mergedTags !== undefined || mergedNote !== undefined) {
              setPaperNoteAndTags(latestState, key, mergedTags, mergedNote, pItem);
            }
            const exists = latestState.basket.some((p) => (p.paper_key || computePaperKey(p)) === key);
            if (!exists) {
              latestState.basket.push({
                ...pItem,
                paper_key: key,
                ...(mergedTags !== undefined ? { user_tags: mergedTags } : {}),
                ...(mergedNote !== undefined ? { user_note: mergedNote } : {})
              });
            } else if (mergedTags !== undefined || mergedNote !== undefined) {
              latestState.basket = latestState.basket.map((bp) =>
                (bp.paper_key || computePaperKey(bp)) === key
                  ? {
                      ...bp,
                      ...(mergedTags !== undefined ? { user_tags: mergedTags } : {}),
                      ...(mergedNote !== undefined ? { user_note: mergedNote } : {})
                    }
                  : bp
              );
            }
          }
        } else if (act === "remove" && input.paperKey) {
          latestState.basket = latestState.basket.filter(
            (p) => (p.paper_key || computePaperKey(p)) !== input.paperKey
          );
        } else if (act === "clear") {
          latestState.basket = [];
        } else if ((act === "update_evidence" || act === "set_note_tags") && (input.paperKey || input.paper)) {
          const targetKey = String(input.paperKey || input.paper?.paper_key || computePaperKey(input.paper)).trim();
          const prev = latestState.evidenceMap[targetKey] || {};
          latestState.evidenceMap[targetKey] = {
            ...prev,
            ...(input.stage ? { stage: input.stage } : {}),
            ...(input.basis ? { basis: input.basis } : {}),
            ...(input.reason !== undefined ? { reason: input.reason } : {}),
            updatedAt: new Date().toISOString()
          };
          if (input.user_tags !== undefined || input.user_note !== undefined) {
            setPaperNoteAndTags(latestState, targetKey, input.user_tags, input.user_note, input.paper);
          }
        }
      });
      const panelState = await getFullPanelState();
      return {
        text: `收藏篮与阅读进度已更新（当前收藏共 ${panelState.basket.length} 篇文献）。`,
        structured: panelState
      };
    }
  },
  {
    name: "lookup_venue_rank",
    description:
      "调用 easyScholar 接口查询学术期刊或会议的等级分区（中科院升级版分区、TOP标记、JCR Q1-Q4、SCI 影响因子、5年IF、CCF A-C、EI、CSSCI、北大核心及中科院预警名单），或为当前检索批次自动补全期刊分区。需配置 EASYSCHOLAR_SECRET_KEY。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        venues: {
          type: "array",
          items: { type: "string" },
          description: "要查询的期刊或会议名称列表（如 ['Advanced Materials', 'ACS Catalysis', 'CVPR']）"
        },
        runId: {
          type: "string",
          description: "可选。传入历史批次 ID，自动为该批次内所有论文补全期刊等级与分区"
        },
        preserveActiveRun: {
          type: "boolean",
          description: "设为 true 时仅更新缓存和卡片分区徽章，不切换当前主检索结果视图"
        }
      }
    },
    execute: async (input) => {
      const initialState = loadState();
      const targetRun = input?.runId || (!input?.venues?.length ? initialState.lastRunId : null);
      const venuesList = Array.isArray(input?.venues)
        ? input.venues.map((v) => String(v || "").trim()).filter(Boolean)
        : [];
      if (!venuesList.length && !targetRun) {
        throw new Error("请提供至少一个期刊/会议名称（venues）或指定要补全分区的批次 ID（runId）。");
      }
      const runId = makeRunId("rank");
      const outDir = join(RUNS_DIR, runId);
      const args = ["rank", ...venuesList.slice(0, 20)];
      if (targetRun) {
        args.push("--run", resolveRunDir(targetRun));
      }
      args.push("--out", outDir);
      const pyRes = await runPython(PAPER_SEARCH_PY, args, 90000);
      requirePythonBundle(pyRes, outDir, "easyScholar rank lookup failed");

      // If targetRun was enriched, also update its papers.json in place so activeRun reflects the new venue_rank immediately
      if (targetRun) {
        const enrichedPapers = readJsonSafe(join(outDir, "papers.json"), []);
        const origDir = resolveRunDir(targetRun);
        if (Array.isArray(enrichedPapers) && enrichedPapers.length > 0 && existsSync(join(origDir, "papers.json"))) {
          const manifestPath = join(origDir, "manifest.json");
          const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
          const paperEntry = manifest?.files?.find((entry) => entry.path === "papers.json");
          if (manifest.algorithm !== "sha256" || !paperEntry) {
            throw new Error("原批次缺少有效的 papers.json 哈希清单，已保留原批次。");
          }
          writeJsonSafe(join(origDir, "papers.json"), enrichedPapers);
          const paperBytes = readFileSync(join(origDir, "papers.json"));
          paperEntry.sha256 = createHash("sha256").update(paperBytes).digest("hex");
          paperEntry.size = paperBytes.length;
          writeJsonSafe(manifestPath, manifest);
        }
      }
      const preserve = input?.preserveActiveRun !== undefined ? Boolean(input.preserveActiveRun) : Boolean(targetRun);
      let activeTargetRunId = runId;
      if (!preserve) {
        updateStateTransaction((latestState) => {
          recordMainRunNavigation(latestState, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }
      const panelState = await getFullPanelState(activeTargetRunId);
      const rankResults = readJsonSafe(join(outDir, "results.json"), []);
      const lines = ["### 🏆 easyScholar 期刊/会议等级查询结果："];
      for (const r of Array.isArray(rankResults) ? rankResults : []) {
        if (r?.found && r?.venue_rank) {
          lines.push(`- **${r.venue}**：${(r.venue_rank.badges || []).join(" · ")} — ${r.venue_rank.summary || ""}`);
        } else if (r?.venue) {
          lines.push(`- **${r.venue}**：未匹配到等级记录${r.error ? ` (${r.error})` : ""}`);
        }
      }
      if (targetRun) {
        lines.push(`- 已同步更新批次 \`${targetRun}\` 及本地期刊等级缓存。`);
      }
      return {
        text: lines.join("\n"),
        structured: { ...panelState, venueRanks: rankResults }
      };
    }
  },
  {
    name: "search_ai4scholar",
    description:
      "使用 AI4Scholar 开放平台接口执行全源学术检索与引文分析：支持 Semantic Scholar（相关性搜索 s2-search、布尔批量搜索 s2-bulk、标题匹配 s2-match、详情 s2-detail、批量 s2-batch、施引 s2-citations、参考文献 s2-references、多正负样本推荐 s2-recommendations、500词全文片段搜索 s2-snippets、学者搜索 s2-authors、学者论文 s2-author-papers）、Google Scholar（学术检索 gs-search、标准引用格式 gs-cite、学者主页 gs-profiles / gs-author）、Google Patents（多维全球专利检索 patents）、PubMed（检索 pubmed-search、详情 pubmed-detail、批量 pubmed-batch、施引 pubmed-citations、相似文献 pubmed-related）及账户积分查询（credits）。需配置 AI4SCHOLAR_API_KEY。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: [
            "s2-search",
            "s2-bulk",
            "s2-match",
            "s2-detail",
            "s2-batch",
            "s2-citations",
            "s2-references",
            "s2-recommendations",
            "s2-snippets",
            "s2-authors",
            "s2-author-papers",
            "gs-search",
            "gs-cite",
            "gs-profiles",
            "gs-author",
            "patents",
            "pubmed-search",
            "pubmed-detail",
            "pubmed-batch",
            "pubmed-citations",
            "pubmed-related",
            "credits"
          ],
          default: "s2-search"
        },
        query: { type: "string", description: "检索关键词、学者姓名或论文标题" },
        id: { type: "string", description: "单篇论文 ID（DOI、PMID、S2 paperId、Google Scholar paperId/authorId）" },
        ids: {
          type: "array",
          items: { type: "string" },
          description: "批量查询或推荐正样本论文 ID 列表"
        },
        negativeIds: {
          type: "array",
          items: { type: "string" },
          description: "s2-recommendations 模式的负样本论文 ID 列表"
        },
        pool: { type: "string", enum: ["recent", "all-cs"], default: "recent" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 10 },
        offset: { type: "integer", minimum: 0, default: 0 },
        page: { type: "integer", minimum: 1, maximum: 50, default: 1 },
        year: { type: "string", description: "S2 年份范围表达式，如 2023、2021-2026、2022-" },
        yearFrom: { type: "integer" },
        yearTo: { type: "integer" },
        minDate: { type: "string", description: "PubMed 最早日期 YYYY/MM/DD" },
        maxDate: { type: "string", description: "PubMed 最晚日期 YYYY/MM/DD" },
        sort: { type: "string", description: "排序方式（relevance, date, citationCount:desc, new, old）" },
        venue: { type: "string", description: "s2-snippets 期刊过滤" },
        reviewOnly: { type: "boolean", description: "gs-search 仅看综述论文" },
        sortByDate: { type: "boolean", description: "gs-search 按最新日期排序" },
        cites: { type: "string", description: "gs-search Cited-By 施引簇 ID" },
        language: { type: "string", description: "语言过滤（如 en, zh-CN, ENGLISH, CHINESE）" },
        patentStatus: { type: "string", enum: ["GRANT", "APPLICATION"], description: "专利状态（已授权 GRANT / 申请中 APPLICATION）" },
        patentType: { type: "string", enum: ["PATENT", "DESIGN"], description: "专利类型（发明专利 PATENT / 外观设计 DESIGN）" },
        country: { type: "string", description: "专利国家/地区代码，逗号分隔，如 CN,US,EP,WO,JP" },
        inventor: { type: "string", description: "专利发明人过滤" },
        assignee: { type: "string", description: "专利申请人/机构过滤" },
        preserveActiveRun: { type: "boolean" }
      }
    },
    execute: async (input) => {
      const mode = input?.mode || "s2-search";
      const runId = makeRunId(`ai4s-${mode}`);
      const outDir = join(RUNS_DIR, runId);
      const args = ["ai4scholar"];
      if (input?.query && String(input.query).trim()) args.push(String(input.query).trim());
      args.push("--mode", mode, "--limit", String(input?.limit || 10), "--out", outDir);
      if (input?.id && String(input.id).trim()) args.push("--id", String(input.id).trim());
      for (const item of input?.ids || []) {
        if (item && String(item).trim()) args.push("--ids", String(item).trim());
      }
      for (const neg of input?.negativeIds || []) {
        if (neg && String(neg).trim()) args.push("--negative-ids", String(neg).trim());
      }
      if (input?.pool) args.push("--pool", input.pool);
      if (input?.offset) args.push("--offset", String(input.offset));
      if (input?.page) args.push("--page", String(input.page));
      if (input?.year) args.push("--year", String(input.year).trim());
      if (input?.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (input?.yearTo) args.push("--year-to", String(input.yearTo));
      if (input?.minDate) args.push("--min-date", String(input.minDate).trim());
      if (input?.maxDate) args.push("--max-date", String(input.maxDate).trim());
      if (input?.sort) args.push("--sort", String(input.sort).trim());
      if (input?.venue) args.push("--venue", String(input.venue).trim());
      if (input?.reviewOnly) args.push("--review-only");
      if (input?.sortByDate) args.push("--sort-by-date");
      if (input?.cites) args.push("--cites", String(input.cites).trim());
      if (input?.language) args.push("--language", String(input.language).trim());
      if (input?.patentStatus) args.push("--patent-status", input.patentStatus);
      if (input?.patentType) args.push("--patent-type", input.patentType);
      if (input?.country) args.push("--country", String(input.country).trim());
      if (input?.inventor) args.push("--inventor", String(input.inventor).trim());
      if (input?.assignee) args.push("--assignee", String(input.assignee).trim());
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args, 180000);
      requirePythonBundle(pyRes, outDir, `AI4Scholar (${mode}) failed`);

      let activeTargetRunId = runId;
      if (!input?.preserveActiveRun && mode !== "credits") {
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }
      const panelState = await getFullPanelState(activeTargetRunId);
      let bundle = panelState.activeRun?.runId === runId ? panelState.activeRun : null;
      if (!bundle) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        bundle = loadRunBundle(runId, subProbe.matches);
      }
      return {
        text: summarizeRunForModel(bundle),
        structured:
          input?.preserveActiveRun || mode === "credits"
            ? { ...panelState, subRun: bundle, creditsBalance: bundle.run?.credits_balance || null }
            : panelState
      };
    }
  },
  {
    name: "ai4scholar_draw_and_cite",
    description:
      "调用 AI4Scholar 科研绘图与矢量化（mode='draw'，支持 smart 智能优化提示词绘图、generate 直接文生图、style 科研风格迁移、edit 编辑修改、compose 多图合成、svg 矢量生成、vectorize 位图转矢量 PDF + 可编辑 PPTX、critic 专家审图）或自动引文标注（mode='auto-cite'，为 100-10000 字学术段落自动检索匹配真实文献并插入 [1] 引用标记与格式化参考文献）。生成的图片/PDF/PPTX 自动同步保存到用户的默认下载目录。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: ["draw", "auto-cite"],
          default: "draw"
        },
        drawAction: {
          type: "string",
          enum: ["smart", "generate", "edit", "style", "compose", "iterate", "critic", "svg", "vectorize"],
          default: "smart",
          description: "当 mode=draw 时的操作类型（smart 自动优化中文/英文提示词并绘图；vectorize 将位图转为 PDF 和可编辑 PPTX）"
        },
        prompt: {
          type: "string",
          description: "当 mode=draw 时的图片描述或修改指令；当 mode=auto-cite 时的待标注学术文本（100-10000字符）"
        },
        model: {
          type: "string",
          enum: ["flash", "flash31", "pro", "gptimage"],
          default: "flash"
        },
        aspectRatio: {
          type: "string",
          default: "16:9",
          description: "宽高比：16:9, 1:1, 4:3, 3:2, 3:4, 9:16"
        },
        imageSize: {
          type: "string",
          enum: ["512px", "1K", "2K", "4K"],
          default: "2K"
        },
        stylePreset: {
          type: "string",
          description: "style 模式风格预设：biorender, nature, schematic, textbook, threed, infograph, electron, sketch 等"
        },
        lang: {
          type: "string",
          enum: ["zh", "en"],
          default: "zh"
        },
        vectorizeMode: {
          type: "string",
          enum: ["fast", "standard", "premium"],
          default: "standard"
        },
        images: {
          type: "array",
          items: { type: "string" },
          description: "参考图片 URL 列表或本地图片文件路径列表（用于 edit / style / compose / critic / vectorize）"
        },
        citeMode: {
          type: "string",
          enum: ["auto", "manual"],
          default: "auto"
        },
        minCitations: {
          type: "integer",
          minimum: 1,
          maximum: 50,
          default: 10
        },
        citationStyle: {
          type: "string",
          default: "gbt7714",
          description: "参考文献格式：gbt7714, ieee, apa, nature, acs, vancouver 等"
        },
        field: { type: "string", description: "学科领域提示（如 catalysis, materials science, computer science）" },
        yearPreference: { type: "integer", description: "优先引用年份（如 2024）" },
        preferredVenues: { type: "string", description: "优先匹配期刊名，逗号分隔" },
        excludePreprints: { type: "boolean" },
        excludeConferences: { type: "boolean" },
        saveDir: { type: "string", description: "可选。生成的科研图片/矢量 PDF/PPTX 保存目录" }
      },
      required: ["mode"]
    },
    execute: async (input) => {
      const mode = input?.mode || "draw";
      const runId = makeRunId(mode === "draw" ? `draw-${input?.drawAction || "smart"}` : "autocite");
      const outDir = join(RUNS_DIR, runId);
      const args = ["ai4scholar"];
      if (input?.prompt && String(input.prompt).trim()) {
        args.push(String(input.prompt).trim());
      }
      args.push("--mode", mode, "--out", outDir);
      if (mode === "draw") {
        args.push(
          "--draw-action",
          input?.drawAction || "smart",
          "--model",
          input?.model || "flash",
          "--aspect-ratio",
          input?.aspectRatio || "16:9",
          "--image-size",
          input?.imageSize || "2K"
        );
        if (input?.stylePreset) args.push("--style-preset", String(input.stylePreset).trim());
        if (input?.lang) args.push("--lang", input.lang);
        if (input?.vectorizeMode) args.push("--vectorize-mode", input.vectorizeMode);
        for (const img of input?.images || []) {
          if (img && String(img).trim()) {
            const rawImg = String(img).trim();
            const resolvedImg =
              !rawImg.startsWith("http") && !rawImg.startsWith("data:") && !isAbsolutePath(rawImg)
                ? resolve(WORKSPACE_ROOT, rawImg)
                : rawImg;
            args.push("--image", resolvedImg);
          }
        }
      } else {
        args.push(
          "--cite-mode",
          input?.citeMode || "auto",
          "--min-citations",
          String(input?.minCitations || 10),
          "--citation-style",
          input?.citationStyle || "gbt7714"
        );
        if (input?.field) args.push("--field", String(input.field).trim());
        if (input?.yearPreference) args.push("--year-preference", String(input.yearPreference));
        if (input?.preferredVenues) args.push("--preferred-venues", String(input.preferredVenues).trim());
        if (input?.excludePreprints) args.push("--exclude-preprints");
        if (input?.excludeConferences) args.push("--exclude-conferences");
        if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
          args.push("--with-ranks");
        }
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args, 280000);
      requirePythonBundle(pyRes, outDir, `AI4Scholar ${mode} failed`);

      // Copy generated figure or auto-cite files (PNG, SVG, PDF, PPTX, MD, BIB) to user's saveDir
      const destDir = resolveTargetSaveDir(input?.saveDir);
      const copiedPaths = {};
      const runJson = readJsonSafe(join(outDir, "run.json"), {});
      const drawRes = runJson?.draw_result || {};
      const savedMap = { ...(drawRes?.savedFiles || {}) };
      if (mode === "auto-cite") {
        if (existsSync(join(outDir, "annotated_text.md"))) savedMap.annotated_md = "annotated_text.md";
        if (existsSync(join(outDir, "references.bib"))) savedMap.bibtex_file = "references.bib";
      }
      const stem = makeSafeFileStem(new Date().getFullYear(), input?.prompt || mode, runId, runId);
      for (const [k, relName] of Object.entries(savedMap)) {
        const srcPath = join(outDir, relName);
        if (existsSync(srcPath) && statSync(srcPath).isFile()) {
          const ext = extname(relName);
          const suffix = k === "pdf_file" ? "-vector" : k === "pptx_file" ? "-editable" : k === "annotated_md" ? "-autocite" : k === "bibtex_file" ? "-refs" : "";
          const dstPath = uniqueDestination(destDir, `${stem}${suffix}${ext}`, runId);
          copyFileSync(srcPath, dstPath, fsConstants.COPYFILE_EXCL);
          copiedPaths[k] = dstPath;
        }
      }
      if (Object.keys(copiedPaths).length > 0) {
        runJson.exported_save_dir = destDir;
        if (runJson.draw_result) {
          runJson.draw_result = {
            ...drawRes,
            exportedFiles: copiedPaths
          };
        }
        if (runJson.auto_cite_result) {
          runJson.auto_cite_result = {
            ...runJson.auto_cite_result,
            exportedFiles: copiedPaths
          };
        }
        const runPath = join(outDir, "run.json");
        writeJsonSafe(runPath, runJson);
        const manifestPath = join(outDir, "manifest.json");
        if (existsSync(manifestPath)) {
          try {
            const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
            const runEntry = manifest?.files?.find((entry) => entry.path === "run.json");
            if (manifest?.algorithm === "sha256" && runEntry) {
              const runBytes = readFileSync(runPath);
              runEntry.sha256 = createHash("sha256").update(runBytes).digest("hex");
              runEntry.size = runBytes.length;
              writeJsonSafe(manifestPath, manifest);
            }
          } catch {}
        }
      }

      updateStateTransaction((state) => {
        if (Object.keys(copiedPaths).length > 0) {
          state.runExports = { ...(state.runExports || {}), [runId]: destDir };
        }
        recordMainRunNavigation(state, runId);
      });

      const panelState = await getFullPanelState(runId);
      return {
        text:
          summarizeRunForModel(panelState.activeRun) +
          (Object.keys(copiedPaths).length
            ? `\n\n✅ 已自动将生成文件保存至本地目录：\`${destDir}\`\n` +
              Object.entries(copiedPaths)
                .map(([k, p]) => `- ${k}: \`${p}\``)
                .join("\n")
            : ""),
        structured: {
          ...panelState,
          drawResult: panelState.activeRun?.run?.draw_result || null,
          autoCiteResult: panelState.activeRun?.run?.auto_cite_result || null
        }
      };
    }
  },
  {
    name: "search_sciverse",
    description:
      "调用 SciVerse 开放平台接口执行学术文献与结构化证据检索：支持结构化文献/学者/期刊检索 (meta-search，含布尔检索、新鲜度/影响力/语言软加权与 FWCI)、自然语言全文语义片段检索 (semantic-search，Agentic RAG 返回原文匹配段落与页码/偏移)、按 Unicode 码点偏移读取论文原文 Markdown 并一键提取下载高清原图 (content / resource)、引文关系分页与批量题录补全 (relations)、字段目录自省 (catalog)，以及 Paper Schema 结构化论文搜索 (schema-search)、复现/方法/综述研究材料包构建 (schema-materials)、结构对象检索 (schema-entities)、跨论文公式/表格/核心结论证据检索 (schema-evidence)、引文图谱扩展 (schema-citations) 与单篇正文检索定位 (schema-in-paper)。需配置 SCIVERSE_API_KEY (或 SCIVERSE_API_TOKEN)。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: [
            "meta-search",
            "semantic-search",
            "content",
            "resource",
            "relations",
            "catalog",
            "schema-search",
            "schema-materials",
            "schema-entities",
            "schema-evidence",
            "schema-citations",
            "schema-in-paper"
          ],
          default: "meta-search",
          description: "SciVerse 操作模式"
        },
        collection: {
          type: "string",
          enum: ["papers", "authors", "sources"],
          default: "papers",
          description: "meta-search / catalog 的实体集合：papers（论文）、authors（学者）、sources（来源期刊）"
        },
        query: {
          type: "string",
          description: "检索词、布尔检索式、自然语言问题（semantic-search）、单篇正文检索词（schema-in-paper）或论文标题/DOI"
        },
        id: {
          type: "string",
          description: "论文 DOI、SciVerse unique_id (如 paper:10.1038/...)、doc_id 或 schema_id"
        },
        docId: {
          type: "string",
          description: "SciVerse 64位全文哈希 doc_id（用于 content 读取原文或 semantic-search 限定单篇）"
        },
        docIds: {
          type: "array",
          items: { type: "string" },
          description: "semantic-search 硬范围过滤的 doc_id 数组（最多 1000 个）"
        },
        schemaId: {
          type: "string",
          description: "Paper Schema 论文主键 schema_id（用于 schema-materials / schema-entities / schema-evidence / schema-citations / schema-in-paper）"
        },
        schemaIds: {
          type: "array",
          items: { type: "string" },
          description: "schema-materials 批量构建研究材料包的多个 schema_id（1..20）"
        },
        titleContains: { type: "string", description: "meta-search 标题包含词" },
        abstractContains: { type: "string", description: "meta-search 摘要包含词" },
        authors: { type: "array", items: { type: "string" }, description: "作者姓名过滤列表" },
        journals: { type: "array", items: { type: "string" }, description: "期刊/会议规范名过滤列表" },
        subjects: { type: "array", items: { type: "string" }, description: "学科分类过滤列表" },
        yearFrom: { type: "integer", description: "起始发表年份" },
        yearTo: { type: "integer", description: "结束发表年份" },
        sort: {
          type: "string",
          description: "排序字段：cited（被引）、date（年份降序）、year_asc（年份升序）、fwci（领域加权影响力）、influential（高影响力被引）、references（参考文献数）、h_index（学者/期刊 h-index）"
        },
        freshnessBoost: {
          type: "string",
          enum: ["NONE", "MILD", "STRONG"],
          default: "NONE",
          description: "meta-search 模糊检索新鲜度软加权（MILD=近10年，STRONG=近3年）"
        },
        impactBoost: {
          type: "string",
          enum: ["NONE", "MILD", "STRONG"],
          default: "NONE",
          description: "meta-search 模糊检索高被引软加权（MILD / STRONG）"
        },
        languageAffinity: {
          type: "string",
          enum: ["NONE", "MILD", "STRONG"],
          default: "NONE",
          description: "meta-search 语言亲和软加权（NONE / MILD / STRONG）"
        },
        oaOnly: { type: "boolean", description: "仅筛选开放获取 (OA) 论文" },
        language: { type: "string", description: "语言过滤代码（如 en, zh）" },
        semanticMode: {
          type: "string",
          enum: ["fast", "balanced", "quality"],
          default: "balanced",
          description: "semantic-search 检索深度：fast（关键词）、balanced（混合检索）、quality（多路改写+混合）"
        },
        sourceTypes: {
          type: "array",
          items: { type: "string", enum: ["pdf", "web"] },
          description: "semantic-search 来源类型过滤"
        },
        offset: {
          type: "integer",
          minimum: 0,
          default: 0,
          description: "content 模式读取原文的起始 Unicode 码点偏移（可直接传入 semantic-search 返回的 offset）"
        },
        chars: {
          type: "integer",
          minimum: 200,
          maximum: 65536,
          default: 6000,
          description: "content 模式单次读取的 Unicode 字符数上限（默认 6000）"
        },
        downloadFigures: {
          type: "boolean",
          default: true,
          description: "content 模式下是否自动解析并下载正文 Markdown 中引用的论文原图（/resource）到保存目录"
        },
        fileName: {
          type: "string",
          description: "resource 模式下要下载的论文图表相对路径（来自 content Markdown 的 ![alt](file_name)）"
        },
        relation: {
          type: "string",
          enum: ["REFERENCES", "CITATIONS", "RELATED_WORKS", "references", "citations", "recommendations"],
          default: "REFERENCES",
          description: "relations 模式的关系类型：REFERENCES（参考文献）、CITATIONS（施引文献）、RELATED_WORKS（相关工作）"
        },
        goal: {
          type: "string",
          enum: ["overview", "survey", "benchmark", "method", "reproduction"],
          default: "reproduction",
          description: "schema-materials 模式的研究目标（overview / survey / benchmark / method / reproduction）"
        },
        hasCode: { type: "boolean", description: "schema-search 仅看含开源代码的论文" },
        hasData: { type: "boolean", description: "schema-search 仅看含数据集的论文" },
        entityTypes: {
          type: "array",
          items: { type: "string" },
          description: "schema-entities 筛选的 Entity 类型（如 Problem, Contribution, Component, ExperimentSetup, Measure, Finding, Resource）"
        },
        evidenceGroups: {
          type: "array",
          items: {
            type: "string",
            enum: [
              "citation_signal",
              "comparison_detail",
              "core_claim_result",
              "evidence_score",
              "formula",
              "reference_core",
              "reference_semantics",
              "resource",
              "schema_unit",
              "table_evidence"
            ]
          },
          description: "schema-evidence 筛选的证据类别"
        },
        direction: {
          type: "string",
          enum: ["outbound", "inbound"],
          default: "outbound",
          description: "schema-citations 引文图谱扩展方向"
        },
        depth: { type: "integer", minimum: 1, maximum: 3, default: 1, description: "schema-citations 引文图谱扩展深度 (1..3)" },
        sectionHint: { type: "string", description: "schema-in-paper 优先章节提示（如 method, experiment）" },
        sampleValues: { type: "boolean", description: "catalog 模式是否返回枚举字段样本值" },
        fieldStats: { type: "boolean", description: "catalog 模式是否返回字段统计" },
        page: { type: "integer", minimum: 1, maximum: 200, default: 1 },
        limit: { type: "integer", minimum: 1, maximum: 100, default: 10 },
        title: { type: "string", description: "可选。关联论文标题，用于生成易读的导出文件夹名" },
        year: { type: ["integer", "string"], description: "可选。关联论文发表年份，用于生成易读的导出文件夹名" },
        paperKey: { type: "string", description: "可选。关联的论文键值，用于将提取的原文切片、原图或材料包绑定到该卡片" },
        saveDir: { type: "string", description: "可选。提取的原文切片、高清图表或复现材料包同步保存目录" },
        preserveActiveRun: {
          type: "boolean",
          description: "设为 true 时保持当前主检索结果列表不跳转，将本次结果作为 subRun 返回（适合在详情页内联读取原文或提取复现材料包）"
        }
      }
    },
    execute: async (input) => {
      const mode = input?.mode || "meta-search";
      const runId = makeRunId(`sciverse-${mode}`);
      const outDir = join(RUNS_DIR, runId);
      const args = ["sciverse"];
      if (input?.query && String(input.query).trim()) {
        args.push(String(input.query).trim());
      }
      args.push("--mode", mode, "--limit", String(input?.limit || 10), "--out", outDir);
      if (input?.collection) args.push("--collection", input.collection);
      if (input?.id && String(input.id).trim()) args.push("--id", String(input.id).trim());
      if (input?.docId && String(input.docId).trim()) args.push("--doc-id", String(input.docId).trim());
      for (const d of input?.docIds || []) {
        if (d && String(d).trim()) args.push("--doc-ids", String(d).trim());
      }
      if (input?.schemaId && String(input.schemaId).trim()) args.push("--schema-id", String(input.schemaId).trim());
      for (const sid of input?.schemaIds || []) {
        if (sid && String(sid).trim()) args.push("--schema-ids", String(sid).trim());
      }
      if (input?.titleContains) args.push("--title-contains", String(input.titleContains).trim());
      if (input?.abstractContains) args.push("--abstract-contains", String(input.abstractContains).trim());
      for (const a of input?.authors || []) {
        if (a && String(a).trim()) args.push("--author", String(a).trim());
      }
      for (const j of input?.journals || []) {
        if (j && String(j).trim()) args.push("--journal", String(j).trim());
      }
      for (const s of input?.subjects || []) {
        if (s && String(s).trim()) args.push("--subject", String(s).trim());
      }
      if (input?.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (input?.yearTo) args.push("--year-to", String(input.yearTo));
      if (input?.sort) args.push("--sort", String(input.sort).trim());
      if (input?.freshnessBoost && input.freshnessBoost !== "NONE") args.push("--freshness-boost", input.freshnessBoost);
      if (input?.impactBoost && input.impactBoost !== "NONE") args.push("--impact-boost", input.impactBoost);
      if (input?.languageAffinity && input.languageAffinity !== "NONE") args.push("--language-affinity", input.languageAffinity);
      if (input?.oaOnly) args.push("--oa-only");
      if (input?.language) args.push("--language", String(input.language).trim());
      if (input?.semanticMode) args.push("--semantic-mode", input.semanticMode);
      for (const st of input?.sourceTypes || []) {
        if (st === "pdf" || st === "web") args.push("--source-type", st);
      }
      if (input?.offset !== undefined && input?.offset !== null) args.push("--offset", String(input.offset));
      if (input?.chars !== undefined && input?.chars !== null) args.push("--chars", String(input.chars));
      if (mode === "content" && input?.downloadFigures !== false) args.push("--download-figures");
      if (input?.fileName && String(input.fileName).trim()) args.push("--file-name", String(input.fileName).trim());
      if (input?.relation) args.push("--relation", input.relation);
      if (input?.goal) args.push("--goal", input.goal);
      if (input?.hasCode) args.push("--has-code");
      if (input?.hasData) args.push("--has-data");
      for (const et of input?.entityTypes || []) {
        if (et && String(et).trim()) args.push("--entity-type", String(et).trim());
      }
      for (const eg of input?.evidenceGroups || []) {
        if (eg && String(eg).trim()) args.push("--evidence-group", String(eg).trim());
      }
      if (input?.direction) args.push("--direction", input.direction);
      if (input?.depth) args.push("--depth", String(input.depth));
      if (input?.sectionHint) args.push("--section-hint", String(input.sectionHint).trim());
      if (input?.sampleValues) args.push("--sample-values");
      if (input?.fieldStats) args.push("--field-stats");
      if (input?.page) args.push("--page", String(input.page));
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args, 180000);
      requirePythonBundle(pyRes, outDir, `SciVerse (${mode}) failed`);

      // Sync downloaded fulltext excerpt, figures, or schema_materials.md to user's saveDir when applicable
      let exportedSaveDir = null;
      let exportedExcerptPath = null;
      let exportedMaterialsPath = null;
      const exportedFigureFiles = [];
      if (["content", "resource", "schema-materials"].includes(mode)) {
        const hasExcerpt = existsSync(join(outDir, "fulltext_excerpt.md"));
        const hasMaterials = existsSync(join(outDir, "schema_materials.md"));
        const figSrcDir = join(outDir, "figures");
        const hasFigs = existsSync(figSrcDir) && statSync(figSrcDir).isDirectory();
        if (hasExcerpt || hasMaterials || hasFigs) {
          const baseSaveDir = resolveTargetSaveDir(input?.saveDir);
          const papers = readJsonSafe(join(outDir, "papers.json"), []);
          const knownP = input?.paperKey ? findKnownPaperByAnyId(input.paperKey, loadState()) : null;
          const firstPaper = papers[0] || knownP || {};
          const folderYear = input?.year || firstPaper.year;
          const folderTitle = input?.title || firstPaper.title || input?.query || input?.id || input?.schemaId || (input?.docId ? String(input.docId).slice(0, 16) : runId);
          const folderName = `${makeSafeFileStem(folderYear, folderTitle, runId)}_SciVerse`;
          exportedSaveDir = uniqueDestination(baseSaveDir, folderName);
          mkdirSync(exportedSaveDir, { recursive: true });
          if (hasExcerpt) {
            exportedExcerptPath = join(exportedSaveDir, "fulltext_excerpt.md");
            copyFileSync(join(outDir, "fulltext_excerpt.md"), exportedExcerptPath, fsConstants.COPYFILE_EXCL);
          }
          if (hasMaterials) {
            exportedMaterialsPath = join(exportedSaveDir, "schema_materials.md");
            copyFileSync(join(outDir, "schema_materials.md"), exportedMaterialsPath, fsConstants.COPYFILE_EXCL);
          }
          if (hasFigs) {
            const dstFigDir = join(exportedSaveDir, "figures");
            mkdirSync(dstFigDir, { recursive: true });
            for (const entry of readdirSync(figSrcDir, { withFileTypes: true })) {
              if (entry.isFile() && !entry.isSymbolicLink()) {
                const srcF = join(figSrcDir, entry.name);
                const dstF = join(dstFigDir, entry.name);
                assertReadablePath(srcF);
                copyFileSync(srcF, dstF, fsConstants.COPYFILE_EXCL);
                exportedFigureFiles.push(dstF);
              }
            }
          }

          const runJson = readJsonSafe(join(outDir, "run.json"), {});
          runJson.exported_save_dir = exportedSaveDir;
          if (runJson.sciverse_content) {
            runJson.sciverse_content.exported_excerpt_path = exportedExcerptPath;
            if (Array.isArray(runJson.sciverse_content.downloaded_figures)) {
              runJson.sciverse_content.downloaded_figures = runJson.sciverse_content.downloaded_figures.map((df) => ({
                ...df,
                saved_path: df.local_path ? join(exportedSaveDir, df.local_path) : null
              }));
            }
          }
          if (runJson.sciverse_materials) {
            runJson.sciverse_materials.exported_materials_path = exportedMaterialsPath;
          }
          const runPath = join(outDir, "run.json");
          writeJsonSafe(runPath, runJson);
          const manifestPath = join(outDir, "manifest.json");
          if (existsSync(manifestPath)) {
            try {
              const manifest = JSON.parse(readFileSync(manifestPath, "utf8"));
              const runEntry = manifest?.files?.find((entry) => entry.path === "run.json");
              if (manifest?.algorithm === "sha256" && runEntry) {
                const runBytes = readFileSync(runPath);
                runEntry.sha256 = createHash("sha256").update(runBytes).digest("hex");
                runEntry.size = runBytes.length;
                writeJsonSafe(manifestPath, manifest);
              }
            } catch {}
          }

          const targetKey =
            input?.paperKey ||
            firstPaper.paper_key ||
            (firstPaper.title || firstPaper.identifiers ? computePaperKey(firstPaper) : null);
          updateStateTransaction((exportState) => {
            exportState.runExports = { ...(exportState.runExports || {}), [runId]: exportedSaveDir };
            if (targetKey) {
              const prevEv = exportState.evidenceMap[targetKey] || {};
              exportState.evidenceMap[targetKey] = {
                ...prevEv,
                sciverse_saved_dir: exportedSaveDir,
                ...(exportedFigureFiles.length ? { sciverse_figure_files: exportedFigureFiles } : {}),
                ...(exportedExcerptPath ? { sciverse_excerpt_path: exportedExcerptPath } : {}),
                ...(exportedMaterialsPath ? { sciverse_materials_path: exportedMaterialsPath } : {}),
                updatedAt: new Date().toISOString()
              };
            }
          });
        }
      }

      let activeTargetRunId = runId;
      if (!input?.preserveActiveRun) {
        updateStateTransaction((state) => {
          recordMainRunNavigation(state, runId);
        });
      } else {
        activeTargetRunId = loadState().lastRunId;
      }

      const panelState = await getFullPanelState(activeTargetRunId);
      let subRun = null;
      if (input?.preserveActiveRun) {
        const subRaw = readJsonSafe(join(outDir, "papers.json"), []);
        const subProbe = await probeLocalZotero(Array.isArray(subRaw) ? subRaw : []);
        subRun = loadRunBundle(runId, subProbe.matches);
      }
      const targetBundle = subRun || panelState.activeRun;
      return {
        text:
          summarizeRunForModel(targetBundle) +
          (exportedSaveDir ? `\n\n✅ SciVerse 原文切片/原图/结构化材料包已自动保存至目录：\`${exportedSaveDir}\`` : ""),
        structured: {
          ...panelState,
          ...(subRun ? { subRun } : {}),
          ...(exportedSaveDir ? { exportedSaveDir } : {}),
          sciverseContent: targetBundle?.run?.sciverse_content || null,
          sciverseMaterials: targetBundle?.run?.sciverse_materials || null
        }
      };
    }
  },
  {
    name: "pick_system_path",
    description:
      "调起本机系统原生文件或文件夹选择对话框（供侧栏「📝 文档解析」直接从文件管理器挑选本地 PDF / PPTX / JSON 文件或选择 Markdown 输出保存目录，并自动探测同目录关联的 SI PDF 或 PPTX）。",
    visibility: ["app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: ["file", "files", "folder"],
          default: "file",
          description: "选择模式：file（单选文件）、files（多选文件）、folder（选择文件夹）"
        },
        filter: {
          type: "string",
          enum: ["pdf_pptx", "pdf", "pptx", "json", "all"],
          default: "pdf_pptx",
          description: "文件扩展名过滤器"
        },
        title: { type: "string", description: "系统选择窗口标题" },
        initialDir: { type: "string", description: "可选。初始打开的目录路径" }
      }
    },
    execute: async (input) => {
      const picked = pickSystemPath(input || {});
      return {
        text: picked.canceled
          ? "已取消文件/目录选择。"
          : `已选择路径：\`${picked.path}\``,
        structured: picked
      };
    }
  },
  {
    name: "list_zotero_pdf_items",
    description:
      "从本机 Zotero 桌面端检索条目并提取已关联的本地 PDF 附件路径（自动区分主文 PDF 与补充材料 SI PDF）及完整题录元数据，供「📝 文档解析」直接点选 Zotero 文献转换 Markdown。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        collectionKey: { type: "string", description: "可选。Zotero 分类 Key（留空查询文库全部条目）" },
        query: { type: "string", description: "可选。按标题、作者、年份或 DOI 搜索过滤" },
        limit: { type: "integer", minimum: 1, maximum: 50, default: 20 },
        onlyWithPdf: {
          type: "boolean",
          default: true,
          description: "是否仅返回带有本地可用 PDF 附件的 Zotero 条目（默认 true）"
        }
      }
    },
    execute: async (input) => {
      const zotRes = await listZoteroPdfItems(input || {});
      const panelState = await getFullPanelState();
      const itemLines = (zotRes.items || []).map(
        (it, idx) =>
          `${idx + 1}. [${it.itemKey}] **${it.title}** (${it.year || "n.d."})${it.doi ? ` | DOI: \`${it.doi}\`` : ""}\n   - 主文 PDF: ${it.mainPdfPath ? `\`${it.mainPdfPath}\`` : "无"}${it.siPdfPath ? `\n   - 补充材料 SI PDF: \`${it.siPdfPath}\`` : ""}`
      );
      return {
        text: zotRes.zoteroOnline
          ? `📚 已从本机 Zotero 读取到 ${zotRes.items.length} 条含本地 PDF 的文献条目：\n\n${itemLines.join("\n")}`
          : zotRes.message || "本机 Zotero 未在线。",
        structured: {
          ...panelState,
          zoteroPdfList: zotRes
        }
      };
    }
  },
  {
    name: "run_doc2md",
    description:
      "调用 sanmu-doc2md 将本地 PDF、补充材料（SI PDF）、PPTX 课件或长篇教材转换为保留 LaTeX 公式、高清原图和页码溯源的 Obsidian 兼容 Markdown 知识库（支持 MinerU 云端高精度 OCR、本地原生极速提取与复用已有 OCR 结果，自动执行结构与 KaTeX 公式校验）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        mode: {
          type: "string",
          enum: ["research", "slides", "book"],
          default: "research",
          description: "解析模式：research（科研论文+SI双链入库）、slides（PPTX/PDF 课件转讲义笔记）、book（长篇教材切片与按章拆分）"
        },
        ocrEngine: {
          type: "string",
          enum: ["mineru", "local_fast", "existing_raw"],
          default: "mineru",
          description: "解析引擎：mineru（MinerU 云端公式/图表高精度 OCR，推荐）、local_fast（PyMuPDF/PPTX 本地原生文字与内嵌图直提，免 Token）、existing_raw（复用已有 raw/ OCR 目录直接装配）"
        },
        pdfPath: { type: "string", description: "主文档 PDF 本地路径（或工作区相对路径）" },
        siPdfPath: {
          type: "string",
          description: "可选。补充材料 (SI) PDF 本地路径（research 模式下自动建立主文与 SI 双向 [[WikiLink]] 及独立 supplemental: 图表命名空间）"
        },
        pptxPath: {
          type: "string",
          description: "可选。课件模式 (slides) 下的源 PPTX 文件路径（用于自动校对替换 OCR 错字并提取未裁剪内嵌原图）"
        },
        existingRawDir: {
          type: "string",
          description: "可选。当 ocrEngine=existing_raw 时，指定已有的 MinerU OCR raw 目录路径"
        },
        chaptersJsonPath: {
          type: "string",
          description: "可选。教材模式 (book) 下的 chapters.json 章节定义文件路径（未传时自动从 PDF 书签目录或分片边界生成）"
        },
        language: {
          type: "string",
          enum: ["en", "ch"],
          description: "OCR 识别语言声明：en（英文论文默认）或 ch（中文课件/教材默认）"
        },
        autoRepairs: {
          type: "boolean",
          default: true,
          description: "是否自动缝合高置信度的同页多面板碎图（默认 true）"
        },
        keepTableImages: {
          type: "boolean",
          default: true,
          description: "是否同时保留复杂表格/化学环结构的原版裁图以便核对（默认 true）"
        },
        agentIndex: {
          type: "boolean",
          default: false,
          description: "是否额外生成供 Agent 分层检索用的 agent/ 索引与原页映射（--agent-index）"
        },
        readingOrder: {
          type: "string",
          enum: ["source", "geometry"],
          default: "source",
          description: "双栏/页面阅读顺序：source（保持 OCR 物理流顺序，默认）或 geometry（几何重排）"
        },
        chunkSize: {
          type: "integer",
          minimum: 10,
          maximum: 200,
          default: 96,
          description: "教材模式 (book) 下的单次切片页数上限（默认 96 页）"
        },
        bookSplitOnly: {
          type: "boolean",
          default: false,
          description: "教材模式 (book) 下是否仅执行切片与云端 OCR 并预生成章节草稿，留待核对章节表后再装配"
        },
        autoDetectSi: {
          type: "boolean",
          default: true,
          description: "是否自动检测同目录下的 -SI.pdf 补充材料文件（默认 true）"
        },
        saveDir: {
          type: "string",
          description: "可选。解析产出的 Markdown 知识库存放父目录（支持工作区相对路径或任意本地绝对路径，默认保存到配置的文档解析目录）"
        },
        outputFolderName: {
          type: "string",
          description: "可选。自定义输出子文件夹名称（未传时按『第一作者 等 - 年份 - 标题』自动生成）"
        },
        paperKey: {
          type: "string",
          description: "可选。关联的检索/收藏篮论文键值，解析完成后自动绑定 Markdown 路径并更新阅读状态"
        },
        zoteroItemKey: {
          type: "string",
          description: "可选。关联的本机 Zotero 条目 Key"
        },
        metadata: {
          type: "object",
          description: "可选。关联的论文或 Zotero 题录元数据（title, authors, year, date, doi, journal, publication_title, abstract, url, zotero_item_uri）"
        },
        async: {
          type: "boolean",
          description: "可选。设为 true 时在后台异步执行并立即返回任务 ID 供面板轮询进度"
        }
      }
    },
    execute: async (input) => {
      return await startOrRunDoc2mdJob(input || {});
    }
  },
  {
    name: "manage_doc2md",
    description:
      "管理文档解析任务与后续操作：支持查询进度 (status)、MinerU 断点续传 (resume，绝不重复新建批次)、修改章节表/修复表后重新装配 (rebuild)、独立执行结构与 KaTeX 公式校验 (validate)、启动/停止本地 KaTeX 渲染预览服务 (preview / stop_preview)、删除任务记录 (delete / clear_all) 及环境自检 (doctor)。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["status", "resume", "rebuild", "validate", "preview", "stop_preview", "delete", "clear_all", "doctor"],
          default: "status"
        },
        jobId: { type: "string", description: "目标文档解析任务 ID" },
        outDir: { type: "string", description: "可选。当 action=validate / preview / stop_preview 时直接指定的知识库输出目录" },
        chaptersJsonPath: { type: "string", description: "可选。当 action=rebuild 时传入修订后的 chapters.json 路径" },
        repairsJsonPath: { type: "string", description: "可选。当 action=rebuild 时传入回源核对后的 repairs.json 路径" },
        port: { type: "integer", minimum: 1024, maximum: 65535, description: "可选。KaTeX 预览服务端口（默认 8766）" },
        openInBrowser: { type: "boolean", description: "启动预览服务时是否自动在系统默认浏览器中打开（默认 true）" },
        async: { type: "boolean", description: "可选。resume / rebuild 是否在后台异步运行" }
      }
    },
    execute: async (input) => {
      return await manageDoc2mdAction(input || {});
    }
  }
];

function validateInput(schema, value, path = "arguments") {
  const types = Array.isArray(schema.type) ? schema.type : schema.type ? [schema.type] : [];
  const matches = (type) => type === "object" ? value !== null && typeof value === "object" && !Array.isArray(value)
    : type === "array" ? Array.isArray(value) : type === "integer" ? Number.isInteger(value)
    : typeof value === type;
  if (types.length && !types.some(matches)) throw new Error(`${path} 类型错误（需要 ${types.join("/")}）。`);
  if (schema.enum && !schema.enum.includes(value)) throw new Error(`${path} 不在允许的选项中。`);
  if (typeof value === "number" && ((schema.minimum !== undefined && value < schema.minimum) || (schema.maximum !== undefined && value > schema.maximum))) {
    throw new Error(`${path} 超出允许范围。`);
  }
  if (typeof value === "string" && (value.includes("\0") || value.length > 2_000_000)) throw new Error(`${path} 包含无效字符或过长。`);
  if (Array.isArray(value)) {
    if (value.length > 500) throw new Error(`${path} 数组过长。`);
    value.forEach((item, idx) => { if (schema.items) validateInput(schema.items, item, `${path}[${idx}]`); });
  } else if (value && typeof value === "object") {
    for (const key of schema.required || []) if (value[key] === undefined) throw new Error(`${path}.${key} 为必填参数。`);
    for (const [key, val] of Object.entries(value)) {
      if (["__proto__", "prototype", "constructor"].includes(key)) throw new Error(`${path} 包含无效键名。`);
      if (schema.properties?.[key]) validateInput(schema.properties[key], val, `${path}.${key}`);
      else if (schema.additionalProperties === false) throw new Error(`${path}.${key} 是未知参数。`);
    }
  }
}

function validateToolInput(name, input) {
  const requiredText = (key) => { if (typeof input[key] !== "string" || !input[key].trim()) throw new Error(`${key} 不能为空。`); };
  for (const key of ["yearFrom", "yearTo"]) if (input[key] !== undefined && (input[key] < 1000 || input[key] > 9999)) throw new Error(`${key} 必须在1000至9999之间。`);
  if (input.yearFrom !== undefined && input.yearTo !== undefined && input.yearFrom > input.yearTo) throw new Error("起始年份不能晚于结束年份。");
  for (const key of ["sources", "defaultSources"]) if (input[key] && !input[key].length) throw new Error(`${key} 至少选择一个来源。`);
  if (["search_papers", "search_exa"].includes(name)) requiredText("query");
  if (["lookup_paper", "explore_related"].includes(name)) requiredText("id");
  if (name === "search_author" && ![input.query, input.authorId, input.fromPaper].some((v) => v?.trim())) throw new Error("请提供学者姓名、authorId 或 fromPaper。");
  if (name === "search_epmc" && ![input.query, input.id].some((v) => v?.trim())) throw new Error("请提供 Europe PMC 检索词或标识符。");
  if (name === "search_serper") {
    const mode = input.type || "scholar";
    if (mode === "webpage") {
      if (!input.url && !input.params?.url) throw new Error("webpage 需要 url。");
    } else if (!(input.query?.trim() || input.params?.q || (mode === "scholar" && (input.params?.cites || input.params?.cluster)))) {
      requiredText("query");
    }
  }
  if (name === "save_config") {
    if (input.zoteroUrl?.trim()) validateZoteroUrl(input.zoteroUrl.trim());
    const allowed = [
      "openalexApiKey",
      "s2ApiKey",
      "exaApiKey",
      "serperApiKey",
      "easyscholarSecretKey",
      "ai4scholarApiKey",
      "sciverseApiKey",
      "mineruApiToken",
      "mineruApiKey",
      "politeEmail"
    ];
    if (input.clearKeys?.some((key) => !allowed.includes(key))) throw new Error("clearKeys 只允许清空 API Key 或联系邮箱。");
    if (input.defaultSources?.some((source) => !["openalex", "semantic-scholar", "crossref"].includes(source))) throw new Error("defaultSources 含无效来源。");
  }
  if (name === "manage_basket") {
    const isBatchAdd = input.action === "add" && Array.isArray(input.papers) && input.papers.length > 0;
    if (!isBatchAdd && ["add", "save_to_zotero", "enrich_abstract"].includes(input.action) && (!input.paper || typeof input.paper.title !== "string" || !input.paper.title.trim())) throw new Error("请提供有标题的论文数据。");
    if (["remove", "update_evidence", "set_note_tags"].includes(input.action) && !input.paperKey && !input.paper?.paper_key) requiredText("paperKey");
    for (const key of ["paperKey", "zoteroItemKey", "collectionKey"]) if (["__proto__", "prototype", "constructor"].includes(input[key])) throw new Error(`${key} 无效。`);
    if (["zoteroItemKey", "collectionKey"].some((key) => input[key] && !/^[23456789A-Z]{8}$/.test(input[key]))) throw new Error("Zotero 条目或分类 Key 格式无效。");
    for (const paper of [...(input.paper ? [input.paper] : []), ...(input.papers || [])]) {
      if (input.action === "add" && (typeof paper.title !== "string" || !paper.title.trim())) throw new Error("请提供有标题的论文数据。");
      if (["__proto__", "prototype", "constructor"].includes(paper.paper_key)) throw new Error("paper_key 无效。");
      for (const key of ["authors", "oa_locations", "provenance", "relations"]) if (paper[key] !== undefined && !Array.isArray(paper[key])) throw new Error(`paper.${key} 必须为数组。`);
      if (paper.identifiers) for (const ids of Object.values(paper.identifiers)) if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string")) throw new Error("paper.identifiers 必须使用字符串数组。");
    }
  }
  if (name === "read_run_file") requiredText("relativePath");
  if (name === "copy_clipboard") requiredText("text");
  if (name === "search_ai4scholar") {
    const m = input.mode || "s2-search";
    if (["s2-search", "s2-bulk", "s2-match", "s2-snippets", "s2-authors", "gs-search", "gs-profiles", "patents", "pubmed-search"].includes(m)) {
      if (!input.query?.trim() && !(m === "gs-search" && input.cites?.trim())) {
        requiredText("query");
      }
    } else if (["s2-detail", "s2-citations", "s2-references", "s2-author-papers", "gs-cite", "gs-author", "pubmed-detail", "pubmed-citations", "pubmed-related"].includes(m)) {
      if (!input.id?.trim() && !input.query?.trim()) {
        throw new Error(`模式 ${m} 需要提供 id 或 query 参数。`);
      }
    }
  }
  if (name === "ai4scholar_draw_and_cite") {
    const m = input.mode || "draw";
    if (m === "auto-cite") {
      requiredText("prompt");
      if (input.prompt.trim().length < 100 || input.prompt.trim().length > 10000) {
        throw new Error("Auto-Cite 待标注学术文本长度需在 100 至 10000 字符之间。");
      }
    } else {
      const act = input.drawAction || "smart";
      if (!["critic", "vectorize"].includes(act)) {
        requiredText("prompt");
      }
      if (["edit", "style", "compose", "iterate", "critic", "vectorize"].includes(act) && (!Array.isArray(input.images) || !input.images.some((x) => String(x || "").trim()))) {
        throw new Error(`绘图操作 ${act} 需要至少提供一张参考图片（images）。`);
      }
    }
  }
  if (name === "search_sciverse") {
    const m = input.mode || "meta-search";
    if (m === "semantic-search") {
      requiredText("query");
    } else if (m === "content") {
      if (![input.docId, input.id, input.query].some((v) => v?.trim())) {
        throw new Error("SciVerse content 模式需要提供 docId、id 或 query 参数。");
      }
    } else if (m === "resource") {
      if (![input.fileName, input.query].some((v) => v?.trim())) {
        throw new Error("SciVerse resource 模式需要提供 fileName 参数。");
      }
    } else if (m === "relations") {
      if (![input.id, input.query].some((v) => v?.trim())) {
        throw new Error("SciVerse relations 模式需要提供 id (unique_id / DOI) 或 query 参数。");
      }
    } else if (["schema-materials", "schema-citations", "schema-in-paper"].includes(m)) {
      if (![input.schemaId, input.id, input.query].some((v) => v?.trim()) && !(Array.isArray(input.schemaIds) && input.schemaIds.length > 0)) {
        throw new Error(`SciVerse ${m} 模式需要提供 schemaId、id 或 query 参数。`);
      }
    }
  }
  if (name === "list_zotero_pdf_items" && input.collectionKey && !/^[23456789A-Z]{8}$/.test(String(input.collectionKey).trim())) {
    throw new Error("Zotero 分类 Key 格式无效。");
  }
  if (name === "run_doc2md") {
    const mode = input.mode || "research";
    const engine = input.ocrEngine || "mineru";
    if (engine === "existing_raw") {
      requiredText("existingRawDir");
    } else if (mode === "slides") {
      if (!input.pdfPath?.trim() && !input.pptxPath?.trim()) {
        throw new Error("课件解析模式需要提供 pdfPath 或 pptxPath。");
      }
    } else {
      requiredText("pdfPath");
    }
  }
  if (name === "manage_doc2md") {
    const act = input.action || "status";
    if (["resume", "rebuild", "delete"].includes(act)) {
      requiredText("jobId");
    }
    if (["validate", "preview"].includes(act) && !input.jobId?.trim() && !input.outDir?.trim()) {
      throw new Error(`操作 ${act} 需要提供 jobId 或 outDir。`);
    }
  }
}

if (zoteroTools) TOOLS.push({
  name: "zotero_library", description: zoteroTools.description,
  visibility: ["model", "app"], inputSchema: zoteroTools.inputSchema,
  execute: async input => {
    const { settings } = getEffectiveConfig();
    const out = await zoteroTools.executeZoteroLibrary(input, { baseUrl: settings.zoteroUrl, authorize: getOrAuthorizeZoteroWriteKey });
    if (["update_item", "add_note", "create_collection"].includes(input.action)) invalidateZoteroProbeCache();
    return out;
  }
});

for (const tool of TOOLS) tool.inputSchema.additionalProperties = false;

function buildToolsList() {
  return TOOLS.map((t) => ({
    name: t.name,
    description: t.description,
    inputSchema: t.inputSchema,
    execution: { taskSupport: "forbidden" },
    _meta: {
      ui: {
        resourceUri: PANEL_URI,
        surface: SURFACE_ID,
        visibility: t.visibility
      },
      ...(t.visibility.includes("model")
        ? { "openai/ui": { preferredModelDisplayMode: "fullscreen" } }
        : {})
    }
  }));
}

let toolQueue = Promise.resolve();

function applyRequestWorkspace(params) {
  if (process.env.SANMU_HOST !== "codex" || resolveEnvPath(process.env.SANMU_WORKSPACE_ROOT)) return;
  const state = params?._meta?.["codex/sandbox-state"] || params?._meta?.["codex/sandbox-state-meta"];
  const cwd = state?.sandboxCwd;
  if (typeof cwd !== "string" || !cwd.trim()) return;
  let candidate;
  try { candidate = cwd.startsWith("file:") ? fileURLToPath(cwd) : cwd; } catch { return; }
  if (!isAbsolutePath(candidate)) return;
  try { if (statSync(candidate).isDirectory()) WORKSPACE_ROOT = resolve(candidate); } catch {}
}

async function handleRequest(message) {
  if (!message || typeof message !== "object" || Array.isArray(message) || message.jsonrpc !== "2.0" || typeof message.method !== "string") {
    return { jsonrpc: "2.0", id: message?.id ?? null, error: { code: -32600, message: "Invalid JSON-RPC request" } };
  }
  const { id, method, params } = message;
  if (method === "initialize") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: ["2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"].includes(params?.protocolVersion) ? params.protocolVersion : "2025-11-25",
        capabilities: {
          tools: { listChanged: true },
          resources: { listChanged: true },
          experimental: { "codex/sandbox-state": {} }
        },
        serverInfo: {
          name: "sanmu-paper-search",
          version: "1.10.1"
        }
      }
    };
  }
  if (method === "ping") {
    return { jsonrpc: "2.0", id, result: {} };
  }
  if (method === "resources/list") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        resources: [
          {
            uri: PANEL_URI,
            name: "panel",
            mimeType: HTML_MIME
          }
        ]
      }
    };
  }
  if (method === "resources/read") {
    if (params?.uri !== PANEL_URI) {
      return {
        jsonrpc: "2.0",
        id,
        error: { code: -32602, message: `Unknown resource URI: ${params?.uri}` }
      };
    }
    const html = existsSync(PANEL_HTML_PATH)
      ? readFileSync(PANEL_HTML_PATH, "utf8")
      : "<!doctype html><html><body>Panel asset missing</body></html>";
    return {
      jsonrpc: "2.0",
      id,
      result: {
        contents: [
          {
            uri: PANEL_URI,
            mimeType: HTML_MIME,
            text: html
          }
        ]
      }
    };
  }
  if (method === "tools/list") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        tools: buildToolsList()
      }
    };
  }
  if (method === "_claim_browser_pdf") {
    try {
      const operation = toolQueue.then(() => claimBrowserPdf(params || {}));
      toolQueue = operation.catch(() => {});
      const out = await operation;
      return redactSecrets({
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: out.text || "OK" }],
          structuredContent: out.structured || {}
        }
      });
    } catch (err) {
      const msg = redactSecrets(err instanceof Error ? err.message : String(err));
      return {
        jsonrpc: "2.0",
        id,
        result: {
          isError: true,
          content: [{ type: "text", text: `[sanmu-paper-search error] ${msg}` }],
          structuredContent: { error: { code: "execution_failed", message: msg } }
        }
      };
    }
  }
  if (method === "_set_paper_note_tags") {
    try {
      validateInput({ type: "object" }, params || {});
      const input = Object.fromEntries(Object.entries({
        action: "set_note_tags", paperKey: params?.paperKey, paper: params?.paper,
        user_tags: params?.user_tags ?? params?.userTags,
        user_note: params?.user_note ?? params?.userNote
      }).filter(([, value]) => value !== undefined));
      validateInput(TOOLS.find((tool) => tool.name === "manage_basket").inputSchema, input);
      validateToolInput("manage_basket", input);
      const operation = toolQueue.then(async () => {
        let saved;
        updateStateTransaction((state) => {
          saved = setPaperNoteAndTags(
            state,
            params?.paperKey,
            params?.user_tags ?? params?.userTags,
            params?.user_note ?? params?.userNote,
            params?.paper
          );
        });
        const panelState = await getFullPanelState();
        return {
          text: `已保存文献标签与随手批注 (${saved.paperKey})。`,
          structured: { ...panelState, updatedNoteTags: saved }
        };
      });
      toolQueue = operation.catch(() => {});
      const out = await operation;
      return redactSecrets({
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: out.text }],
          structuredContent: out.structured
        }
      });
    } catch (err) {
      const msg = redactSecrets(err instanceof Error ? err.message : String(err));
      return {
        jsonrpc: "2.0",
        id,
        result: {
          isError: true,
          content: [{ type: "text", text: `[sanmu-paper-search error] ${msg}` }],
          structuredContent: { error: { code: "execution_failed", message: msg } }
        }
      };
    }
  }
  if (method === "tools/call") {
    const toolName = params?.name;
    const tool = TOOLS.find((t) => t.name === toolName);
    if (!tool) {
      return {
        jsonrpc: "2.0",
        id,
        error: { code: -32601, message: `Tool not found: ${toolName}` }
      };
    }
    try {
      const input = params?.arguments === undefined ? {} : params.arguments;
      validateInput(tool.inputSchema, input);
      validateToolInput(toolName, input);
      // One writer per MCP process: UI/model calls must not overwrite state read before an await.
      const operation = toolQueue.then(() => {
        applyRequestWorkspace(params);
        return tool.execute(input);
      });
      toolQueue = operation.catch(() => {});
      const out = await operation;
      return redactSecrets({
        jsonrpc: "2.0",
        id,
        result: {
          ...(out.isError ? { isError: true } : {}),
          content: [{ type: "text", text: out.text || "OK" }],
          structuredContent: out.structured || {}
        }
      });
    } catch (err) {
      const msg = redactSecrets(err instanceof Error ? err.message : String(err));
      return {
        jsonrpc: "2.0",
        id,
        result: {
          isError: true,
          content: [{ type: "text", text: `[sanmu-paper-search error] ${msg}` }],
          structuredContent: { error: { code: "execution_failed", message: msg, ...(err.runId ? { runId: err.runId, exitCode: err.exitCode } : {}) } }
        }
      };
    }
  }
  return {
    jsonrpc: "2.0",
    id,
    error: { code: -32601, message: `Method not found: ${method}` }
  };
}

async function startStdio() {
  try {
    for await (const line of createInterface({ input: process.stdin })) {
      const trimmed = line.trim();
      if (!trimmed) continue;
      let message;
      try {
        message = JSON.parse(trimmed);
      } catch {
        process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: null, error: { code: -32700, message: "Parse error" } }) + "\n");
        continue;
      }
      if (message && typeof message === "object" && !Array.isArray(message) && message.id === undefined) continue;
      let response;
      try {
        response = await handleRequest(message);
      } catch (err) {
        response = { jsonrpc: "2.0", id: message?.id ?? null, error: { code: -32603, message: redactSecrets(err.message || "Internal error") } };
      }
      process.stdout.write(JSON.stringify(response) + "\n");
    }
  } finally {
    if (browserPanel) {
      await browserPanel.close();
      browserPanel = null;
    }
  }
}

export {
  handleRequest,
  startStdio,
  resolveEnvPath,
  readUserConfigFromEnv,
  resolveHostPaths,
  buildRelationshipGraph,
  formatZoteroBasenameJs,
  inspectLocalDocumentFile,
  stopKatexPreviewServer
};
if (process.argv[1] && resolve(process.argv[1]) === __filename) await startStdio();
