import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { copyFile, mkdir, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { existsSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { setTimeout as delay } from "node:timers/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import test from "node:test";

const repoRoot = fileURLToPath(new URL("../../", import.meta.url));
const extensionPath = join(repoRoot, "extensions", "pi.ts");
const skillNames = ["sanmu-doc2md", "sanmu-paper-reader", "sanmu-paper-search"];
const packageManifest = {
  name: "sanmu-skills",
  version: "1.10.2",
  type: "module",
  pi: { extensions: ["./extensions/pi.ts"], skills: ["./skills"] },
};

function findPiEntry() {
  if (process.env.SANMU_PI_PACKAGE_DIR) {
    return join(resolve(process.env.SANMU_PI_PACKAGE_DIR), "dist", "index.js");
  }
  try {
    return fileURLToPath(import.meta.resolve("@earendil-works/pi-coding-agent"));
  } catch {}
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  const result = spawnSync(npm, ["root", "-g"], {
    encoding: "utf8",
    // Windows npm is a .cmd shim; this command has no user-derived arguments.
    shell: process.platform === "win32",
    windowsHide: true,
    timeout: 15000,
  });
  const root = result.status === 0 ? result.stdout.trim() : "";
  const candidate = root && join(root, "@earendil-works", "pi-coding-agent", "dist", "index.js");
  if (candidate && existsSync(candidate)) return candidate;
  return undefined;
}

const piEntry = findPiEntry();
const sdk = piEntry ? await import(pathToFileURL(piEntry).href) : undefined;
// Pi's dependencies are ESM-only; ask Node's import resolver in Pi's module root.
const mcpEntry = piEntry ? spawnSync(process.execPath, [
  "--input-type=module", "-e", "process.stdout.write(import.meta.resolve('@earendil-works/pi-mcp'))",
], { cwd: dirname(piEntry), encoding: "utf8", timeout: 15000, windowsHide: true }) : undefined;
if (mcpEntry && mcpEntry.status !== 0) throw new Error(mcpEntry.stderr || "Cannot resolve Pi's native MCP client");
const nativeMcp = mcpEntry ? await import(mcpEntry.stdout.trim()) : undefined;

const fixtureCli = `import { createInterface } from "node:readline";
const tools = [
  { name: "doctor_status", description: "Inspect fixture runtime", inputSchema: { type: "object", properties: {}, additionalProperties: false } },
  { name: "echo_result", description: "Return mixed MCP content", inputSchema: { type: "object", properties: { message: { type: "string" } }, required: ["message"], additionalProperties: false } },
  { name: "fail_result", description: "Return an MCP tool failure", inputSchema: { type: "object", properties: {}, additionalProperties: false } },
];
for await (const line of createInterface({ input: process.stdin })) {
  const m = JSON.parse(line);
  if (m.id === undefined) continue;
  let result;
  if (m.method === "initialize") result = { protocolVersion: "2025-11-25", capabilities: { tools: {} }, serverInfo: { name: "sanmu-fixture", version: "1.0.0" } };
  else if (m.method === "tools/list") result = { tools };
  else if (m.method === "tools/call") {
    if (m.params.name === "doctor_status") result = { content: [{ type: "text", text: "fixture ready" }], structuredContent: { cwd: process.cwd(), workspace: process.env.SANMU_WORKSPACE_ROOT, host: process.env.SANMU_HOST, argv: process.argv.slice(2), pid: process.pid } };
    else if (m.params.name === "echo_result") result = { content: [{ type: "text", text: m.params.arguments.message }, { type: "image", mimeType: "image/png", data: "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aD1sAAAAASUVORK5CYII=" }, { type: "resource", resource: { uri: "fixture://text", mimeType: "text/plain", text: "embedded resource text" } }], structuredContent: { echoed: m.params.arguments.message } };
    else result = { isError: true, content: [{ type: "text", text: "fixture tool failed" }], structuredContent: { error: { code: "fixture_failure" } } };
  }
  else { process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: m.id, error: { code: -32601, message: "Method not found" } }) + "\\n"); continue; }
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id: m.id, result }) + "\\n");
}
`;

async function temporary(t, prefix) {
  const path = await mkdtemp(join(tmpdir(), prefix));
  t.after(() => rm(path, { recursive: true, force: true, maxRetries: 5, retryDelay: 100 }));
  return path;
}

async function makeFixture(t) {
  const directory = await temporary(t, "sanmu-pi-package-");
  // Exercise URL decoding, spaces, and Unicode in the installed package's path.
  const packageDir = join(directory, "三木 package");
  await mkdir(join(packageDir, "extensions"), { recursive: true });
  await mkdir(join(packageDir, "scripts"), { recursive: true });
  await copyFile(extensionPath, join(packageDir, "extensions", "pi.ts"));
  await writeFile(join(packageDir, "package.json"), JSON.stringify(packageManifest));
  await writeFile(join(packageDir, "scripts", "sanmu.mjs"), fixtureCli);
  for (const name of skillNames) {
    await mkdir(join(packageDir, "skills", name), { recursive: true });
    await copyFile(join(repoRoot, "skills", name, "SKILL.md"), join(packageDir, "skills", name, "SKILL.md"));
  }
  return packageDir;
}

function alive(pid) {
  try {
    process.kill(pid, 0);
    return true;
  } catch (error) {
    return error.code === "EPERM";
  }
}

async function waitFor(predicate, message, timeoutMs = 15000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    const result = predicate();
    if (result) return result;
    await delay(25);
  }
  assert.fail(message);
}

async function createSession(t, packageDir, workspace, sandbox) {
  const agentDir = join(sandbox, "agent");
  const dataDir = join(sandbox, "sanmu-data");
  await mkdir(workspace, { recursive: true });
  await mkdir(agentDir, { recursive: true });
  const settingsManager = sdk.SettingsManager.inMemory({
    packages: [],
    compaction: { enabled: false },
    cacheWarming: { enabled: false },
    telemetry: { enabled: false },
  });
  const credentials = { read: async () => undefined, list: async () => [], modify: async (_id, fn) => fn(undefined), delete: async () => {} };
  const modelRuntime = await sdk.ModelRuntime.create({
    credentials,
    modelsPath: null,
    allowModelNetwork: false,
    refreshOnCreate: false,
  });
  const transports = [];
  const errors = [];
  const resourceLoader = new sdk.DefaultResourceLoader({
    cwd: workspace,
    agentDir,
    settingsManager,
    // Explicit local package loading checks pi.extensions and pi.skills without installing anything.
    additionalExtensionPaths: [packageDir],
    noExtensions: true,
    noSkills: true,
    noPromptTemplates: true,
    noThemes: true,
    noContextFiles: true,
    extensionFactories: [
      sdk.createCodemodeExtension({ mode: "on" }),
      sdk.createToolSearchExtension(),
      sdk.createMcpExtension({
        loadConfig: () => ({ servers: [], errors: [], autoEnableCodemode: true }),
        logPath: join(agentDir, "mcp.log"),
        // Stdio does not use OAuth. Supplying a store avoids opening the user's credential file.
        credentials: { forServer: () => { throw new Error("Unexpected OAuth access in stdio test"); } },
        createTransport(entry, cwd) {
          assert.equal(entry.name, "sanmu");
          const transport = new nativeMcp.StdioTransport({
            command: entry.config.command,
            args: entry.config.args,
            cwd: entry.config.cwd ?? cwd,
            env: {
              ...entry.config.env,
              SANMU_DATA_DIR: dataDir,
              SANMU_NO_AUTO_SETUP: "1",
              SANMU_PANEL_WORKER: "1",
            },
          });
          transports.push({ transport, entry });
          return transport;
        },
      }),
    ],
  });
  await resourceLoader.reload();
  assert.deepEqual(resourceLoader.getExtensions().errors, []);
  assert.deepEqual(resourceLoader.getSkills().skills.map((s) => s.name).sort(), [...skillNames].sort());
  const { session } = await sdk.createAgentSession({
    cwd: workspace,
    agentDir,
    modelRuntime,
    model: modelRuntime.getModels()[0],
    settingsManager,
    resourceLoader,
    sessionManager: sdk.SessionManager.inMemory(workspace),
  });
  let closed = false;
  async function close(reason = "quit") {
    if (closed) return;
    closed = true;
    try {
      // SDK dispose() alone does not emit the awaited session_shutdown hook.
      await session.extensionRunner.emit({ type: "session_shutdown", reason });
    } finally {
      session.dispose();
    }
  }
  t.after(() => close());
  await session.bindExtensions({ onError: (error) => errors.push(error) });
  await waitFor(
    () => session.getToolDefinition("mcp__sanmu__doctor_status"),
    `Native MCP did not discover sanmu tools: ${JSON.stringify(errors)}`,
  );
  assert.deepEqual(errors, []);
  assert.equal(transports.length, 1);
  session.agent.state.messages.push({ role: "assistant", content: [{ type: "text", text: "Isolated tool acceptance test" }],
    api: "openai-responses", provider: "test", model: "test", timestamp: Date.now(), stopReason: "toolUse",
    usage: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, totalTokens: 0, cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0, total: 0 } } });
  const invoke = async (name, args = {}, options) => {
    const outcome = await session.extensionRunner.createToolContext("sanmu-test", undefined)
      .executeTool(`mcp__sanmu__${name}`, args, options);
    return { ...outcome.result, isError: outcome.isError };
  };
  return { session, transports, errors, close, invoke, dataDir };
}

