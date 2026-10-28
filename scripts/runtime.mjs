import { spawn, spawnSync } from "node:child_process";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, readFileSync, realpathSync, statSync } from "node:fs";
import { mkdir, readFile, readdir, rename, rm, rmdir, unlink, writeFile } from "node:fs/promises";
import { homedir, hostname } from "node:os";
import { basename, delimiter, dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const DEFAULT_ROOT = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const MIN_PYTHON = [3, 10];
const PYTHON_CACHE = new Map();
const MODULES = {
  requests: "requests", pymupdf: "fitz", pillow: "PIL", "python-pptx": "pptx",
  markdown: "markdown", "markdown-it-py": "markdown_it", "mdit-py-plugins": "mdit_py_plugins",
  beautifulsoup4: "bs4", pyyaml: "yaml",
};
const CREDENTIALS = [
  [["politeEmail"], "POLITE_EMAIL", ["POLITE_EMAIL", "OPENALEX_EMAIL", "CROSSREF_EMAIL", "UNPAYWALL_EMAIL"]],
  [["openalexApiKey"], "OPENALEX_API_KEY", ["OPENALEX_API_KEY"]],
  [["s2ApiKey"], "S2_API_KEY", ["S2_API_KEY", "SEMANTIC_SCHOLAR_API_KEY"]],
  [["exaApiKey"], "EXA_API_KEY", ["EXA_API_KEY"]],
  [["serperApiKey"], "SERPER_API_KEY", ["SERPER_API_KEY"]],
  [["easyscholarSecretKey"], "EASYSCHOLAR_SECRET_KEY", ["EASYSCHOLAR_SECRET_KEY"]],
  [["ai4scholarApiKey"], "AI4SCHOLAR_API_KEY", ["AI4SCHOLAR_API_KEY"]],
  [["sciverseApiKey"], "SCIVERSE_API_KEY", ["SCIVERSE_API_KEY", "SCIVERSE_API_TOKEN"]],
  [["mineruApiToken", "mineruApiKey"], "MINERU_API_TOKEN", ["MINERU_API_TOKEN"]],
];

// Hosts sometimes pass unexpanded manifest placeholders. They are not paths or credentials.
function value(text) {
  if (typeof text !== "string") return "";
  const trimmed = text.trim();
  return /\$\{[^}]+\}|\$[A-Za-z_][\w]*|%[A-Za-z_][\w]*%|\{\{[^}]+\}\}/.test(trimmed) ? "" : trimmed;
}

function readJson(path, fallback = null) {
  try { return JSON.parse(readFileSync(path, "utf8")); } catch { return fallback; }
}

export function resolveRuntimePaths(env = process.env, defaultRoot = DEFAULT_ROOT) {
  const pluginRoot = resolve(value(env.SANMU_PLUGIN_ROOT) || value(env.CLAUDE_PLUGIN_ROOT) || value(env.PLUGIN_ROOT) || defaultRoot);
  const workspaceRoot = resolve(value(env.SANMU_WORKSPACE_ROOT) || value(env.ZCODE_WORKSPACE_ROOT) ||
    value(env.ZCODE_PROJECT_DIR) || value(env.DSH_WORKSPACE_ROOT) || value(env.CLAUDE_PROJECT_DIR) || process.cwd());
  const pluginData = resolve(value(env.SANMU_DATA_DIR) || value(env.SANMU_PLUGIN_DATA) || value(env.ZCODE_PLUGIN_DATA) ||
    value(env.DSH_PLUGIN_DATA) || value(env.CLAUDE_PLUGIN_DATA) || value(env.PLUGIN_DATA) ||
    (value(env.DSH_HOME) ? join(value(env.DSH_HOME), "plugin-data", "sanmu-paper-search") :
      join(homedir(), ".sanmu", "plugin-data", "sanmu-paper-search")));
  const pythonDir = join(pluginData, "python");
  const nodeDir = join(pluginData, "doc2md-node");
  return { pluginRoot, workspaceRoot, pluginData, dataDir: pluginData, pythonDir, nodeDir, nodeModulesDir: join(nodeDir, "node_modules") };
}

