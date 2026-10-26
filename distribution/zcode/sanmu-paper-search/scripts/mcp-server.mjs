import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
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
import { basename, dirname, extname, join, relative, resolve } from "node:path";
import { createInterface } from "node:readline";
import { fileURLToPath } from "node:url";

const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

const PLUGIN_ROOT = process.env.SANMU_PLUGIN_ROOT || resolve(__dirname, "..");
const WORKSPACE_ROOT = process.env.ZCODE_WORKSPACE_ROOT || process.env.ZCODE_PROJECT_DIR || process.cwd();
const PLUGIN_DATA =
  process.env.ZCODE_PLUGIN_DATA ||
  join(homedir(), ".zcode", "plugin-data", "sanmu-paper-search");

const RUNS_DIR = join(PLUGIN_DATA, "runs");
const CONFIG_PATH = join(PLUGIN_DATA, "config.json");
const STATE_PATH = join(PLUGIN_DATA, "state.json");
const EASYSCHOLAR_CACHE_PATH = join(PLUGIN_DATA, "easyscholar-cache.json");
const ZOTERO_KEYS_PATH = join(PLUGIN_DATA, "zotero-local-keys.json");
const OFFICIAL_ZOTERO_KEYS_PATH = join(dirname(PLUGIN_DATA), "zotero@zcode-plugins-official", "local-api-keys.json");
const PANEL_URI = "ui://sanmu-paper-search/panel.html";
const SURFACE_ID = "sanmu-paper-search";
const HTML_MIME = "text/html;profile=mcp-app";
const PANEL_HTML_PATH = join(PLUGIN_ROOT, "assets", "panel.html");
const PAPER_SEARCH_PY = join(PLUGIN_ROOT, "scripts", "paper_search.py");
const FETCH_PDF_PY = join(PLUGIN_ROOT, "scripts", "fetch_pdf.py");

mkdirSync(RUNS_DIR, { recursive: true });

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

