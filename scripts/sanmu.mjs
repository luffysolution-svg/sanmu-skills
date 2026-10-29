#!/usr/bin/env node
import { existsSync } from "node:fs";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { ensureRuntime, inspectRuntime, resolveConfigEnvironment, resolvePythonRuntime, resolveRuntimePaths, runProcess } from "./runtime.mjs";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const version = "1.10.3";
const help = `sanmu ${version} — research skills, MCP and browser workbench

Usage: sanmu <command> [arguments]
  mcp                         Start the stdio MCP server
  panel [--port N] [--no-open] Open the complete browser workbench
  setup [--json]              Prepare private Python and KaTeX dependencies
  doctor [--json]             Inspect dependencies and credential presence
  search ...                 Literature search CLI (all original arguments)
  fetch-pdf ...              OA PDF retrieval CLI
  doc2md <script> ...         Document CLI; default: build-research-document
  reader <tool> ...           evidence, topic, si-probe, extract-si, crop, benchmark

Document scripts: build-research-document, build-markdown, mineru-ocr,
  doctor, validate-output, extract-formulas, validate-math, preview-server,
  normalize-math, audit-figures, zotero-export, zotero-sync

Environment: SANMU_DATA_DIR, SANMU_PYTHON, SANMU_WORKSPACE_ROOT,
  SANMU_NO_AUTO_SETUP=1. Credentials can also be saved in the workbench.
`;
const readerTools = {
  evidence: "evidence_tools.py", topic: "topic_tools.py", "si-probe": "si_probe.py",
  "extract-si": "extract_si_figures.py", crop: "crop_subfigures.py", benchmark: "benchmark_collector.py"
};
const documentTools = Object.fromEntries([
  "build_research_document", "build_markdown", "mineru_ocr", "doctor", "validate_output",
  "extract_formulas", "preview_server", "normalize_math", "audit_figures", "zotero_export", "zotero_sync"
].map(stem => [stem.replaceAll("_", "-"), `${stem}.py`]));
documentTools["validate-math"] = "validate_math.cjs";

function publicDoctor(report) {
  return {
    version, ready: report.ready, paths: report.paths,
    python: { supported: report.system.supported, version: report.system.version, executable: report.system.cmd },
    privatePython: { ready: report.python.ok, version: report.managed.version, dependencies: report.python.dependencies },
    katex: report.katex, credentials: report.config, configFileExists: report.configFileExists
  };
}

function entrypoint(paths, ...parts) {
  return parts.map(path => join(paths.pluginRoot, path)).find(existsSync);
}

function parsePanelOptions(args) {
  const options = { port: 0, openBrowser: true };
  for (let i = 0; i < args.length; i++) {
    if (args[i] === "--no-open") options.openBrowser = false;
    else if (args[i] === "--port") {
      if (!/^\d+$/.test(args[i + 1] || "")) throw new Error("--port requires an integer from 0 to 65535.");
      options.port = Number(args[++i]);
    } else throw new Error(`Unknown panel option: ${args[i]}`);
  }
  return options;
}

async function pythonCommand(command, args, paths, env) {
  let script;
  let requiresDependencies = false;
  if (command === "search" || command === "fetch-pdf") {
    const name = command === "search" ? "paper_search.py" : "fetch_pdf.py";
    script = entrypoint(paths, `skills/sanmu-paper-search/scripts/${name}`, `scripts/${name}`);
  } else if (command === "reader") {
    if (!args.length || args[0] === "--help") { process.stdout.write(help); return 0; }
    const name = readerTools[args.shift()];
    if (!name) throw new Error("Unknown reader tool. Use evidence, topic, si-probe, extract-si, crop or benchmark.");
    script = join(paths.pluginRoot, "skills", "sanmu-paper-reader", "scripts", name);
    requiresDependencies = ["si_probe.py", "extract_si_figures.py", "crop_subfigures.py"].includes(name);
  } else {
    let key = (args[0] || "").replace(/\.(?:py|cjs)$/, "").replaceAll("_", "-");
    if (Object.hasOwn(documentTools, key)) args.shift();
    else if (args[0] && !args[0].startsWith("-")) throw new Error(`Unknown document script: ${args[0]}`);
    else key = "build-research-document";
    script = join(paths.pluginRoot, "skills", "sanmu-doc2md", "scripts", documentTools[key]);
    requiresDependencies = key !== "doctor";
  }
  if (!script || !existsSync(script)) throw new Error(`The sanmu package is incomplete: missing ${script || command}.`);
  const isHelp = args.includes("--help") || args.includes("-h");
  const runtime = requiresDependencies && !isHelp
    ? await ensureRuntime({ root: paths.pluginRoot, dataDir: paths.dataDir, env })
    : resolvePythonRuntime({ root: paths.pluginRoot, dataDir: paths.dataDir, env });
  if (!runtime.supported) throw new Error(runtime.error || "Python >=3.10 is required.");
  const nodeScript = script.endsWith(".cjs");
  const result = await runProcess(nodeScript ? process.execPath : runtime.cmd,
    [...(nodeScript ? [] : runtime.prefixArgs), script, ...args],
    { env: { ...env, ...runtime.env }, cwd: paths.workspaceRoot, stdio: "inherit", timeoutMs: 1800000 });
  return result.code;
}