function optionPaths({ root, dataDir, env = process.env } = {}) {
  return resolveRuntimePaths({ ...env, ...(root ? { SANMU_PLUGIN_ROOT: root } : {}), ...(dataDir ? { SANMU_DATA_DIR: dataDir } : {}) });
}

export function resolveConfigEnvironment({ env = process.env, dataDir, root } = {}) {
  const paths = optionPaths({ root, dataDir, env });
  const config = readJson(join(paths.dataDir, "config.json"), {});
  const result = { ...env };
  for (const [fields, name, aliases] of CREDENTIALS) {
    const configured = fields.map(field => value(config?.[field])).find(Boolean) || value(env[`USER_CONFIG_${name}`]);
    let resolved = configured || aliases.map(alias => value(env[alias])).find(Boolean) || "";
    if (name === "EASYSCHOLAR_SECRET_KEY") resolved = resolved.replace(/\s+/g, "");
    if (!resolved) continue;
    for (const alias of aliases) {
      // Existing provider-specific mail addresses retain MCP's environment-only precedence.
      if (name === "POLITE_EMAIL" && !configured && alias !== "POLITE_EMAIL" && value(env[alias])) continue;
      result[alias] = resolved;
    }
  }
  if (value(result.AI4SCHOLAR_API_KEY) && !value(result.S2_API_KEY)) result.AI4SCHOLAR_S2_PROXY = "1";
  return result;
}

function managedPython(paths) {
  return join(paths.pythonDir, process.platform === "win32" ? "Scripts" : "bin", process.platform === "win32" ? "python.exe" : "python");
}

function pathValue(env) {
  return env.PATH ?? env.Path ?? env.path ?? "";
}

function runtimeEnvironment(env, paths, venv = false) {
  const result = {
    ...env, SANMU_PLUGIN_ROOT: paths.pluginRoot, SANMU_WORKSPACE_ROOT: paths.workspaceRoot,
    SANMU_DATA_DIR: paths.dataDir, SANMU_PLUGIN_DATA: paths.dataDir,
    PYTHONIOENCODING: "utf-8", PYTHONUTF8: "1", PYTHONDONTWRITEBYTECODE: "1", PYTHONNOUSERSITE: "1",
    EASYSCHOLAR_CACHE_PATH: join(paths.dataDir, "easyscholar-cache.json"),
  };
  const nodePaths = [paths.nodeModulesDir, ...(env.NODE_PATH || "").split(delimiter).filter(Boolean)];
  result.NODE_PATH = [...new Set(nodePaths)].join(delimiter);
  if (venv) {
    delete result.PYTHONHOME;
    delete result.PYTHONPATH;
    for (const name of Object.keys(result)) if (name.toUpperCase() === "PATH") delete result[name];
    result.PATH = [dirname(managedPython(paths)), pathValue(env)].filter(Boolean).join(delimiter);
    result.VIRTUAL_ENV = paths.pythonDir;
  }
  return result;
}

const VERSION_PROBE = "import json,sys; print(json.dumps({'version':sys.version.split()[0],'executable':sys.executable}))";
function fileSignature(path) {
  try { const s = statSync(path); return `${s.ino}:${s.size}:${s.mtimeMs}:${s.ctimeMs}`; } catch { return "missing"; }
}

function probeVersion(candidate, env, probe = spawnSync) {
  try {
    const result = probe(candidate.cmd, [...candidate.prefixArgs, "-c", VERSION_PROBE], {
      env, shell: false, windowsHide: true, encoding: "utf8", timeout: 5000, maxBuffer: 1024 * 1024,
    });
    if ((result.status ?? result.code) !== 0 || result.error) return { ...candidate, version: null, supported: false };
    const text = String(result.stdout || "").trim();
    let details;
    try { details = JSON.parse(text); } catch { details = { version: text }; }
    const version = typeof details.version === "string" ? details.version : "";
    const numbers = version.match(/^(\d+)\.(\d+)(?:\.(\d+))?/);
    const supported = Boolean(numbers && (Number(numbers[1]) > MIN_PYTHON[0] ||
      Number(numbers[1]) === MIN_PYTHON[0] && Number(numbers[2]) >= MIN_PYTHON[1]));
    return { ...candidate, version: numbers ? version : null, supported };
  } catch { return { ...candidate, version: null, supported: false }; }
}