const requiresPi = !sdk ? "Install Pi >=1.4 first, or set SANMU_PI_PACKAGE_DIR to its existing package directory" : false;

test("extension rejects hosts without the native MCP API", async () => {
  const { default: extension } = await import(pathToFileURL(extensionPath).href);
  assert.throws(() => extension({ on() {} }), /Pi 1\.4 or newer.*registerMcpServer/);
});

test("factory defers registration and derives the actual session cwd", async (t) => {
  const packageDir = await makeFixture(t);
  const { default: extension } = await import(pathToFileURL(join(packageDir, "extensions", "pi.ts")).href);
  const handlers = new Map();
  const registrations = [];
  extension({
    on: (event, handler) => handlers.set(event, handler),
    registerMcpServer: (name, config) => registrations.push({ name, config }),
  });
  assert.equal(registrations.length, 0, "loading an extension must not start a server");
  for (const workspace of [join(packageDir, "workspace one"), join(packageDir, "workspace 二")]) {
    handlers.get("session_start")({ reason: "startup" }, { cwd: workspace });
    const { name, config } = registrations.at(-1);
    assert.equal(name, "sanmu");
    assert.equal(config.command, process.execPath);
    assert.deepEqual(config.args, [join(packageDir, "scripts", "sanmu.mjs"), "mcp"]);
    assert.equal(config.cwd, workspace);
    assert.deepEqual(config.env, { SANMU_HOST: "pi", SANMU_WORKSPACE_ROOT: workspace });
    assert.equal(config.timeout, 300);
  }
  await rm(join(packageDir, "scripts", "sanmu.mjs"));
  assert.throws(() => handlers.get("session_start")({}, { cwd: packageDir }), /package is incomplete/);
});