function savePanelConfig(patch) {
  const current = loadPanelConfig();
  const next = { ...current };
  const secretKeys = new Set([
    "openalexApiKey",
    "s2ApiKey",
    "exaApiKey",
    "serperApiKey",
    "easyscholarSecretKey",
    "ai4scholarApiKey"
  ]);
  for (const [k, v] of Object.entries(patch || {})) {
    if (k === "clearKeys" || v === undefined || v === null) continue;
    if (typeof v === "string") {
      if (secretKeys.has(k)) {
        const cleaned = k === "easyscholarSecretKey" ? v.replace(/\s+/g, "").trim() : v.trim();
        if (cleaned !== "") next[k] = cleaned;
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
    }
  }
  writeJsonSafe(CONFIG_PATH, next);
  return next;
}

function loadState() {
  const saved = readStoredObject(STATE_PATH);
  if ((saved.basket !== undefined && !Array.isArray(saved.basket)) ||
      (saved.evidenceMap !== undefined && (!saved.evidenceMap || typeof saved.evidenceMap !== "object" || Array.isArray(saved.evidenceMap)))) {
    throw new Error("state.json 中的收藏篮或阅读记录格式无效；已保留原文件。");
  }
  return {
    lastRunId: null,
    runNavHistory: [],
    lastZoteroImport: null,
    basket: [],
    evidenceMap: {},
    zoteroImports: {},
    runExports: {},
    ...saved
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
  writeJsonSafe(STATE_PATH, state);
  return state;
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
  for (const key of ["openalexApiKey", "s2ApiKey", "exaApiKey", "serperApiKey", "easyscholarSecretKey", "ai4scholarApiKey"]) {
    if (typeof cfg[key] === "string" && cfg[key].trim()) {
      secrets.add(cfg[key].trim());
      secrets.add(cfg[key].replace(/\s+/g, "").trim());
    }
  }
  for (const [key, val] of Object.entries(process.env)) {
    if (/(?:API_KEY|TOKEN|SECRET|SECRET_KEY|PASSWORD)$/i.test(key) && val && !val.startsWith("${")) {
      secrets.add(val);
      secrets.add(val.replace(/\s+/g, "").trim());
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

function matchLocalSavedPdf(paper, pdfIndex) {
  if (!paper || !pdfIndex || !pdfIndex.entries.length) return null;
  const stem = makeSafeFileStem(paper.year, paper.title, "paper").toLowerCase();
  if (pdfIndex.byExactStem.has(stem)) {
    return pdfIndex.byExactStem.get(stem);
  }
  const doi = paper.identifiers?.doi?.[0];
  if (doi) {
    const cleanDoi = normalizeDoi(doi);
    const doiAlnum = cleanDoi.replace(/[^a-z0-9]+/g, "");
    const doiTail = (cleanDoi.split("/").pop() || "").replace(/[^a-z0-9]+/g, "");
    for (const item of pdfIndex.entries) {
      if ((doiAlnum.length >= 8 && item.normAlnum.includes(doiAlnum)) ||
          (doiTail.length >= 7 && item.normAlnum.includes(doiTail))) {
        return item.fullPath;
      }
    }
  }
  if (paper.title && String(paper.title).trim().length >= 8) {
    const normTitle = String(paper.title)
      .toLowerCase()
      .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, "")
      .slice(0, 48);
    if (normTitle.length >= 8) {
      for (const item of pdfIndex.entries) {
        if (item.stemLower.startsWith(stem) || item.normAlnum.includes(normTitle)) {
          return item.fullPath;
        }
      }
    }
  }
  return null;
}

function resolveCredential(panelVal, userConfigEnvName, sysEnvNames) {
  if (panelVal && String(panelVal).trim()) {
    return { value: String(panelVal).trim(), source: "panel" };
  }
  const ucVal = process.env[userConfigEnvName];
  if (ucVal && String(ucVal).trim() && !String(ucVal).startsWith("${")) {
    return { value: String(ucVal).trim(), source: "userConfig" };
  }
  for (const name of sysEnvNames) {
    const sysVal = process.env[name];
    if (sysVal && String(sysVal).trim()) {
      return { value: String(sysVal).trim(), source: "systemEnv" };
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
  const zoteroUrl = validateZoteroUrl(resolveCredential(cfg.zoteroUrl, "USER_CONFIG_ZOTERO_URL", []).value || "http://127.0.0.1:23119");
  const exportSubdir = resolveCredential(cfg.exportSubdir, "USER_CONFIG_EXPORT_SUBDIR", []).value || "00-收件箱-待整理/文献检索";

  const childEnv = {
    ...process.env,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1",
    EASYSCHOLAR_CACHE_PATH
  };
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

  const pyRt = resolvePythonRuntime();

  return {
    childEnv,
    status: {
      python_runtime: { configured: pyRt.supported, source: pyRt.version ? `Python ${pyRt.version}` : "missing" },
      polite_email: { configured: Boolean(polite.value), source: polite.source },
      openalex_api_key: { configured: Boolean(openalex.value), source: openalex.source },
      semantic_scholar_api_key: {
        configured: Boolean(s2.value || ai4scholar.value),
        source: s2.value ? s2.source : ai4scholar.value ? `ai4scholar (${ai4scholar.source})` : "none"
      },
      exa_api_key: { configured: Boolean(exa.value), source: exa.source },
      serper_api_key: { configured: Boolean(serper.value), source: serper.source },
      easyscholar_secret_key: { configured: Boolean(easyscholar.value), source: easyscholar.source },
      ai4scholar_api_key: { configured: Boolean(ai4scholar.value), source: ai4scholar.source },
      europepmc_public_access: { configured: true, source: "public" }
    },
    settings: {
      politeEmail: cfg.politeEmail || "",
      politeEmailConfigured: Boolean(polite.value),
      politeEmailSource: polite.source,
      zoteroUrl,
      exportSubdir,
      resolvedExportDir: isAbsolutePath(exportSubdir)
        ? resolve(exportSubdir)
        : resolve(WORKSPACE_ROOT, exportSubdir.replace(/^[/\\]+/, "")),
      defaultSources: cfg.defaultSources || ["openalex", "semantic-scholar", "crossref"],
      defaultLimit: cfg.defaultLimit || 10,
      runsDir: RUNS_DIR,
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
  return [...new Set(affiliations.map(formatAffiliationItem).filter(Boolean))];
}

function normalizeDoi(value) {
  return String(value || "").trim().replace(/^(?:https?:\/\/(?:dx\.)?doi\.org\/|doi:\s*)/i, "").toLowerCase();
}

function samePaperIdentity(target, candidate) {
  const a = target?.identifiers || {};
  const b = candidate?.identifiers || {};
  const aDois = (a.doi || []).map(normalizeDoi);
  const bDois = (b.doi || []).map(normalizeDoi);
  if (aDois.length && bDois.length) return aDois.some((id) => bDois.includes(id));
  for (const ns of ["openalex", "semantic_scholar", "pmid", "pmcid", "arxiv"]) {
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

function isLikelyLandingPageUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== "string") return true;
  const u = rawUrl.trim().toLowerCase();
  if (!u.startsWith("https://") && !u.startsWith("http://arxiv.org/")) return true;
  if (
    /^https?:\/\/(dx\.)?doi\.org\//.test(u) ||
    u.includes("pubmed.ncbi.nlm.nih.gov/") ||
    u.includes("doaj.org/article/") ||
    u.includes("semanticscholar.org/paper/") ||
    u.includes("openalex.org/w") ||
    u.includes("europepmc.org/article/") ||
    u.includes("figshare.com/articles/") ||
    u.includes("hal.science/hal-") && !u.endsWith("/document") && !u.endsWith(".pdf") ||
    /ncbi\.nlm\.nih\.gov\/pmc\/articles\/pmc\d+\/?$/.test(u) ||
    u.includes("api.elsevier.com/content/article/") ||
    u.includes("harvest.aps.org/") ||
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
  if (/\.pdf(\?.*)?$/i.test(low)) return true;
  if (
    low.includes("/pdf/") ||
    low.includes("/pdfdirect/") ||
    low.includes("/articlepdf/") ||
    low.includes("/article-pdf/") ||
    low.includes("/bitstreams/") ||
    low.includes("delivery.cfm") ||
    low.includes("blobtype=pdf")
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

function buildClickableLinks(paper) {
  const links = [];
  const seen = new Set();
  const add = (label, url, kind, isDirectPdf = false) => {
    if (!url || typeof url !== "string" || !url.startsWith("http")) return;
    if (seen.has(url)) return;
    seen.add(url);
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
    const cleanPmc = String(pmcid).startsWith("PMC") ? pmcid : `PMC${pmcid}`;
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
    if (loc?.url) {
      const isPdf = isLikelyDirectPdfUrl(loc.url);
      const normUrl = isPdf ? normalizeDirectPdfUrl(loc.url) : loc.url;
      const tag = isPdf
        ? `OA PDF [${loc.provider || "web"}]`
        : loc.license_status === "open"
          ? `OA 网页 [${loc.provider || "web"}]`
          : `来源链接 [${loc.provider || "web"}]`;
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

  if (
    !forceRefresh &&
    zoteroStatusCache.baseUrl === baseUrl &&
    nowMs - zoteroStatusCache.checkedAt < ZOTERO_CACHE_TTL_MS
  ) {
    zoteroOnline = zoteroStatusCache.zoteroOnline;
    serverId = zoteroStatusCache.serverId;
    colMap = zoteroStatusCache.colMap;
    collections = zoteroStatusCache.collections;
  } else {
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
    if (zoteroOnline) {
      const colRes = await fetchZoteroCollectionsMap(baseUrl);
      colMap = colRes.colMap;
      collections = colRes.collections;
    } else {
      zoteroPaperMatchCache.clear();
    }
    zoteroStatusCache.checkedAt = Date.now();
    zoteroStatusCache.baseUrl = baseUrl;
    zoteroStatusCache.zoteroOnline = zoteroOnline;
    zoteroStatusCache.serverId = serverId;
    zoteroStatusCache.colMap = colMap;
    zoteroStatusCache.collections = collections;
  }

  if (!zoteroOnline) {
    return { zoteroOnline: false, serverId: null, collections: [], matches: {} };
  }

  if (!Array.isArray(papers) || papers.length === 0) {
    return { zoteroOnline, serverId, collections, matches: {} };
  }

  const norm = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
  const matches = {};

  for (const paper of papers.slice(0, 30)) {
    const key = paper.paper_key || computePaperKey(paper);
    const cacheKey = `${baseUrl}::${key}`;
    if (!forceRefresh) {
      const cached = zoteroPaperMatchCache.get(cacheKey);
      if (cached && Date.now() - cached.checkedAt < ZOTERO_CACHE_TTL_MS) {
        if (cached.matchedObj) matches[key] = cached.matchedObj;
        continue;
      }
    }

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
      continue;
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

async function savePaperToLocalZotero(paper, collectionKey = "") {
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");

  const pKey = paper.paper_key || computePaperKey(paper);
  invalidateZoteroProbeCache(pKey);
  // First check if already in Zotero
  const probe = await probeLocalZotero([paper], true);
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

  const rankSummary = paper?.venue_rank?.summary ? String(paper.venue_rank.summary).trim() : "";
  const extraTags = [{ tag: "sanmu-paper-search" }];
  if (paper?.venue_rank?.jcr) extraTags.push({ tag: `JCR:${paper.venue_rank.jcr}` });
  if (paper?.venue_rank?.cas_up) extraTags.push({ tag: paper.venue_rank.cas_up });
  if (paper?.venue_rank?.ccf) extraTags.push({ tag: `CCF-${paper.venue_rank.ccf}` });

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

  // If paper already has a downloaded local PDF on disk (or auto-discovered in saveDir), attach it to the new Zotero item!
  let pdfAttached = false;
  let attachmentKey = null;
  const localPdfPath =
    (paper?.local_pdf_path && existsSync(paper.local_pdf_path) ? paper.local_pdf_path : null) ||
    matchLocalSavedPdf(paper, scanSaveDirPdfIndex());
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

  let undoAvailable = false;
  let undoWarning = null;
  try {
    await rememberZoteroImport(baseUrl, serverId, apiKey, createdKey, pKey, attachmentKey);
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

async function rememberZoteroImport(baseUrl, serverId, apiKey, itemKey, paperKey, attachmentKey = null) {
  const headers = { "Zotero-Allowed-Request": "1", "Zotero-API-Key": apiKey, "Zotero-Server-ID": serverId };
  const resp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}`, { headers, signal: AbortSignal.timeout(8000) });
  if (!resp.ok) throw new Error("条目已保存，但未能回读核验；为保护文献库，暂不提供自动撤销。");
  const item = await resp.json();
  const version = item?.version ?? item?.data?.version;
  if (item?.key !== itemKey || !item.data || !Number.isInteger(version)) throw new Error("Zotero 回读身份/版本无效，无法建立自动撤销记录。");
  const record = { itemKey, paperKey, serverId, baseUrl, version, snapshot: zoteroItemSnapshot(item), attachmentKey, children: {} };
  if (attachmentKey) {
    const childResp = await fetch(`${baseUrl}/api/users/0/items/${attachmentKey}`, { headers, signal: AbortSignal.timeout(8000) });
    if (!childResp.ok) throw new Error("未能回读 PDF 附件，无法建立自动撤销记录。");
    const child = await childResp.json();
    if (child?.data?.parentItem !== itemKey) throw new Error("PDF 附件父条目不匹配。");
    record.children[attachmentKey] = { version: child.version ?? child.data.version, snapshot: zoteroItemSnapshot(child) };
  }
  const state = loadState();
  state.zoteroImports = { ...(state.zoteroImports || {}), [itemKey]: record };
  saveState(state);
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
  // Delete only the verified parent; Zotero handles its verified children atomically.
  const delResp = await fetch(`${baseUrl}/api/users/0/items/${itemKey}`, {
    method: "DELETE", headers: { ...headers, "If-Unmodified-Since-Version": String(record.version) }, signal: AbortSignal.timeout(10000)
  });
  if (!delResp.ok && delResp.status !== 404) throw new Error(`撤销 Zotero 导入失败 (HTTP ${delResp.status})，导入记录已保留。`);
  return { deleted: true, itemKey };
}

function normalizeVenueCacheKey(name) {
  return String(name || "")
    .replace(/\s*\((?:online|print|electronic|issn[^)]*)\)\s*$/i, "")
    .replace(/\s*\[[A-Z]\]\s*$/, "")
    .toLowerCase()
    .replace(/[^a-z0-9\u4e00-\u9fa5]+/g, " ")
    .trim();
}

function enrichPapersList(papers, state, zoteroMatches = {}, runMeta = null) {
  const basketKeys = new Set((state.basket || []).map((p) => p.paper_key || computePaperKey(p)));
  const pdfIndex = scanSaveDirPdfIndex();
  const rankCache = readJsonSafe(EASYSCHOLAR_CACHE_PATH, {}) || {};
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
    const explicitLocalPdf =
      savedEv.pdfPath && existsSync(savedEv.pdfPath)
        ? savedEv.pdfPath
        : p.local_pdf_path && existsSync(p.local_pdf_path)
          ? p.local_pdf_path
          : null;
    const localPdf = explicitLocalPdf || matchLocalSavedPdf(p, pdfIndex) || null;

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
    const normalizedAuthors = (p.authors || []).map((a) => ({
      ...a,
      affiliations: normalizeAffiliationsList(a.affiliations)
    }));
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

    return {
      ...p,
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
      local_pdf_path: localPdf,
      in_basket: basketKeys.has(key),
      zotero_match: zMatch
    };
  });
}

function extractDoisFromText(text) {
  if (!text) return [];
  const matches = String(text).match(/10\.\d{4,9}\/[-._;()/:A-Za-z0-9]+/g) || [];
  return [...new Set(matches.map((m) => m.replace(/[.,;:)\]]+$/, "").toLowerCase()))];
}

function parseRawItemToCandidatePaper(flatItem, idx, state) {
  const dois = flatItem.extracted_dois || [];
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

  const identifiers = {};
  if (dois.length) identifiers.doi = dois;
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

  return enrichPapersList([rawPaper], state)[0];
}

function normalizeRawResults(resultsRaw, state) {
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

    const snippetText = JSON.stringify(flat);
    const extractedDois = extractDoisFromText(snippetText);
    const enrichedItem = {
      ...flat,
      index: idx + 1,
      extracted_dois: extractedDois
    };
    // General web/maps/shop snippets can mention unrelated DOIs and are not paper identities.
    enrichedItem.candidate_paper = flat.serper_type === "scholar"
      ? parseRawItemToCandidatePaper(enrichedItem, idx, state) : null;
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
    const kw = typeof q === "string" ? q : q.query || q.id || "检索";
    const fetchMode = runJson?.fetch_mode || q?.fetch || "none";
    const actuallyFetched = Boolean(runJson?.fulltext_xml_fetched || runJson?.supplementary_fetched);
    return {
      modeLabel: "Europe PMC 与全文/SI",
      title: `Europe PMC：「${kw}」`,
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
  return {
    modeLabel: cmd,
    title: `检索记录 (${cmd})`,
    subtitle: `${paperCount || resultCount} 条记录`
  };
}

function loadRunBundle(runIdOrPath, zoteroMatches = {}) {
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
            ext === ".svg" ||
            ext === ".bib" ||
            rel.startsWith("supplementary/") ||
            entry.name === "annotated_text.md" ||
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
  let papers = enrichPapersList(papersRaw, state, zoteroMatches, {
    fetchedPaperKey: hasFetchedFiles && papersRaw[0] ? computePaperKey(papersRaw[0]) : null
  });
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
      const enrichedRemoved = enrichPapersList(removedList, state, zoteroMatches).map((rp, idx) => ({
        ...rp,
        index: papers.length + idx + 1,
        diff_status: "removed",
        diff_fields: []
      }));
      papers = [...papers, ...enrichedRemoved];
    }
  }
  const enrichedResults = normalizeRawResults(resultsRaw, state);

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

  const sourceStatuses = rawRunJson.source_status || rawRunJson.source_statuses || {};
  const cmdName = rawRunJson.command || rawRunJson.action || (acquisitionJson ? "fetch_pdf" : "search");
  const humanSummary = formatRunHumanSummary(rawRunJson, acquisitionJson, papers.length, enrichedResults.length);

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

function listRecentRuns(limit = 40) {
  if (!existsSync(RUNS_DIR)) return [];
  const entries = readdirSync(RUNS_DIR, { withFileTypes: true })
    .filter((e) => e.isDirectory() && !e.name.startsWith("."))
    .map((e) => {
      const dir = join(RUNS_DIR, e.name);
      const st = statSync(dir);
      const runJson = readJsonSafe(join(dir, "run.json"), null);
      const acqJson = readJsonSafe(join(dir, "acquisition.json"), null);
      const papers = readJsonSafe(join(dir, "papers.json"), []);
      const resultsRaw = readJsonSafe(join(dir, "results.json"), null);
      const paperCount = Array.isArray(papers) ? papers.length : 0;
      const resultCount = countNormalizedResults(resultsRaw);
      const cmd = runJson?.command || runJson?.action || (acqJson ? "fetch_pdf" : "search");
      const human = formatRunHumanSummary(runJson, acqJson, paperCount, resultCount);
      return {
        runId: e.name,
        runDir: dir,
        mtimeMs: st.mtimeMs,
        timestamp: runJson?.created_at || runJson?.timestamp || acqJson?.retrieved_at || new Date(st.mtimeMs).toISOString(),
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
    })
    .sort((a, b) => b.mtimeMs - a.mtimeMs);
  return entries.slice(0, limit);
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
        `${k}: ${v.status}${v.error ? ` (${v.error})` : ""} [fetched=${v.fetched ?? 0}${v.truncated ? ", truncated" : ""}]`
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

function buildRelationshipGraph(activeBundle, recentRuns, basket, state, zoteroMatches = {}) {
  const nodeMap = new Map();
  const aliasToNodeId = new Map();
  const edges = [];
  const edgeKeys = new Set();

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

  const upsertPaperNode = (paper, role = "search_hit", priority = 1) => {
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
      const enriched = enrichPapersList(rPapers.slice(0, 15), state, zoteroMatches);
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
      centerNodeId = upsertPaperNode(knownSeed, "seed", 10);
    }

    for (const p of activePapers) {
      const rel = (p.relations || []).find((r) => r && ["references", "citations", "recommendations"].includes(r.type));
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
      const nid = upsertPaperNode(p, role, prio);
      if (!centerNodeId && role === "seed") centerNodeId = nid;

      if (nid && rel?.seed) {
        const seedNorm = normalizeGraphNodeId(rel.seed);
        const seedNodeId = aliasToNodeId.get(seedNorm) || centerNodeId;
        if (seedNodeId) {
          if (rel.type === "references") {
            addEdge(seedNodeId, nid, "cites", "引用参考文献");
          } else if (rel.type === "citations") {
            addEdge(nid, seedNodeId, "cites", "施引");
          } else if (rel.type === "recommendations") {
            addEdge(seedNodeId, nid, "similar", "相似推荐");
          }
        }
      }
    }
  }

  // 2. Also include other related runs that share the same seed (or multi-hop seeds) so references + citations + recommendations appear together!
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
      if (!matchedSeedNodeId && nodeMap.size > 0) continue;

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
          9
        );
      if (!centerNodeId) centerNodeId = seedNid;

      const rPapersRaw = readJsonSafe(join(RUNS_DIR, basename(rMeta.runId), "papers.json"), []);
      const rPapers = enrichPapersList(rPapersRaw.slice(0, 12), state, zoteroMatches);
      for (const p of rPapers) {
        const relType = rMeta.query?.relation || "references";
        const role =
          relType === "references"
            ? "reference"
            : relType === "citations"
              ? "citation"
              : "recommendation";
        const nid = upsertPaperNode(p, role, 4);
        if (nid && seedNid) {
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
    upsertPaperNode(bp, "basket", 3);
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

  const nodes = [...nodeMap.values()];
  return {
    centerNodeId: centerNodeId || nodes[0]?.id || null,
    nodes,
    edges,
    counts: {
      nodes: nodes.length,
      citesEdges: edges.filter((e) => e.type === "cites").length,
      similarEdges: edges.filter((e) => e.type === "similar").length,
      coauthorEdges: edges.filter((e) => e.type === "coauthor").length
    }
  };
}

async function getFullPanelState(activeRunId = null) {
  const state = loadState();
  const eff = getEffectiveConfig();
  const recentRuns = listRecentRuns(35);
  const targetRunId = activeRunId || state.lastRunId || recentRuns[0]?.runId || null;

  let activeBundle = null;
  let zoteroProbe = { zoteroOnline: false, collections: [], matches: {} };
  if (targetRunId) {
    try {
      const rawPapers = readJsonSafe(join(RUNS_DIR, basename(targetRunId), "papers.json"), []);
      const combinedForProbe = [...(Array.isArray(rawPapers) ? rawPapers : []), ...(state.basket || [])];
      zoteroProbe = await probeLocalZotero(combinedForProbe);
      activeBundle = loadRunBundle(targetRunId, zoteroProbe.matches);
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

  const enrichedBasket = enrichPapersList(state.basket || [], state, zoteroProbe.matches);
  const graphData = buildRelationshipGraph(
    activeBundle,
    recentRuns,
    enrichedBasket,
    state,
    zoteroProbe.matches
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
    evidenceMap: state.evidenceMap || {},
    zoteroImports: Object.keys(state.zoteroImports || {}),
    basket: enrichedBasket,
    citationsPreview: generateCitations(enrichedBasket),
    graphData
  };
}

const TOOLS = [
  {
    name: "open_panel",
    description:
      "打开「三木文献检索 (Sanmu Paper Search)」侧边栏面板，查看当前检索结果、多源配置状态、历史运行批次与文献收藏篮。",
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
      return {
        text: panelState.activeRun
          ? summarizeRunForModel(panelState.activeRun)
          : "三木文献检索侧边栏已就绪。可在面板中选择文献源、配置 API 或直接发起检索。",
        structured: panelState
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
        zoteroUrl: { type: "string" },
        exportSubdir: { type: "string" },
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
        yearTo: { type: "integer", description: "结束年份（如 2026）" }
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
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Search failed");
      const state = loadState();
      recordMainRunNavigation(state, runId);
      saveState(state);

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
      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }
      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
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
      const latestState = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(latestState, runId);
        saveState(latestState);
      }

      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
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
      if (input.yearFrom && (hasAuthorId || hasFromPaper)) args.push("--year-from", String(input.yearFrom));
      if (input.yearTo && (hasAuthorId || hasFromPaper)) args.push("--year-to", String(input.yearTo));
      if (getEffectiveConfig().status.easyscholar_secret_key.configured) {
        args.push("--with-ranks");
      }

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      // A confirmed S2 author ID must never silently become an OpenAlex name search.
      requirePythonBundle(pyRes, outDir, "Author query failed");
      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }

      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
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
          const exportState = loadState();
          exportState.runExports = { ...(exportState.runExports || {}), [runId]: exportedSaveDir };
          saveState(exportState);
        }
      }

      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }

      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
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

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      requirePythonBundle(pyRes, outDir, "Exa search failed");
      const state = loadState();
      recordMainRunNavigation(state, runId);
      saveState(state);

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
      const state = loadState();
      recordMainRunNavigation(state, runId);
      saveState(state);

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
      "定位开放获取（OA）全文位置（含 OpenAlex、S2、Unpaywall），或从已核实的公开 HTTPS PDF 链接安全下载 PDF 文件（支持指定保存目录 saveDir，自动以论文标题命名保存）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        action: {
          type: "string",
          enum: ["resolve_oa", "download_pdf"],
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
      if (input.action === "download_pdf") {
        const cleanUrl = input.pdfUrl ? String(input.pdfUrl).trim() : "";
        const cleanDoi = input.doi ? String(input.doi).trim() : "";
        if (!cleanUrl && !cleanDoi) throw new Error("pdfUrl or doi is required for download_pdf");
        const runId = makeRunId("pdf");
        const outDir = join(RUNS_DIR, runId);
        const args = [];
        if (cleanUrl) args.push(cleanUrl);
        args.push("--out", outDir);
        if (cleanDoi) args.push("--doi", cleanDoi);
        if (!cleanUrl && cleanDoi) args.push("--resolve-doi");
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

        const state = loadState();
        const key = input.paperKey || (input.doi ? `doi:${String(input.doi).toLowerCase()}` : null);
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
        saveState(state);

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
      const state = loadState();
      const resolvedRaw = readJsonSafe(join(outDir, "papers.json"), []);
      if (Array.isArray(resolvedRaw) && resolvedRaw.length > 0) {
        for (const rp of resolvedRaw) {
          const pKey = input.paperKey || rp.paper_key || computePaperKey(rp);
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
      }
      saveState(state);
      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
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
        const state = loadState();
        if (input.runId) {
          resolveRunDir(input.runId);
          recordMainRunNavigation(state, input.runId);
          saveState(state);
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
        const state = loadState();
        state.runNavHistory = (state.runNavHistory || []).filter((id) => !targets.includes(id));
        if (state.lastRunId && targets.includes(state.lastRunId)) {
          const remaining = listRecentRuns(1);
          state.lastRunId = remaining[0]?.runId || null;
        }
        saveState(state);
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
        const state = loadState();
        state.lastRunId = null;
        state.runNavHistory = [];
        saveState(state);
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
        const state = loadState();
        recordMainRunNavigation(state, runId);
        saveState(state);
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
        const state = loadState();
        recordMainRunNavigation(state, runId);
        saveState(state);
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
        paperKey: { type: "string", description: "当 action=remove / update_evidence / undo_zotero_save 时的 paper_key" },
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
        reason: { type: "string", description: "入选/排除理由或阅读备注" }
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
        state = loadState();
        const key = input.paper.paper_key || computePaperKey(input.paper);
        const prev = state.evidenceMap[key] || {};
        state.evidenceMap[key] = {
          ...prev,
          zoteroUndoneKey: null,
          zoteroUndoneAt: null,
          zoteroMatch: {
            matched: true,
            itemKey: zRes.itemKey,
            attachmentKey: zRes.attachmentKey || null,
            createdByPlugin: Boolean(state.zoteroImports?.[zRes.itemKey]),
            undoAvailable: Boolean(state.zoteroImports?.[zRes.itemKey]),
            selectUri: zRes.selectUri,
            collections: zRes.collections || []
          },
          updatedAt: new Date().toISOString()
        };
        if (!zRes.alreadyExists && zRes.undoAvailable) {
          state.lastZoteroImport = {
            paperKey: key,
            itemKey: zRes.itemKey,
            attachmentKey: zRes.attachmentKey || null,
            title: input.paper.title || key,
            timestamp: new Date().toISOString()
          };
        }
        saveState(state);
        const panelState = await getFullPanelState();
        return {
          text: zRes.alreadyExists
            ? `该论文已存在于本机 Zotero 库中（Key: \`${zRes.itemKey}\`）。`
            : `✅ 已成功将论文存入本机 Zotero（Key: \`${zRes.itemKey}\`${zRes.pdfAttached ? "，并已自动上传附加本地 PDF 全文" : ""}）。支持随时点击「↩ 撤销导入」撤回。`,
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
        state = loadState();
        delete state.zoteroImports[targetKey];
        if (pKey && state.evidenceMap[pKey]) {
          delete state.evidenceMap[pKey].zoteroMatch;
          state.evidenceMap[pKey].zoteroUndoneKey = targetKey;
          state.evidenceMap[pKey].zoteroUndoneAt = Date.now();
          state.evidenceMap[pKey].updatedAt = new Date().toISOString();
        }
        for (const [k, ev] of Object.entries(state.evidenceMap || {})) {
          if (ev?.zoteroMatch?.itemKey === targetKey) {
            delete state.evidenceMap[k].zoteroMatch;
            state.evidenceMap[k].zoteroUndoneKey = targetKey;
            state.evidenceMap[k].zoteroUndoneAt = Date.now();
          }
        }
        if (state.lastZoteroImport?.itemKey === targetKey) {
          state.lastZoteroImport = null;
        }
        saveState(state);
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
        state = loadState();
        const prev = state.evidenceMap[key] || {};
        state.evidenceMap[key] = {
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
        if (Array.isArray(state.basket)) {
          state.basket = state.basket.map((bp) =>
            (bp.paper_key || computePaperKey(bp)) === key ? { ...bp, ...enriched } : bp
          );
        }
        saveState(state);
        const panelState = await getFullPanelState();
        return {
          text: enriched.abstract
            ? `✅ 已跨源补全《${input.paper.title}》的完整摘要与题录信息！`
            : `已跨源补全题录信息（当前公开源暂未收录该文摘要文本）。`,
          structured: { ...panelState, enrichedPaperKey: key, enrichedData: enriched }
        };
      }
      if (act === "add" && (input.paper || (Array.isArray(input.papers) && input.papers.length))) {
        const itemsToAdd = Array.isArray(input.papers) && input.papers.length ? input.papers : [input.paper];
        for (const pItem of itemsToAdd) {
          if (!pItem || typeof pItem !== "object") continue;
          const key = pItem.paper_key || computePaperKey(pItem);
          const exists = state.basket.some((p) => (p.paper_key || computePaperKey(p)) === key);
          if (!exists) {
            state.basket.push({ ...pItem, paper_key: key });
          }
        }
      } else if (act === "remove" && input.paperKey) {
        state.basket = state.basket.filter(
          (p) => (p.paper_key || computePaperKey(p)) !== input.paperKey
        );
      } else if (act === "clear") {
        state.basket = [];
      } else if (act === "update_evidence" && input.paperKey) {
        const prev = state.evidenceMap[input.paperKey] || {};
        state.evidenceMap[input.paperKey] = {
          ...prev,
          ...(input.stage ? { stage: input.stage } : {}),
          ...(input.basis ? { basis: input.basis } : {}),
          ...(input.reason !== undefined ? { reason: input.reason } : {}),
          updatedAt: new Date().toISOString()
        };
      }
      saveState(state);
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
      const state = loadState();
      const targetRun = input?.runId || (!input?.venues?.length ? state.lastRunId : null);
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
      if (!preserve) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }
      const panelState = await getFullPanelState(preserve ? state.lastRunId : runId);
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

      const state = loadState();
      if (!input?.preserveActiveRun && mode !== "credits") {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }
      const panelState = await getFullPanelState(
        input?.preserveActiveRun || mode === "credits" ? state.lastRunId : runId
      );
      const bundle = loadRunBundle(runId);
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

      // Copy generated figure files (PNG, SVG, PDF, PPTX) to user's saveDir
      const destDir = resolveTargetSaveDir(input?.saveDir);
      const copiedPaths = {};
      const runJson = readJsonSafe(join(outDir, "run.json"), {});
      const drawRes = runJson?.draw_result || {};
      const savedMap = drawRes?.savedFiles || {};
      const stem = makeSafeFileStem(new Date().getFullYear(), input?.prompt || mode, runId, runId);
      for (const [k, relName] of Object.entries(savedMap)) {
        const srcPath = join(outDir, relName);
        if (existsSync(srcPath) && statSync(srcPath).isFile()) {
          const ext = extname(relName);
          const suffix = k === "pdf_file" ? "-vector" : k === "pptx_file" ? "-editable" : "";
          const dstPath = uniqueDestination(destDir, `${stem}${suffix}${ext}`, runId);
          copyFileSync(srcPath, dstPath, fsConstants.COPYFILE_EXCL);
          copiedPaths[k] = dstPath;
        }
      }
      if (Object.keys(copiedPaths).length > 0) {
        runJson.exported_save_dir = destDir;
        runJson.draw_result = {
          ...drawRes,
          exportedFiles: copiedPaths
        };
        writeJsonSafe(join(outDir, "run.json"), runJson);
      }

      const state = loadState();
      if (Object.keys(copiedPaths).length > 0) {
        state.runExports = { ...(state.runExports || {}), [runId]: destDir };
      }
      recordMainRunNavigation(state, runId);
      saveState(state);

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
      "politeEmail"
    ];
    if (input.clearKeys?.some((key) => !allowed.includes(key))) throw new Error("clearKeys 只允许清空 API Key 或联系邮箱。");
    if (input.defaultSources?.some((source) => !["openalex", "semantic-scholar", "crossref"].includes(source))) throw new Error("defaultSources 含无效来源。");
  }
  if (name === "manage_basket") {
    const isBatchAdd = input.action === "add" && Array.isArray(input.papers) && input.papers.length > 0;
    if (!isBatchAdd && ["add", "save_to_zotero", "enrich_abstract"].includes(input.action) && (!input.paper || typeof input.paper.title !== "string" || !input.paper.title.trim())) throw new Error("请提供有标题的论文数据。");
    if (["remove", "update_evidence"].includes(input.action)) requiredText("paperKey");
    for (const key of ["paperKey", "zoteroItemKey", "collectionKey"]) if (["__proto__", "prototype", "constructor"].includes(input[key])) throw new Error(`${key} 无效。`);
    if (["zoteroItemKey", "collectionKey"].some((key) => input[key] && !/^[23456789A-Z]{8}$/.test(input[key]))) throw new Error("Zotero 条目或分类 Key 格式无效。");
    if (input.paper) {
      if (["__proto__", "prototype", "constructor"].includes(input.paper.paper_key)) throw new Error("paper_key 无效。");
      for (const key of ["authors", "oa_locations", "provenance", "relations"]) if (input.paper[key] !== undefined && !Array.isArray(input.paper[key])) throw new Error(`paper.${key} 必须为数组。`);
      if (input.paper.identifiers) for (const ids of Object.values(input.paper.identifiers)) if (!Array.isArray(ids) || ids.some((id) => typeof id !== "string")) throw new Error("paper.identifiers 必须使用字符串数组。");
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
}

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
          resources: { listChanged: true }
        },
        serverInfo: {
          name: "sanmu-paper-search",
          version: "1.6.8"
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
      const operation = toolQueue.then(() => tool.execute(input));
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
}

export { handleRequest, startStdio };
if (process.argv[1] && resolve(process.argv[1]) === __filename) await startStdio();