function systemCandidates(env) {
  const specified = value(env.SANMU_PYTHON);
  if (specified) return [{ cmd: specified, prefixArgs: /^py(?:\.exe)?$/i.test(basename(specified)) ? ["-3"] : [] }];
  return process.platform === "win32" ? [
    { cmd: "python", prefixArgs: [] }, { cmd: "py", prefixArgs: ["-3"] }, { cmd: "python3", prefixArgs: [] },
  ] : [{ cmd: "python3", prefixArgs: [] }, { cmd: "python", prefixArgs: [] }];
}

function cacheKey(env, paths, systemOnly) {
  const executables = systemCandidates(env).map(c => c.cmd);
  return JSON.stringify([systemOnly, env.SANMU_PYTHON, paths.dataDir, pathValue(env), env.PYTHONHOME, env.PYTHONPATH,
    env.PY_PYTHON, env.PY_PYTHON3, ...executables.map(fileSignature), fileSignature(managedPython(paths)),
    fileSignature(join(paths.pythonDir, "pyvenv.cfg"))]);
}

/** Synchronous and offline. An explicit SANMU_PYTHON never falls back to another interpreter. */
export function resolvePythonRuntime({ env = process.env, dataDir, root, probe = spawnSync, systemOnly = false } = {}) {
  const paths = optionPaths({ root, dataDir, env });
  const key = cacheKey(env, paths, systemOnly);
  let result = probe === spawnSync ? PYTHON_CACHE.get(key) : null;
  if (!result) {
    const candidates = systemCandidates(env);
    if (!systemOnly && !value(env.SANMU_PYTHON) && existsSync(managedPython(paths))) {
      candidates.unshift({ cmd: managedPython(paths), prefixArgs: [], managed: true });
    }
    result = null;
    for (const candidate of candidates) {
      const checked = probeVersion(candidate, runtimeEnvironment(env, paths, Boolean(candidate.managed)), probe);
      result ||= checked;
      if (checked.supported) { result = checked; break; }
    }
    result ||= { cmd: "python", prefixArgs: [], version: null, supported: false };
    if (!result.supported) result.error = value(env.SANMU_PYTHON) ?
      "SANMU_PYTHON must name an executable Python >=3.10; the specified interpreter is unavailable or unsupported." :
      "Python >=3.10 was not found. Install Python or set SANMU_PYTHON to its executable.";
    if (probe === spawnSync) {
      if (PYTHON_CACHE.size > 64) PYTHON_CACHE.clear();
      PYTHON_CACHE.set(key, result);
    }
  }
  return { ...result, prefixArgs: [...result.prefixArgs], env: runtimeEnvironment(env, paths, Boolean(result.managed)) };
}

function findSkillDirectory(root, skill) {
  const candidates = [join(root, "skills", skill), resolve(root, "..", "..", "..", "skills", skill)];
  return candidates.find(path => existsSync(join(path, "scripts"))) || candidates[0];
}