export async function main(argv = process.argv.slice(2)) {
  const [major, minor] = process.versions.node.split(".").map(Number);
  if (major < 22 || major === 22 && minor < 20) throw new Error("sanmu requires Node.js >=22.20.0.");
  const [command = "--help", ...args] = argv;
  if (["--help", "-h", "help"].includes(command)) { process.stdout.write(help); return 0; }
  if (["--version", "-v"].includes(command)) { process.stdout.write(version + "\n"); return 0; }
  const paths = resolveRuntimePaths(process.env, root);
  const env = resolveConfigEnvironment({ env: process.env, root: paths.pluginRoot, dataDir: paths.dataDir });
  Object.assign(env, { SANMU_PLUGIN_ROOT: paths.pluginRoot, SANMU_DATA_DIR: paths.dataDir, SANMU_WORKSPACE_ROOT: paths.workspaceRoot });
  if (command === "mcp") {
    if (args.length) throw new Error("mcp takes no arguments.");
    const server = entrypoint(paths, "scripts/mcp-server.mjs", "distribution/zcode/sanmu-paper-search/scripts/mcp-server.mjs");
    if (!server) throw new Error("The sanmu package is incomplete: MCP server missing.");
    Object.assign(process.env, { SANMU_PLUGIN_ROOT: paths.pluginRoot, SANMU_DATA_DIR: paths.dataDir,
      ...(process.env.SANMU_HOST === "codex" && !process.env.SANMU_WORKSPACE_ROOT ? {} : { SANMU_WORKSPACE_ROOT: paths.workspaceRoot }),
      SANMU_HOST: process.env.SANMU_HOST || "cli" });
    const { startStdio } = await import(pathToFileURL(server).href);
    await startStdio();
    return 0;
  }
  if (command === "panel") {
    if (args.includes("--help")) { process.stdout.write(help); return 0; }
    const { startPanelServer } = await import("./panel-server.mjs");
    const panel = await startPanelServer({ ...parsePanelOptions(args), hostRoot: paths.pluginRoot, env });
    process.stdout.write(`Sanmu workbench: ${panel.url}\n`);
    if (panel.browserError) process.stderr.write(`Open this link in your browser: ${panel.browserError}\n`);
    return 0;
  }
  if (command === "setup" || command === "doctor") {
    if (args.some(arg => arg !== "--json")) throw new Error(`${command} accepts only --json.`);
    if (command === "setup") await ensureRuntime({ root: paths.pluginRoot, dataDir: paths.dataDir, env, explicit: true });
    const report = publicDoctor(inspectRuntime({ root: paths.pluginRoot, dataDir: paths.dataDir, env }));
    process.stdout.write(JSON.stringify(report, null, 2) + "\n");
    return command === "setup" && !report.ready ? 1 : 0;
  }
  if (["search", "fetch-pdf", "doc2md", "reader"].includes(command)) return pythonCommand(command, args, paths, env);
  throw new Error(`Unknown sanmu command: ${command}. Run sanmu --help.`);
}

if (process.argv[1] && resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  try { process.exitCode = await main(); }
  catch (error) {
    process.stderr.write(`sanmu: ${error.message}\n`);
    process.exitCode = Number.isInteger(error.exitCode) && error.exitCode > 0 && error.exitCode < 256 ? error.exitCode : 1;
  }
}