test("Pi loads three packaged skills and bridges stdio schemas, content, errors, and shutdown without an LLM", { skip: requiresPi, timeout: 60000 }, async (t) => {
  assert.match(sdk.VERSION, /^(?:1\.[4-9]\.|1\.[1-9]\d+\.|[2-9]\.)/);
  const packageDir = await makeFixture(t);
  const sandbox = await temporary(t, "sanmu-pi-session-");
  const workspace = join(sandbox, "workspace 一");
  const first = await createSession(t, packageDir, workspace, join(sandbox, "first"));
  const status = await first.invoke("doctor_status");
  assert.equal(status.isError, false, JSON.stringify(status));
  const observed = status.structuredContent.structuredContent;
  assert.equal(observed.cwd, workspace);
  assert.equal(observed.workspace, workspace);
  assert.equal(observed.host, "pi");
  assert.deepEqual(observed.argv, ["mcp"]);
  assert(first.session.getActiveToolNames().includes("mcp__sanmu__doctor_status"));
  assert(first.session.getCallableToolNames().includes("mcp__sanmu__echo_result"));

  const echoed = await first.invoke("echo_result", { message: "Unicode 内容" });
  assert.equal(echoed.isError, false);
  assert.equal(echoed.content[0].text, "Unicode 内容");
  assert(echoed.content.some((block) => block.type === "image" && block.mimeType === "image/png"));
  assert(echoed.content.some((block) => block.type === "text" && block.text === "embedded resource text"));
  assert.equal(echoed.structuredContent.structuredContent.echoed, "Unicode 内容");
  const invalid = await first.invoke("echo_result", { message: { invalid: true } });
  assert.equal(invalid.isError, true, "Pi must validate the MCP input schema before calling the server");
  const failed = await first.invoke("fail_result");
  assert.equal(failed.isError, true);
  assert.equal(failed.structuredContent.isError, true);
  assert.equal(failed.structuredContent.structuredContent.error.code, "fixture_failure");
  await first.close("reload");
  await waitFor(() => !alive(observed.pid), "Pi left the MCP process alive after session shutdown");
  await first.close();

  const secondWorkspace = join(sandbox, "workspace 二");
  const second = await createSession(t, packageDir, secondWorkspace, join(sandbox, "second"));
  const nextStatus = (await second.invoke("doctor_status")).structuredContent.structuredContent;
  assert.equal(nextStatus.cwd, secondWorkspace);
  assert.equal(nextStatus.workspace, secondWorkspace);
  await second.close();
  await waitFor(() => !alive(nextStatus.pid), "Pi left the replacement MCP process alive");
  assert(!existsSync(join(workspace, ".pi", "mcp.json")));
  assert(!existsSync(join(secondWorkspace, ".pi", "mcp.json")));
});