function requirements(paths) {
  const files = ["sanmu-doc2md", "sanmu-paper-reader"].map(skill => join(findSkillDirectory(paths.pluginRoot, skill), "requirements.txt"));
  const specs = new Map();
  for (const file of files) {
    for (const line of readFileSync(file, "utf8").split(/\r?\n/)) {
      const text = line.replace(/\s*#.*/, "").trim();
      if (!text) continue;
      const match = text.match(/^([A-Za-z0-9_.-]+)\s*>=\s*(\d+(?:\.\d+)*)$/);
      if (!match) throw new Error(`Unsupported runtime requirement in ${file}: ${text}`);
      const name = match[1].toLowerCase().replace(/_/g, "-");
      if (!MODULES[name]) throw new Error(`No runtime import probe for requirement ${match[1]}`);
      const previous = specs.get(name);
      const versionNumber = v => v.split(".").reduce((n, part, i) => n + Number(part) / 1000 ** i, 0);
      if (!previous || versionNumber(match[2]) > versionNumber(previous.minimum)) {
        specs.set(name, { name: match[1], module: MODULES[name], minimum: match[2] });
      }
    }
  }
  const manifest = join(findSkillDirectory(paths.pluginRoot, "sanmu-doc2md"), "scripts", "package.json");
  const katex = readJson(manifest, {}).dependencies?.katex;
  if (typeof katex !== "string" || !/^[~^]?\d+\.\d+\.\d+(?:[-+][\w.-]+)?$/.test(katex)) {
    throw new Error("The doc2md package manifest must declare a KaTeX version.");
  }
  const fingerprint = createHash("sha256").update(JSON.stringify([files.map(file => readFileSync(file, "utf8")), katex])).digest("hex");
  return { files, specs: [...specs.values()], katex, fingerprint };
}

const DEPENDENCY_PROBE = `import importlib,importlib.metadata,json,os,re,sys
specs=json.loads(sys.argv[1]); expected=sys.argv[2]; checks={}
def version(v):
    m=re.match(r'^(\\d+(?:\\.\\d+)*)',v)
    return tuple(map(int,m.group(1).split('.'))) if m else ()
for spec in specs:
    name=spec['name']; installed=None; ok=False
    try:
        installed=importlib.metadata.version(name)
        importlib.import_module(spec['module'])
        ok=version(installed)>=version(spec['minimum'])
    except Exception: pass
    checks[name]={'available':ok,'version':installed,'minimum':spec['minimum']}
isolated=not expected or (os.path.normcase(os.path.realpath(sys.prefix))==os.path.normcase(os.path.realpath(expected)) and sys.prefix!=sys.base_prefix)
print(json.dumps({'isolated':isolated,'dependencies':checks,'ok':isolated and all(c['available'] for c in checks.values())}))`;

function probeDependencies(runtime, specs, expectedDir, probe) {
  if (!runtime.supported) return { ok: false, isolated: false, dependencies: {} };
  try {
    const result = probe(runtime.cmd, [...runtime.prefixArgs, "-I", "-c", DEPENDENCY_PROBE, JSON.stringify(specs), expectedDir || ""], {
      env: runtime.env, shell: false, windowsHide: true, encoding: "utf8", timeout: 20000, maxBuffer: 1024 * 1024,
    });
    if ((result.status ?? result.code) === 0 && !result.error) return JSON.parse(String(result.stdout));
  } catch {}
  return { ok: false, isolated: false, dependencies: {} };
}

const KATEX_PROBE = `const p=require('node:path');const paths=[process.argv[1],...(process.env.NODE_PATH||'').split(p.delimiter).filter(Boolean)];const resolved=require.resolve('katex',{paths});const k=require(resolved);k.renderToString('x^2',{throwOnError:true,trust:false});process.stdout.write(JSON.stringify({version:k.version,path:resolved}));`;
function probeKatex(paths, env, probe, managed = true) {
  try {
    // Managed checks deliberately exclude NODE_PATH, so a global copy cannot satisfy setup.
    const checkedEnv = { ...env, NODE_PATH: managed ? paths.nodeModulesDir : env.NODE_PATH || "" };
    const searchRoot = managed ? paths.nodeDir : join(findSkillDirectory(paths.pluginRoot, "sanmu-doc2md"), "scripts");
    const result = probe(process.execPath, ["-e", KATEX_PROBE, searchRoot], {
      env: checkedEnv, shell: false, windowsHide: true, encoding: "utf8", timeout: 10000, maxBuffer: 1024 * 1024,
    });
    if ((result.status ?? result.code) !== 0 || result.error) return { available: false, version: null };
    const checked = JSON.parse(String(result.stdout));
    const expected = resolve(paths.nodeModulesDir, "katex");
    const located = resolve(checked.path || "");
    const inside = located === expected || located.startsWith(expected + (process.platform === "win32" ? "\\" : "/"));
    return { available: Boolean(checked.version && (!managed || inside)), version: checked.version || null };
  } catch { return { available: false, version: null }; }
}

export function inspectRuntime({ root, dataDir, env = process.env, probe = spawnSync } = {}) {
  const paths = optionPaths({ root, dataDir, env });
  const spec = requirements(paths);
  const system = resolvePythonRuntime({ env, dataDir: paths.dataDir, root: paths.pluginRoot, probe, systemOnly: true });
  const managed = probeVersion({ cmd: managedPython(paths), prefixArgs: [], managed: true }, runtimeEnvironment(env, paths, true), probe);
  managed.env = runtimeEnvironment(env, paths, true);
  const python = probeDependencies(managed, spec.specs, paths.pythonDir, probe);
  const katex = probeKatex(paths, managed.env, probe);
  const configEnv = resolveConfigEnvironment({ env, dataDir: paths.dataDir, root: paths.pluginRoot });
  const config = Object.fromEntries(CREDENTIALS.map(([fields, , aliases]) => [fields[0], aliases.some(alias => Boolean(value(configEnv[alias])))]));
  return {
    paths, spec, system, managed, python, katex, config,
    ready: Boolean(python.ok && katex.available), configFileExists: existsSync(join(paths.dataDir, "config.json")),
  };
}

function alive(pid) {
  if (!Number.isInteger(pid) || pid <= 0) return true;
  try { process.kill(pid, 0); return true; } catch (error) { return error.code !== "ESRCH"; }
}

const delay = ms => new Promise(accept => setTimeout(accept, ms));

/** Publish a nonempty lock directory atomically; stale cleanup can only remove its immutable owner file. */
async function acquireSetupLock(dataDir, { lockTimeoutMs = 120000, lockPollMs = 100, log = defaultLog } = {}) {
  await mkdir(dataDir, { recursive: true });
  const lockDir = join(dataDir, ".setup.lock");
  const token = `${process.pid}-${randomUUID()}.json`;
  const staging = join(dataDir, `.setup-owner-${token}`);
  await mkdir(staging);
  await writeFile(join(staging, token), JSON.stringify({ pid: process.pid, host: hostname(), token }), { flag: "wx", mode: 0o600 });
  const started = Date.now();
  let waiting = false;
  try {
    while (true) {
      try {
        await rename(staging, lockDir);
        return async () => {
          // Never recursively remove a lock: another process may already own this pathname.
          await unlink(join(lockDir, token)).catch(error => { if (error.code !== "ENOENT") throw error; });
          await rmdir(lockDir).catch(error => { if (!["ENOENT", "ENOTEMPTY", "EEXIST"].includes(error.code)) throw error; });
        };
      } catch (error) {
        if (!["EEXIST", "ENOTEMPTY", "EPERM", "EACCES"].includes(error.code)) throw error;
        let entries = [];
        try { entries = await readdir(lockDir); } catch (readError) { if (readError.code === "ENOENT") continue; throw readError; }
        for (const entry of entries) {
          if (!/^\d+-[\w-]+\.json$/.test(entry)) continue;
          let owner;
          try { owner = JSON.parse(await readFile(join(lockDir, entry), "utf8")); } catch { continue; }
          if (owner.token !== entry || owner.host !== hostname() || alive(owner.pid)) continue;
          await unlink(join(lockDir, entry)).catch(e => { if (e.code !== "ENOENT") throw e; });
        }
        // A new owner always has a file before its directory is published by rename.
        await rmdir(lockDir).catch(e => { if (!["ENOENT", "ENOTEMPTY", "EEXIST"].includes(e.code)) throw e; });
        if (Date.now() - started >= lockTimeoutMs) throw new Error("Timed out waiting for sanmu setup. A live setup lock is never forcibly removed.");
        if (!waiting) { log("Another sanmu setup owns the runtime lock; waiting.\n"); waiting = true; }
        await delay(lockPollMs);
      }
    }
  } finally { await rm(staging, { recursive: true, force: true }); }
}

function defaultLog(text) { process.stderr.write(text); }

function killTree(child) {
  if (!child.pid) return;
  if (process.platform === "win32") {
    const taskkill = join(process.env.SystemRoot || "C:\\Windows", "System32", "taskkill.exe");
    try { spawnSync(taskkill, ["/pid", String(child.pid), "/T", "/F"], { shell: false, windowsHide: true, timeout: 10000, stdio: "ignore" }); } catch {}
    try { child.kill("SIGKILL"); } catch {}
  } else {
    try { process.kill(-child.pid, "SIGKILL"); } catch { try { child.kill("SIGKILL"); } catch {} }
  }
}

/** All subprocesses use argv with shell:false; timeout and process exit clean up the whole child tree. */
export function runProcess(cmd, args, { env = process.env, cwd, timeoutMs = 600000, log, stdio = "pipe", signal } = {}) {
  return new Promise((accept, reject) => {
    let child;
    let stdout = "", stderr = "", failure;
    try {
      child = spawn(cmd, args, {
        env, ...(cwd ? { cwd } : {}), shell: false, windowsHide: true,
        detached: process.platform !== "win32", stdio: stdio === "inherit" ? "inherit" : ["ignore", "pipe", "pipe"],
      });
    } catch (error) { reject(error); return; }
    const append = (name, chunk) => {
      const text = chunk.toString("utf8");
      if (name === "stdout") stdout = (stdout + text).slice(-1024 * 1024);
      else stderr = (stderr + text).slice(-1024 * 1024);
      if (log) log(text);
    };
    child.stdout?.on("data", chunk => append("stdout", chunk));
    child.stderr?.on("data", chunk => append("stderr", chunk));
    const stop = (message, exitCode) => {
      failure ||= Object.assign(new Error(message), { exitCode });
      killTree(child);
    };
    const onInterrupt = () => stop("sanmu command interrupted.", 130);
    const onTerminate = () => stop("sanmu command terminated.", 143);
    const onExit = () => killTree(child);
    const onAbort = () => stop("sanmu command aborted.", 130);
    process.once("SIGINT", onInterrupt);
    process.once("SIGTERM", onTerminate);
    process.once("exit", onExit);
    signal?.addEventListener("abort", onAbort, { once: true });
    if (signal?.aborted) onAbort();
    const timer = setTimeout(() => stop(`sanmu subprocess timed out after ${timeoutMs}ms.`, 124), timeoutMs);
    const finish = error => {
      clearTimeout(timer);
      process.off("SIGINT", onInterrupt);
      process.off("SIGTERM", onTerminate);
      process.off("exit", onExit);
      signal?.removeEventListener("abort", onAbort);
      if (error || failure) reject(failure || error);
    };
    child.once("error", error => { finish(error); });
    child.once("close", (code, childSignal) => {
      finish();
      if (!failure) accept({ code: code ?? (childSignal === "SIGINT" ? 130 : 1), stdout, stderr, signal: childSignal });
    });
  });
}

function executablePaths(env, command) {
  const extensions = process.platform === "win32" ? ["", ".cmd", ".exe"] : [""];
  return pathValue(env).split(delimiter).filter(Boolean).flatMap(dir => extensions.map(ext => join(dir.replace(/^"|"$/g, ""), command + ext)));
}

export function resolveNpmRuntime({ env = process.env } = {}) {
  const candidates = [value(env.npm_execpath), join(dirname(process.execPath), "node_modules", "npm", "bin", "npm-cli.js")];
  for (const path of executablePaths(env, "npm")) {
    candidates.push(join(dirname(path), "node_modules", "npm", "bin", "npm-cli.js"));
    try { candidates.push(realpathSync(path)); } catch {}
  }
  for (const path of candidates) {
    if (path && basename(path) === "npm-cli.js" && existsSync(path)) return { cmd: process.execPath, prefixArgs: [path] };
  }
  if (process.platform !== "win32") {
    const command = executablePaths(env, "npm").find(path => existsSync(path));
    if (command) return { cmd: command, prefixArgs: [] };
  }
  throw new Error("npm-cli.js was not found. Install Node >=22.20 with npm; sanmu never invokes npm.cmd through a shell.");
}

function installationEnvironment(env, paths) {
  const result = runtimeEnvironment(env, paths, true);
  for (const name of Object.keys(result)) {
    if (/^(?:PIP_|NPM_CONFIG_)/i.test(name)) delete result[name];
  }
  Object.assign(result, {
    PIP_CONFIG_FILE: process.platform === "win32" ? "nul" : "/dev/null",
    PIP_DISABLE_PIP_VERSION_CHECK: "1", PIP_NO_INPUT: "1", PIP_REQUIRE_VIRTUALENV: "1",
    npm_config_userconfig: join(paths.dataDir, "npm-user.rc"),
    npm_config_globalconfig: join(paths.dataDir, "npm-global.rc"),
    npm_config_cache: join(paths.dataDir, "npm-cache"), npm_config_prefix: paths.nodeDir,
  });
  return result;
}

function redactedLog(log, env) {
  const secrets = Object.entries(env).filter(([name, text]) => /KEY|TOKEN|SECRET|PASSWORD/i.test(name) && typeof text === "string" && text.length >= 4).map(([, text]) => text);
  return text => {
    for (const secret of secrets) text = text.split(secret).join("[redacted]");
    log(text);
  };
}

function stepFailure(code, label) {
  return Object.assign(new Error(`${label} failed (exit ${code}). Run sanmu setup to retry.`), { exitCode: Number.isInteger(code) && code > 0 && code < 256 ? code : 1 });
}

/** Prepare only user-owned directories. Explicit setup may override SANMU_NO_AUTO_SETUP. */
export async function ensureRuntime({ root, dataDir, env = process.env, python = true, katex = true, explicit = false,
  autoSetup = true, runner = runProcess, probe = spawnSync, npmRuntime, timeoutMs = 600000,
  lockTimeoutMs = 120000, lockPollMs = 100, log = defaultLog, signal } = {}) {
  const paths = optionPaths({ root, dataDir, env });
  const spec = requirements(paths);
  const original = resolvePythonRuntime({ env, dataDir: paths.dataDir, root: paths.pluginRoot, probe });
  if (python && value(env.SANMU_PYTHON) && !original.supported) throw new Error(original.error);
  const checkedEnv = runtimeEnvironment(env, paths, true);
  const managed = () => ({ ...probeVersion({ cmd: managedPython(paths), prefixArgs: [], managed: true }, checkedEnv, probe), env: checkedEnv });
  const state = () => {
    const runtime = managed();
    return { runtime, python: python ? probeDependencies(runtime, spec.specs, paths.pythonDir, probe) : { ok: true },
      katex: katex ? probeKatex(paths, checkedEnv, probe) : { available: true, version: null } };
  };
  let current = state();
  const ready = () => current.python.ok && current.katex.available;
  const result = () => ({ ...(python ? current.runtime : original), env: python ? checkedEnv : runtimeEnvironment(env, paths, false),
    dataDir: paths.dataDir, pythonReady: Boolean(current.python.ok), katexReady: Boolean(current.katex.available) });
  if (ready()) return result();
  if (!explicit && (!autoSetup || value(env.SANMU_NO_AUTO_SETUP) === "1")) {
    throw new Error("The private sanmu runtime is incomplete and automatic setup is disabled. Run sanmu setup explicitly.");
  }
  const logger = redactedLog(log, env);
  const release = await acquireSetupLock(paths.dataDir, { lockTimeoutMs, lockPollMs, log: logger });
  const readyPath = join(paths.dataDir, "runtime-ready.json");
  try {
    current = state();
    if (ready()) return result();
    await rm(readyPath, { force: true });
    const installEnv = installationEnvironment(env, paths);
    const step = async (cmd, args, label, cwd, limit = timeoutMs) => {
      logger(`${label}\n`);
      const completed = await runner(cmd, args, { env: installEnv, cwd, timeoutMs: limit, shell: false, log: logger, signal });
      const code = completed.code ?? completed.status;
      if (code !== 0 || completed.error) throw stepFailure(code, label);
    };
    let existingPython = null;
    if (python && !current.python.ok) {
      const base = resolvePythonRuntime({ env, dataDir: paths.dataDir, root: paths.pluginRoot, probe, systemOnly: true });
      if (!base.supported) throw new Error(base.error);
      existingPython = { version: base.version, ...probeDependencies(base, spec.specs, "", probe) };
      if (!current.runtime.supported || !current.python.isolated) {
        // An interrupted venv creation is repaired in place; no unrelated directory is deleted.
        await step(base.cmd, [...base.prefixArgs, "-I", "-m", "venv", paths.pythonDir], "Creating private Python environment.", paths.dataDir, Math.min(timeoutMs, 120000));
        current.runtime = managed();
        if (!current.runtime.supported) throw new Error("Private Python creation did not produce a supported interpreter.");
      }
      const pipCheck = probe(current.runtime.cmd, ["-I", "-m", "pip", "--version"], { env: installEnv, shell: false, windowsHide: true, encoding: "utf8", timeout: 10000 });
      if ((pipCheck.status ?? pipCheck.code) !== 0 || pipCheck.error) {
        await step(current.runtime.cmd, ["-I", "-m", "ensurepip", "--upgrade"], "Restoring private pip.", paths.dataDir, Math.min(timeoutMs, 120000));
      }
      await step(current.runtime.cmd, ["-I", "-m", "pip", "--isolated", "install", "--disable-pip-version-check", "--no-input", "--no-cache-dir", "--require-virtualenv", "--no-user",
        ...spec.files.flatMap(file => ["-r", file])], "Installing private doc2md and reader dependencies.", paths.dataDir);
    }
    let existingKatex = null;
    if (katex && !current.katex.available) {
      existingKatex = probeKatex(paths, env, probe, false);
      await mkdir(paths.nodeDir, { recursive: true });
      for (const rc of ["npm-user.rc", "npm-global.rc"]) {
        await writeFile(join(paths.dataDir, rc), "", { flag: "wx", mode: 0o600 }).catch(error => { if (error.code !== "EEXIST") throw error; });
      }
      await writeFile(join(paths.nodeDir, "package.json"), JSON.stringify({ name: "sanmu-private-doc2md-runtime", private: true, dependencies: { katex: spec.katex } }, null, 2) + "\n", { mode: 0o600 });
      const npm = npmRuntime || resolveNpmRuntime({ env });
      await step(npm.cmd, [...npm.prefixArgs, "install", "--prefix", paths.nodeDir, "--ignore-scripts", "--no-audit", "--no-fund", "--package-lock=false"],
        "Installing private KaTeX.", paths.nodeDir);
    }
    current = state();
    if (!ready()) throw new Error("Private runtime verification failed; no ready marker was written. Run sanmu setup to retry.");
    const temporary = `${readyPath}.${process.pid}.${randomUUID()}.tmp`;
    try {
      await writeFile(temporary, JSON.stringify({ schema: 1, fingerprint: spec.fingerprint, checkedAt: new Date().toISOString(),
        python: python ? { cmd: current.runtime.cmd, version: current.runtime.version, dependencies: current.python.dependencies } : null,
        katex: katex ? current.katex : null, existingPython, existingKatex }, null, 2) + "\n", { flag: "wx", mode: 0o600 });
      await rename(temporary, readyPath);
    } finally { await rm(temporary, { force: true }); }
    logger("Private sanmu runtime verified.\n");
    return result();
  } finally { await release(); }
}
