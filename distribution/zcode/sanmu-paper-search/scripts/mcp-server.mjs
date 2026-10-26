import { spawn, spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import {
  existsSync,
  mkdirSync,
  readFileSync,
  readdirSync,
  statSync,
  writeFileSync,
  cpSync,
  copyFileSync,
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
  read: "已精读"
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
  writeFileSync(filePath, JSON.stringify(data, null, 2) + "\n", "utf8");
}

function loadPanelConfig() {
  return readJsonSafe(CONFIG_PATH, {
    politeEmail: "",
    openalexApiKey: "",
    s2ApiKey: "",
    exaApiKey: "",
    serperApiKey: "",
    zoteroUrl: "http://127.0.0.1:23119",
    exportSubdir: "00-收件箱-待整理/文献检索",
    defaultSources: ["openalex", "semantic-scholar", "crossref"],
    defaultLimit: 10
  });
}

function savePanelConfig(patch) {
  const current = loadPanelConfig();
  const next = { ...current };
  const secretKeys = new Set(["openalexApiKey", "s2ApiKey", "exaApiKey", "serperApiKey"]);
  for (const [k, v] of Object.entries(patch || {})) {
    if (k === "clearKeys" || v === undefined || v === null) continue;
    if (typeof v === "string") {
      if (secretKeys.has(k)) {
        if (v.trim() !== "") next[k] = v.trim();
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
  return readJsonSafe(STATE_PATH, {
    lastRunId: null,
    runNavHistory: [],
    lastZoteroImport: null,
    basket: [],
    evidenceMap: {}
  });
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

function resolveTargetSaveDir(customDir) {
  const { settings } = getEffectiveConfig();
  const raw = String(customDir || settings.exportSubdir || "00-收件箱-待整理/文献检索").trim();
  const destDir = isAbsolutePath(raw)
    ? resolve(raw)
    : resolve(WORKSPACE_ROOT, raw.replace(/^[/\\]+/, ""));
  mkdirSync(destDir, { recursive: true });
  return destDir;
}

function makeSafeFileStem(year, title, fallbackId = "paper") {
  const cleanTitle = String(title || fallbackId || "paper")
    .replace(/[\\/:*?"<>|\r\n\t]+/g, " ")
    .replace(/\s+/g, " ")
    .trim()
    .slice(0, 76);
  const yrPrefix = year && /^\d{4}$/.test(String(year)) ? `${year}-` : "";
  return `${yrPrefix}${cleanTitle || "paper"}`;
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
  const zoteroUrl =
    cfg.zoteroUrl ||
    (process.env.USER_CONFIG_ZOTERO_URL && !process.env.USER_CONFIG_ZOTERO_URL.startsWith("${")
      ? process.env.USER_CONFIG_ZOTERO_URL
      : "http://127.0.0.1:23119");
  const exportSubdir =
    cfg.exportSubdir ||
    (process.env.USER_CONFIG_EXPORT_SUBDIR && !process.env.USER_CONFIG_EXPORT_SUBDIR.startsWith("${")
      ? process.env.USER_CONFIG_EXPORT_SUBDIR
      : "00-收件箱-待整理/文献检索");

  const childEnv = {
    ...process.env,
    PYTHONIOENCODING: "utf-8",
    PYTHONUTF8: "1",
    PYTHONDONTWRITEBYTECODE: "1"
  };
  if (polite.value) {
    childEnv.POLITE_EMAIL = polite.value;
    if (!childEnv.OPENALEX_EMAIL) childEnv.OPENALEX_EMAIL = polite.value;
    if (!childEnv.CROSSREF_EMAIL) childEnv.CROSSREF_EMAIL = polite.value;
    if (!childEnv.UNPAYWALL_EMAIL) childEnv.UNPAYWALL_EMAIL = polite.value;
  }
  if (openalex.value) childEnv.OPENALEX_API_KEY = openalex.value;
  if (s2.value) {
    childEnv.S2_API_KEY = s2.value;
    childEnv.SEMANTIC_SCHOLAR_API_KEY = s2.value;
  }
  if (exa.value) childEnv.EXA_API_KEY = exa.value;
  if (serper.value) childEnv.SERPER_API_KEY = serper.value;

  const pyRt = resolvePythonRuntime();

  return {
    childEnv,
    status: {
      python_runtime: { configured: pyRt.supported, source: pyRt.version ? `Python ${pyRt.version}` : "missing" },
      polite_email: { configured: Boolean(polite.value), source: polite.source },
      openalex_api_key: { configured: Boolean(openalex.value), source: openalex.source },
      semantic_scholar_api_key: { configured: Boolean(s2.value), source: s2.source },
      exa_api_key: { configured: Boolean(exa.value), source: exa.source },
      serper_api_key: { configured: Boolean(serper.value), source: serper.source },
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
    const timer = setTimeout(() => {
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
      resolvePromise({
        exitCode: code ?? 1,
        stdout: trimmed,
        stderr: stderr.trim(),
        parsed
      });
    });
  });
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

function computePaperKey(paper) {
  const ids = paper?.identifiers || {};
  if (ids.doi?.[0]) return `doi:${String(ids.doi[0]).toLowerCase()}`;
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

async function probeLocalZotero(papers) {
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");
  let zoteroOnline = false;
  let serverId = null;
  try {
    const pingResp = await fetch(`${baseUrl}/api/`, {
      method: "GET",
      headers: { "Zotero-Allowed-Request": "1" },
      signal: AbortSignal.timeout(1200)
    });
    zoteroOnline = pingResp.ok || pingResp.status < 500;
    serverId = pingResp.headers.get("zotero-server-id") || null;
  } catch {
    zoteroOnline = false;
  }
  if (!zoteroOnline) {
    return { zoteroOnline: false, serverId: null, collections: [], matches: {} };
  }

  const { colMap, collections } = await fetchZoteroCollectionsMap(baseUrl);
  if (!Array.isArray(papers) || papers.length === 0) {
    return { zoteroOnline, serverId, collections, matches: {} };
  }

  const norm = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
  const matches = {};

  for (const paper of papers.slice(0, 25)) {
    const key = paper.paper_key || computePaperKey(paper);
    const doi = paper?.identifiers?.doi?.[0];
    const cleanDoi = doi ? String(doi).toLowerCase().trim() : "";
    const targetTitle = norm(paper.title);
    const queryCandidates = [];
    if (cleanDoi) {
      queryCandidates.push({ q: cleanDoi, qmode: "everything" });
    }
    if (paper?.title && String(paper.title).trim().length >= 6) {
      queryCandidates.push({ q: String(paper.title).trim().slice(0, 80), qmode: "titleCreatorYear" });
    }
    if (!queryCandidates.length) continue;

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
          const itemDoi = String(data.DOI || "").toLowerCase().trim();
          const itemTitle = norm(data.title);
          const doiMatched = Boolean(cleanDoi && itemDoi && (itemDoi === cleanDoi || itemDoi.includes(cleanDoi)));
          const titleMatched = Boolean(targetTitle && itemTitle && itemTitle === targetTitle);
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

  // First check if already in Zotero
  const probe = await probeLocalZotero([paper]);
  if (!probe.zoteroOnline) {
    throw new Error("未检测到本机 Zotero 桌面端在线，请先启动 Zotero。");
  }
  const pKey = paper.paper_key || computePaperKey(paper);
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
    collections: collectionKey ? [collectionKey] : [],
    tags: [{ tag: "sanmu-paper-search" }]
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

  // If paper already has a downloaded local PDF on disk, attach it to the new Zotero item!
  let pdfAttached = false;
  let attachmentKey = null;
  const localPdfPath = paper?.local_pdf_path;
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

  return {
    alreadyExists: false,
    createdByPlugin: true,
    itemKey: createdKey,
    attachmentKey,
    pdfAttached,
    selectUri: `zotero://select/library/items/${createdKey}`
  };
}

async function deletePaperFromLocalZotero(itemKey, attachmentKey = null) {
  const cleanKey = String(itemKey || "").trim();
  if (!cleanKey) throw new Error("未提供要撤销删除的 Zotero 条目 Key");
  const { settings } = getEffectiveConfig();
  const baseUrl = (settings.zoteroUrl || "http://127.0.0.1:23119").replace(/\/+$/, "");

  const probe = await probeLocalZotero([]);
  if (!probe.zoteroOnline) {
    throw new Error("未检测到本机 Zotero 桌面端在线，请先确认 Zotero 正在运行。");
  }
  const { key: apiKey, serverId } = await getOrAuthorizeZoteroWriteKey(baseUrl, probe.serverId);

  const deleteSingleKey = async (targetKey) => {
    if (!targetKey) return false;
    const getResp = await fetch(`${baseUrl}/api/users/0/items/${encodeURIComponent(targetKey)}`, {
      headers: {
        "Zotero-Allowed-Request": "1",
        "Zotero-API-Key": apiKey,
        "Zotero-Server-ID": serverId
      },
      signal: AbortSignal.timeout(8000)
    });
    if (getResp.status === 404) return true;
    let version = getResp.headers.get("last-modified-version") || "0";
    if (getResp.ok) {
      try {
        const itemObj = await getResp.json();
        if (itemObj?.version !== undefined) version = String(itemObj.version);
      } catch {}
    }
    const delResp = await fetch(`${baseUrl}/api/users/0/items/${encodeURIComponent(targetKey)}`, {
      method: "DELETE",
      headers: {
        "If-Unmodified-Since-Version": String(version),
        "Zotero-Allowed-Request": "1",
        "Zotero-API-Key": apiKey,
        "Zotero-Server-ID": serverId
      },
      signal: AbortSignal.timeout(10000)
    });
    if (delResp.ok || delResp.status === 204 || delResp.status === 404) {
      return true;
    }
    const errText = await delResp.text().catch(() => "");
    throw new Error(`从 Zotero 删除条目 ${targetKey} 失败 (HTTP ${delResp.status}): ${errText.slice(0, 160)}`);
  };

  // Delete child attachment first if present
  if (attachmentKey) {
    try {
      await deleteSingleKey(attachmentKey);
    } catch {}
  } else {
    try {
      const childResp = await fetch(`${baseUrl}/api/users/0/items/${encodeURIComponent(cleanKey)}/children`, {
        headers: {
          "Zotero-Allowed-Request": "1",
          "Zotero-API-Key": apiKey,
          "Zotero-Server-ID": serverId
        },
        signal: AbortSignal.timeout(5000)
      });
      if (childResp.ok) {
        const children = await childResp.json();
        if (Array.isArray(children)) {
          for (const ch of children) {
            if (ch?.key) {
              try {
                await deleteSingleKey(ch.key);
              } catch {}
            }
          }
        }
      }
    } catch {}
  }

  await deleteSingleKey(cleanKey);
  return { deleted: true, itemKey: cleanKey };
}

function enrichPapersList(papers, state, zoteroMatches = {}, runMeta = null) {
  const basketKeys = new Set((state.basket || []).map((p) => p.paper_key || computePaperKey(p)));
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
    let stage = savedEv.stage || p.handoff_stage || "discovered";
    if (stage === "discovered" && (p.abstract_available || unpacked.tldr || p.abstract)) {
      stage = "metadata_checked";
    }
    if ((runMeta?.hasFetchedFiles || savedEv.pdfPath) && stage !== "read" && stage !== "identity_checked") {
      stage = "file_obtained";
    }
    if (p.read === true) {
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
    const zMatch = baseZMatch
      ? {
          ...baseZMatch,
          createdByPlugin: Boolean(baseZMatch.createdByPlugin || savedEv.zoteroMatch?.createdByPlugin),
          attachmentKey: baseZMatch.attachmentKey || savedEv.zoteroMatch?.attachmentKey || null
        }
      : null;
    const localPdf = savedEv.pdfPath && existsSync(savedEv.pdfPath) ? savedEv.pdfPath : p.local_pdf_path || null;
    const candidatePdfUrls = extractDownloadablePdfUrls(p);

    return {
      ...p,
      journal: unpacked.journal,
      venue: unpacked.venue,
      journal_info: unpacked.journal_info,
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
    const authorPart = String(flatItem.publicationInfo).split(" - ")[0] || "";
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
    const parts = String(flatItem.publicationInfo).split(" - ");
    venue = parts[1] || parts[0] || "Google Scholar";
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
    abstract: flatItem.snippet || flatItem.text || "",
    abstract_available: Boolean(flatItem.snippet || flatItem.text),
    url: flatItem.link || flatItem.url || "",
    pdfUrl: flatItem.pdfUrl || null,
    oa_locations: oaLocations,
    evidence_level: "abstract",
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
    enrichedItem.candidate_paper = parseRawItemToCandidatePaper(enrichedItem, idx, state);
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
  return {
    modeLabel: cmd,
    title: `检索记录 (${cmd})`,
    subtitle: `${paperCount || resultCount} 条记录`
  };
}

function loadRunBundle(runIdOrPath, zoteroMatches = {}) {
  const runDir = isAbsolutePath(runIdOrPath) ? runIdOrPath : join(RUNS_DIR, basename(runIdOrPath));
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
      if (entry.isDirectory()) {
        walkFiles(full, rel);
      } else {
        const st = statSync(full);
        const ext = extname(entry.name).toLowerCase();
        const isDeliverable =
          !SYSTEM_JSON_NAMES.has(entry.name) &&
          (ext === ".pdf" ||
            ext === ".xml" ||
            ext === ".zip" ||
            ext === ".docx" ||
            ext === ".xlsx" ||
            ext === ".png" ||
            ext === ".jpg" ||
            rel.startsWith("supplementary/") ||
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

  const hasFetchedFiles = Boolean(fulltextSummary || supplManifest || acquisitionJson);
  const state = loadState();
  const papers = enrichPapersList(papersRaw, state, zoteroMatches, { hasFetchedFiles });
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
  const risBlocks = [];
  const gbtLines = [];
  const apaLines = [];

  (papers || []).forEach((p, idx) => {
    const key = formatBibtexKey(p, idx + 1);
    const authors = (p.authors || []).map((a) => a.name).filter(Boolean);
    const bibAuthors = authors.join(" and ") || "Unknown";
    const doi = p.identifiers?.doi?.[0] || "";
    const journal = p.journal || p.venue || "";
    const year = p.year || "";
    const title = p.title || "Untitled";
    const url = p.clickable_links?.[0]?.url || (doi ? `https://doi.org/${doi}` : p.url || "");

    bibtexBlocks.push(
      [
        `@article{${key},`,
        `  title = {${title}},`,
        `  author = {${bibAuthors}},`,
        year ? `  year = {${year}},` : null,
        journal ? `  journal = {${journal}},` : null,
        doi ? `  doi = {${doi}},` : null,
        url ? `  url = {${url}}` : null,
        `}`
      ]
        .filter(Boolean)
        .join("\n")
    );

    risBlocks.push(
      [
        "TY  - JOUR",
        `TI  - ${title}`,
        ...authors.map((a) => `AU  - ${a}`),
        year ? `PY  - ${year}` : null,
        journal ? `JO  - ${journal}` : null,
        doi ? `DO  - ${doi}` : null,
        url ? `UR  - ${url}` : null,
        p.abstract ? `AB  - ${p.abstract}` : null,
        "ER  - "
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
    ris: risBlocks.join("\n\n"),
    gbt7714: gbtLines.join("\n"),
    apa: apaLines.join("\n\n")
  };
}

function generateMarkdownCard(paper) {
  const doi = paper.identifiers?.doi?.[0] || "";
  const authors = (paper.authors || []).map((a) => a.name).filter(Boolean);
  const providers = (paper.provenance || []).map((pr) => pr.provider).filter(Boolean);
  const linksMd = (paper.clickable_links || [])
    .map((l) => `- [${l.label}](${l.url})`)
    .join("\n");
  const stageCode = paper.handoff_stage || "discovered";
  const stageLabel = STAGE_LABELS[stageCode] || stageCode;

  return [
    "---",
    `title: ${JSON.stringify(paper.title || "Untitled")}`,
    `year: ${paper.year || "null"}`,
    `journal: ${JSON.stringify(paper.journal || paper.venue || "")}`,
    `doi: ${JSON.stringify(doi)}`,
    `authors: ${JSON.stringify(authors)}`,
    `citation_count: ${paper.citation_count ?? "null"}`,
    `evidence_stage: ${JSON.stringify(stageCode)}`,
    `sources: ${JSON.stringify([...new Set(providers)])}`,
    `created_at: ${JSON.stringify(new Date().toISOString().slice(0, 10))}`,
    `tags: ["文献候选", "sanmu-paper-search"]`,
    "---",
    "",
    `# ${paper.title || "Untitled"}`,
    "",
    "## 1. 核心题录与可点击外链",
    "",
    `- **年份**：${paper.year ?? "未知"}`,
    `- **期刊/来源**：${paper.journal || paper.venue || "未标注"}`,
    `- **作者**：${authors.join(", ") || "未知"}`,
    `- **DOI**：${doi ? `[${doi}](https://doi.org/${doi})` : "无"}`,
    `- **被引次数**：${paper.citation_count ?? "未统计"}（参考文献数：${paper.reference_count ?? "未统计"}）`,
    `- **阅读进度**：${stageLabel}（\`${stageCode}\`）`,
    `- **Zotero 本地状态**：${paper.zotero_match?.matched ? `已收录 (Key: \`${paper.zotero_match.itemKey || "local"}\`)` : "未检出本地条目"}`,
    "",
    "### 直达链接",
    linksMd || "- 暂无外链",
    "",
    "## 2. 一句话要点 (TLDR) 与摘要",
    "",
    paper.tldr ? `> **智能要点 (TLDR)**：${paper.tldr}\n` : "",
    paper.abstract || "*当前来源未返回摘要文本。*",
    "",
    "## 3. 筛选备注与本地全文",
    "",
    `- **筛选/阅读备注**：${paper.screening_reason || "待填写与课题相关性"}`,
    `- **本地全文路径**：${paper.local_pdf_path ? `\`${paper.local_pdf_path}\`` : "尚未下载 PDF"}`,
    "",
    "## 4. 精读与实验条件核对记录（配合 `sanmu-paper-reader`）",
    "",
    "- **研究体系 / 关键材料组成**：",
    "- **关键反应/测试条件与性能指标**：",
    "- **核心表征证据与补充材料 (SI) 核查**：",
    ""
  ]
    .filter((line) => line !== null)
    .join("\n");
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
      lines.push(
        `${p.index}. **${titleMd}** (${p.year || "n.d."}) — ${authorsStr || "Unknown"} | 进度: ${p.handoff_stage_label || p.handoff_stage}${doi ? ` | DOI: [${doi}](https://doi.org/${doi})` : ""}`
      );
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
        if (Array.isArray(found) && found[0]) {
          bestPaper = found[0];
        }
        rmSync(tempOut, { recursive: true, force: true });
      }
      if ((!bestPaper || !bestPaper.abstract) && allSrcs.includes("semantic-scholar")) {
        await runPython(PAPER_SEARCH_PY, ["lookup", seedId, "--sources", "semantic-scholar", "--out", tempOut], 35000);
        const s2Found = readJsonSafe(join(tempOut, "papers.json"), []);
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
        const normT = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
        const targetT = normT(titleQuery);
        const match = epmcFound.find((ep) => !targetT || normT(ep.title) === targetT) || epmcFound[0];
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
        const normT = (s) => String(s || "").toLowerCase().replace(/[^\w\u4e00-\u9fa5]+/g, "");
        const targetT = normT(titleQuery);
        const match = srchFound.find((sp) => normT(sp.title) === targetT) || srchFound[0];
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
    const normKey = normalizeGraphNodeId(rawKey);
    const existingId =
      aliasToNodeId.get(normKey) ||
      (paper.identifiers?.doi?.[0] ? aliasToNodeId.get(`doi:${String(paper.identifiers.doi[0]).toLowerCase()}`) : null) ||
      normKey;

    const firstAuthor = paper.authors?.[0]?.name || "Unknown";
    const shortAuthor = firstAuthor.split(/\s+/).pop() || firstAuthor.slice(0, 8);
    const shortLabel = `${shortAuthor}${paper.year ? ` (${paper.year})` : ""}`;

    if (nodeMap.has(existingId)) {
      const existing = nodeMap.get(existingId);
      if (priority > existing.priority) {
        existing.role = role;
        existing.priority = priority;
      }
      if (!existing.paper?.abstract && paper.abstract) existing.paper = paper;
      if (paper.in_basket) existing.inBasket = true;
      if (paper.zotero_match?.matched) existing.inZotero = true;
      if (paper.local_pdf_path) existing.hasPdf = true;
      registerAliases(existingId, paper);
      return existingId;
    }

    if (nodeMap.size >= 80) return null;

    const node = {
      id: existingId,
      paperKey: rawKey,
      seedId: paper.resolvable_seed_id || paper.identifiers?.doi?.[0] || "",
      title: paper.title || existingId,
      shortLabel,
      year: paper.year || null,
      journal: paper.journal || paper.venue || "",
      firstAuthor,
      citationCount: paper.citation_count ?? 0,
      role,
      priority,
      inBasket: Boolean(paper.in_basket),
      inZotero: Boolean(paper.zotero_match?.matched || zoteroMatches[rawKey]?.matched),
      hasPdf: Boolean(paper.local_pdf_path),
      paper
    };
    nodeMap.set(existingId, node);
    registerAliases(existingId, paper);
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
  for (const rMeta of (recentRuns || []).slice(0, 12)) {
    if (rMeta.runId === activeBundle?.runId) continue;
    if (rMeta.action !== "related") continue;
    const qId = rMeta.query?.id;
    if (!qId) continue;
    const normSeed = normalizeGraphNodeId(qId);
    const matchedSeedNodeId = aliasToNodeId.get(normSeed);
    if (!matchedSeedNodeId && nodeMap.size > 0) continue;

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
    const rPapers = enrichPapersList(rPapersRaw.slice(0, 10), state, zoteroMatches);
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
        zoteroUrl: { type: "string" },
        exportSubdir: { type: "string" },
        defaultSources: { type: "array", items: { type: "string" } },
        defaultLimit: { type: "integer", minimum: 1, maximum: 20 },
        clearKeys: { type: "array", items: { type: "string" } }
      }
    },
    execute: async (input) => {
      savePanelConfig(input || {});
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

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Search failed");
      }
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
      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Lookup failed");
      }
      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }
      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
      const subRun = input.preserveActiveRun ? loadRunBundle(runId) : null;
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
      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Related exploration failed");
      }
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }

      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
      const subRun = input.preserveActiveRun ? loadRunBundle(runId) : null;
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

      await runPython(PAPER_SEARCH_PY, args);

      // If Semantic Scholar author lookup hit rate limit (429) or failed, automatically fall back to OpenAlex!
      const firstRunJson = readJsonSafe(join(outDir, "run.json"), null);
      const firstPapers = readJsonSafe(join(outDir, "papers.json"), []);
      const s2Failed =
        (input.provider === "semantic-scholar") &&
        (!firstRunJson ||
          firstRunJson.status === "failed" ||
          (firstPapers.length === 0 &&
            (firstRunJson.author_candidates || []).length === 0 &&
            firstRunJson.source_status?.["semantic-scholar"]?.status === "error"));

      if (s2Failed && (input.query || input.fromPaper)) {
        try {
          rmSync(outDir, { recursive: true, force: true });
        } catch {}
        runId = makeRunId("author-oa");
        outDir = join(RUNS_DIR, runId);
        const fbArgs = ["author"];
        if (input.query && String(input.query).trim()) fbArgs.push(String(input.query).trim());
        fbArgs.push("--provider", "openalex", "--limit", String(input.limit || 10), "--out", outDir);
        if (input.fromPaper && String(input.fromPaper).trim()) {
          fbArgs.push("--from-paper", String(input.fromPaper).trim());
        }
        if (input.yearFrom && input.fromPaper) fbArgs.push("--year-from", String(input.yearFrom));
        if (input.yearTo && input.fromPaper) fbArgs.push("--year-to", String(input.yearTo));
        await runPython(PAPER_SEARCH_PY, fbArgs);
      }

      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error("Author query failed");
      }
      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }

      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
      const subRun = input.preserveActiveRun ? loadRunBundle(runId) : null;
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
      "调用 Europe PMC 公开接口检索论文、预印本、撤稿/勘误记录，或一键下载 JATS 全文 XML 与补充材料（SI PDF 及高清原图压缩包）到指定保存目录。无需 API Key。",
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
          description: "是否同时拉取 JATS 全文 XML 与补充材料包 (all/xml/suppl)"
        },
        saveDir: {
          type: "string",
          description: "可选。当下载全文 XML 或 SI 补充材料包时，同步保存到的目标文件夹路径（支持工作区相对路径或本地绝对路径）"
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

      const pyRes = await runPython(PAPER_SEARCH_PY, args, 240000);
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Europe PMC query failed");
      }

      // If XML or supplementary files were downloaded, copy them to user's chosen saveDir!
      let exportedSaveDir = null;
      if (fetchMode !== "none") {
        const hasXml = existsSync(join(outDir, "fulltext.xml"));
        const hasSuppl = existsSync(join(outDir, "supplementary"));
        if (hasXml || hasSuppl) {
          const baseSaveDir = resolveTargetSaveDir(input.saveDir);
          const papers = readJsonSafe(join(outDir, "papers.json"), []);
          const firstPaper = papers[0] || {};
          const folderName = `${makeSafeFileStem(firstPaper.year, firstPaper.title, input.id || input.query || runId)}_EPMC`;
          exportedSaveDir = join(baseSaveDir, folderName);
          mkdirSync(exportedSaveDir, { recursive: true });
          if (hasXml) {
            copyFileSync(join(outDir, "fulltext.xml"), join(exportedSaveDir, "fulltext.xml"));
          }
          if (hasSuppl) {
            cpSync(join(outDir, "supplementary"), join(exportedSaveDir, "supplementary"), { recursive: true });
          }
          const runJsonPath = join(outDir, "run.json");
          const runJson = readJsonSafe(runJsonPath, {});
          runJson.exported_save_dir = exportedSaveDir;
          writeJsonSafe(runJsonPath, runJson);
        }
      }

      const state = loadState();
      recordMainRunNavigation(state, runId);
      saveState(state);

      const panelState = await getFullPanelState(runId);
      return {
        text:
          summarizeRunForModel(panelState.activeRun) +
          (exportedSaveDir ? `\n\n✅ 全文 XML / SI 补充材料已自动保存至目录：\`${exportedSaveDir}\`` : ""),
        structured: { ...panelState, exportedSaveDir }
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
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Exa search failed");
      }
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
        query: { type: "string", description: "检索词（webpage/lens 模式下可为空并传 url）" },
        type: {
          type: "string",
          enum: [
            "scholar",
            "patents",
            "search",
            "webpage",
            "news",
            "images",
            "videos",
            "places",
            "maps",
            "reviews",
            "shopping",
            "lens",
            "autocomplete"
          ],
          default: "scholar"
        },
        url: { type: "string", description: "当 type 为 webpage 或 lens 时的目标公开网页 URL" },
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
      if (input.page) args.push("--page", String(input.page));
      if (mode === "scholar" && input.yearFrom) args.push("--year-from", String(input.yearFrom));
      if (mode === "scholar" && input.yearTo) args.push("--year-to", String(input.yearTo));

      const pyRes = await runPython(PAPER_SEARCH_PY, args);
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Serper search failed");
      }
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
        if (!input.pdfUrl) throw new Error("pdfUrl is required for download_pdf");
        const runId = makeRunId("pdf");
        const outDir = join(RUNS_DIR, runId);
        const args = [String(input.pdfUrl).trim(), "--out", outDir];
        if (input.doi) args.push("--doi", String(input.doi).trim());
        if (input.title) args.push("--title", String(input.title).trim());
        if (Array.isArray(input.fallbackUrls)) {
          for (const fb of input.fallbackUrls) {
            if (fb && String(fb).trim() && String(fb).trim() !== String(input.pdfUrl).trim()) {
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
        const fileStem = makeSafeFileStem(input.year, input.title, input.doi || runId);
        const savedPdfPath = join(destDir, `${fileStem}.pdf`);
        copyFileSync(join(outDir, "paper.pdf"), savedPdfPath);

        const acqPath = join(outDir, "acquisition.json");
        const acq = readJsonSafe(acqPath, {});
        acq.saved_pdf_path = savedPdfPath;
        acq.saved_dir = destDir;
        writeJsonSafe(acqPath, acq);

        const state = loadState();
        const key = input.paperKey || (input.doi ? `doi:${String(input.doi).toLowerCase()}` : null);
        if (key) {
          const prev = state.evidenceMap[key] || {};
          state.evidenceMap[key] = {
            ...prev,
            stage: acq.identity?.status === "matched" ? "identity_checked" : "file_obtained",
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
          text: `PDF 下载并核验成功，已保存至：\`${savedPdfPath}\`\n- 首屏核验: \`${acq.identity?.status || "file_obtained"}\``,
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
      if (!existsSync(join(outDir, "run.json"))) {
        throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "OA resolution failed");
      }
      const state = loadState();
      if (!input.preserveActiveRun) {
        recordMainRunNavigation(state, runId);
        saveState(state);
      }
      const panelState = await getFullPanelState(input.preserveActiveRun ? state.lastRunId : runId);
      const subRun = input.preserveActiveRun ? loadRunBundle(runId) : null;
      return {
        text: summarizeRunForModel(subRun || panelState.activeRun),
        structured: subRun ? { ...panelState, subRun } : panelState
      };
    }
  },
  {
    name: "manage_runs",
    description:
      "管理与操作历史检索批次：支持切换查看指定批次（load）、删除指定历史记录（delete）、一键清空全部历史记录（clear_all）、多批次合并去重（merge）、前后批次增量对比（diff）以及 SHA-256 校验（check）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        operation: {
          type: "string",
          enum: ["load", "delete", "clear_all", "merge", "diff", "check"],
          default: "load"
        },
        runId: { type: "string", description: "当 operation=load / delete / check 时的批次 ID" },
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
          recordMainRunNavigation(state, basename(input.runId));
          saveState(state);
        }
        const panelState = await getFullPanelState(input.runId);
        return {
          text: panelState.activeRun ? summarizeRunForModel(panelState.activeRun) : "未找到指定批次。",
          structured: panelState
        };
      }
      if (op === "delete") {
        const targets = [
          ...(input.runId ? [input.runId] : []),
          ...(Array.isArray(input.runIds) ? input.runIds : [])
        ]
          .map((id) => basename(String(id || "").trim()))
          .filter(Boolean);
        let deletedCount = 0;
        for (const id of targets) {
          const dir = join(RUNS_DIR, id);
          const rel = relative(RUNS_DIR, dir);
          if (!rel.startsWith("..") && !isAbsolutePath(rel) && existsSync(dir)) {
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
      if (op === "check") {
        const targetDir = join(RUNS_DIR, basename(input.runId || ""));
        const pyRes = await runPython(PAPER_SEARCH_PY, ["check", targetDir, "--include-metadata"]);
        const panelState = await getFullPanelState(input.runId);
        return {
          text: `批次 \`${input.runId}\` 哈希完整性校验结果：\`${pyRes.parsed?.status || "unknown"}\``,
          structured: { ...panelState, checkResult: pyRes.parsed }
        };
      }
      if (op === "merge") {
        const ids = (input.runIds || []).map((id) => join(RUNS_DIR, basename(id)));
        if (ids.length < 1) throw new Error("请至少选择一条记录进行合并");
        const runId = makeRunId("merged");
        const outDir = join(RUNS_DIR, runId);
        const args = ["merge", ...ids, "--out", outDir];
        if (input.zoteroJsonPath) args.push("--zotero", input.zoteroJsonPath);
        const pyRes = await runPython(PAPER_SEARCH_PY, args);
        if (!existsSync(join(outDir, "run.json"))) {
          throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Merge failed");
        }
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
          join(RUNS_DIR, basename(input.beforeRunId)),
          join(RUNS_DIR, basename(input.afterRunId)),
          "--out",
          outDir
        ];
        const pyRes = await runPython(PAPER_SEARCH_PY, args);
        if (!existsSync(join(outDir, "run.json"))) {
          throw new Error(pyRes.parsed?.error?.message || pyRes.stderr || "Diff failed");
        }
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
        const runDir = join(RUNS_DIR, basename(input.runId));
        target = rawRel === "." ? runDir : resolve(runDir, rawRel);
      } else {
        target = resolve(WORKSPACE_ROOT, rawRel);
      }

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
        paperKey: { type: "string", description: "当 action=remove / update_evidence / undo_zotero_save 时的 paper_key" },
        collectionKey: { type: "string", description: "可选。当 action=save_to_zotero 时指定存入的 Zotero 分类 Key" },
        zoteroItemKey: {
          type: "string",
          description: "当 action=open_in_zotero 或 undo_zotero_save 时指定的 Zotero 条目 Key"
        },
        stage: {
          type: "string",
          enum: ["discovered", "metadata_checked", "file_obtained", "identity_checked", "read"]
        },
        basis: { type: "string", description: "筛选依据层级，如 metadata_only、abstract、fulltext" },
        reason: { type: "string", description: "入选/排除理由或阅读备注" }
      },
      required: ["action"]
    },
    execute: async (input) => {
      const state = loadState();
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
        const prev = state.evidenceMap[key] || {};
        state.evidenceMap[key] = {
          ...prev,
          zoteroUndoneKey: null,
          zoteroUndoneAt: null,
          zoteroMatch: {
            matched: true,
            itemKey: zRes.itemKey,
            attachmentKey: zRes.attachmentKey || null,
            createdByPlugin: !zRes.alreadyExists,
            selectUri: zRes.selectUri,
            collections: zRes.collections || []
          },
          updatedAt: new Date().toISOString()
        };
        if (!zRes.alreadyExists) {
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
        const pKey =
          input.paperKey ||
          state.lastZoteroImport?.paperKey ||
          Object.keys(state.evidenceMap || {}).find(
            (k) => state.evidenceMap[k]?.zoteroMatch?.itemKey === targetKey
          ) ||
          null;
        const attKey =
          (pKey && state.evidenceMap[pKey]?.zoteroMatch?.attachmentKey) ||
          state.lastZoteroImport?.attachmentKey ||
          null;
        if (!targetKey) {
          throw new Error("未找到可撤销的 Zotero 导入记录（请传入 zoteroItemKey 或先执行一次存入 Zotero）。");
        }
        const delRes = await deletePaperFromLocalZotero(targetKey, attKey);
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
      if (act === "add" && input.paper) {
        const key = input.paper.paper_key || computePaperKey(input.paper);
        const exists = state.basket.some((p) => (p.paper_key || computePaperKey(p)) === key);
        if (!exists) {
          state.basket.push({ ...input.paper, paper_key: key });
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
    name: "export_to_workspace",
    description:
      "一键将单篇论文、文献收藏篮、关系图谱矢量图或完整检索批次（含已下载的 PDF、EPMC 全文 XML 与 SI 补充材料包）导出到指定的目标文件夹（支持工作区相对路径或本地任意绝对路径）。",
    visibility: ["model", "app"],
    inputSchema: {
      $schema: "http://json-schema.org/draft-07/schema#",
      type: "object",
      properties: {
        exportType: {
          type: "string",
          enum: ["markdown_cards", "citations_bundle", "run_bundle", "graph_svg"],
          default: "markdown_cards"
        },
        targetSubdir: {
          type: "string",
          description: "可选。目标保存文件夹路径（支持工作区相对路径或本地绝对路径，默认使用配置的 00-收件箱-待整理/文献检索）"
        },
        papers: {
          type: "array",
          items: { type: "object" },
          description: "可选。指定要导出的论文数组；未传时默认导出文献收藏篮中的全部论文"
        },
        runId: {
          type: "string",
          description: "当 exportType=run_bundle 时，指定要完整导出的运行批次 ID"
        },
        svgContent: {
          type: "string",
          description: "当 exportType=graph_svg 时，要保存的 SVG 矢量图内容"
        }
      },
      required: ["exportType"]
    },
    execute: async (input) => {
      const destRoot = resolveTargetSaveDir(input.targetSubdir);
      const state = loadState();
      const writtenFiles = [];

      if (input.exportType === "graph_svg") {
        if (!input.svgContent) throw new Error("未提供图谱 SVG 内容");
        const stamp = new Date().toISOString().replace(/[:.]/g, "-").slice(0, 19);
        const svgPath = join(destRoot, `literature-graph-${stamp}.svg`);
        writeFileSync(svgPath, String(input.svgContent), "utf8");
        writtenFiles.push(svgPath);
      } else if (input.exportType === "run_bundle") {
        const targetRunId = basename(input.runId || state.lastRunId || "");
        if (!targetRunId) throw new Error("未指定要导出的批次 ID");
        const srcRunDir = join(RUNS_DIR, targetRunId);
        if (!existsSync(srcRunDir)) throw new Error(`未找到批次目录: ${targetRunId}`);
        const dstRunDir = join(destRoot, targetRunId);
        mkdirSync(dirname(dstRunDir), { recursive: true });
        cpSync(srcRunDir, dstRunDir, { recursive: true });
        writtenFiles.push(dstRunDir);
      } else {
        const targetPapers =
          Array.isArray(input.papers) && input.papers.length
            ? enrichPapersList(input.papers, state)
            : enrichPapersList(state.basket || [], state);
        if (targetPapers.length === 0) {
          throw new Error("没有可导出的论文，请先在检索结果中点击导出或将论文加入收藏篮。");
        }
        if (input.exportType === "markdown_cards") {
          for (const p of targetPapers) {
            const stem = makeSafeFileStem(p.year, p.title, "paper");
            const fullPath = join(destRoot, `${stem}.md`);
            writeFileSync(fullPath, generateMarkdownCard(p), "utf8");
            writtenFiles.push(fullPath);
          }
        } else if (input.exportType === "citations_bundle") {
          const cites = generateCitations(targetPapers);
          const stamp = new Date().toISOString().slice(0, 10);
          const bibPath = join(destRoot, `references-${stamp}.bib`);
          const risPath = join(destRoot, `references-${stamp}.ris`);
          const mdPath = join(destRoot, `citations-${stamp}.md`);
          writeFileSync(bibPath, cites.bibtex + "\n", "utf8");
          writeFileSync(risPath, cites.ris + "\n", "utf8");
          writeFileSync(
            mdPath,
            `# 导出参考文献清单 (${stamp})\n\n## GB/T 7714\n\n${cites.gbt7714}\n\n## APA\n\n${cites.apa}\n`,
            "utf8"
          );
          writtenFiles.push(bibPath, risPath, mdPath);
        }
      }

      const panelState = await getFullPanelState();
      return {
        text: `已成功导出到目录 \`${destRoot}\`：\n${writtenFiles.map((f) => `- [${basename(f)}](${f})`).join("\n")}`,
        structured: {
          ...panelState,
          exportResult: {
            destRoot,
            writtenFiles
          }
        }
      };
    }
  }
];

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

async function handleRequest(message) {
  const { id, method, params } = message;
  if (method === "initialize") {
    return {
      jsonrpc: "2.0",
      id,
      result: {
        protocolVersion: params?.protocolVersion || "2024-11-05",
        capabilities: {
          tools: { listChanged: true },
          resources: { listChanged: true }
        },
        serverInfo: {
          name: "sanmu-paper-search",
          version: "1.6.3"
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
      const out = await tool.execute(params?.arguments || {});
      return {
        jsonrpc: "2.0",
        id,
        result: {
          content: [{ type: "text", text: out.text || "OK" }],
          structuredContent: out.structured || {}
        }
      };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
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
  return {
    jsonrpc: "2.0",
    id,
    error: { code: -32601, message: `Method not found: ${method}` }
  };
}

for await (const line of createInterface({ input: process.stdin })) {
  const trimmed = line.trim();
  if (!trimmed) continue;
  let message;
  try {
    message = JSON.parse(trimmed);
  } catch {
    continue;
  }
  if (message.id === undefined) continue;
  const response = await handleRequest(message);
  process.stdout.write(JSON.stringify(response) + "\n");
}