test("real bundled CLI exposes every MCP tool through Pi without installation or a model", {
  skip: requiresPi || (!existsSync(join(repoRoot, "scripts", "sanmu.mjs")) ? "Bundled CLI is not present yet" : false),
  timeout: 60000,
}, async (t) => {
  const manifest = JSON.parse(await readFile(join(repoRoot, "package.json"), "utf8"));
  assert(manifest.pi.extensions.includes("./extensions/pi.ts"));
  const sandbox = await temporary(t, "sanmu-pi-real-");
  const workspace = join(sandbox, "project");
  const host = await createSession(t, repoRoot, workspace, sandbox);
  const names = host.session.getAllTools().map((tool) => tool.name).filter((name) => name.startsWith("mcp__sanmu__"));
  assert.equal(names.length, 24);
  for (const name of ["search_papers", "search_sciverse", "search_ai4scholar", "run_doc2md", "manage_doc2md", "manage_basket", "save_config"]) {
    assert(names.includes(`mcp__sanmu__${name}`), `Missing bundled MCP tool ${name}`);
  }
  const panel = await host.invoke("open_panel");
  assert.equal(panel.isError, false, JSON.stringify(panel));
  assert(panel.content.some((block) => block.type === "text"));
  const invalid = await host.invoke("search_papers", { query: { invalid: true } });
  assert.equal(invalid.isError, true);
  const pid = host.transports[0].transport.pid;
  assert(Number.isInteger(pid));
  await host.close();
  await waitFor(() => !alive(pid), "Bundled CLI did not exit when Pi closed its stdio transport");
  assert(!existsSync(join(workspace, ".pi", "mcp.json")));
});
