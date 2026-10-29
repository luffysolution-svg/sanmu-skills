import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { cp, mkdir, mkdtemp, readFile, rm, symlink, writeFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { tmpdir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';
import test from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
const canonical = join(root, 'distribution/zcode/sanmu-paper-search');
const adapterPath = join(canonical, 'scripts/dsh-plugin.mjs');
const expectedSkills = ['sanmu-doc2md', 'sanmu-paper-reader', 'sanmu-paper-search'];
const manifest = JSON.parse(await readFile(join(root, 'package.json'), 'utf8'));
const prefix = 'mcp__sanmu-paper-search__';
const runtimeDir = process.env.SANMU_DSH_TEST_RUNTIME;
const runtimeAnchor = runtimeDir && join(resolve(runtimeDir), 'package.json');
const runtimeRequire = runtimeAnchor && createRequire(runtimeAnchor);
const runtimeExecutable = process.env.SANMU_DSH_TEST_EXECUTABLE || process.execPath;

async function temporary(t) {
  const directory = await mkdtemp(join(tmpdir(), 'sanmu-dsh-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

async function fixturePeers(directory) {
  for (const [module, source] of [
    ['dsh-mcp-client', 'export const Config = value => value; export async function apply() {}'],
    ['dsh-skill-filesystem', 'export const Config = value => value; export function apply() {}']
  ]) {
    const target = join(directory, 'node_modules/@deepseek-ai', module);
    await mkdir(target, { recursive: true });
    await writeFile(join(target, 'package.json'), JSON.stringify({ name: `@deepseek-ai/${module}`, type: 'module', exports: './index.mjs' }));
    await writeFile(join(target, 'index.mjs'), source);
  }
}

async function fixturePackage(directory, { shell = false, peers = true } = {}) {
  const adapter = shell ? join(directory, 'scripts/dsh-plugin.mjs')
    : join(directory, 'distribution/zcode/sanmu-paper-search/scripts/dsh-plugin.mjs');
  await mkdir(dirname(adapter), { recursive: true });
  await cp(adapterPath, adapter);
  await writeFile(join(dirname(adapter), 'mcp-server.mjs'), '// fixture path; fake peer owns startup\n');
  await writeFile(join(directory, 'package.json'), JSON.stringify({ name: 'sanmu-skills', type: 'module', version: manifest.version }));
  if (!shell) {
    await mkdir(join(directory, 'scripts'), { recursive: true });
    await writeFile(join(directory, 'scripts/sanmu.mjs'), '// shared CLI fixture\n');
  }
  for (const skill of expectedSkills) {
    await mkdir(join(directory, 'skills', skill), { recursive: true });
    await writeFile(join(directory, 'skills', skill, 'SKILL.md'), `---\nname: ${skill}\ndescription: ${skill} fixture\n---\n\nFixture body.\n`);
  }
  if (peers) await fixturePeers(directory);
  return await import(pathToFileURL(adapter).href);
}

/** A small scoped registry: policy precedes around-dispatch and bodies resolve the latest definition. */
function fixtureContext({ failMcp = false, failSkills = false } = {}) {
  const layers = new Map();
  const events = new Map();
  const commands = new Map();
  const calls = [];
  const disposed = [];
  const executions = [];
  const guards = [];
  const warnings = [];
  const toolNames = ['open_panel', 'save_config', 'search_papers', 'doc2md'];
  let rootCtx;
  const visible = agent => {
    const table = new Map(layers.get(undefined));
    const chain = [];
    for (let cursor = agent; cursor; cursor = cursor.parent) chain.unshift(cursor);
    for (const scope of chain) for (const [name, definition] of layers.get(scope) || []) table.set(name, definition);
    return table;
  };
  const failure = error => ({ isError: true, error: { message: error.message }, content: [{ type: 'text', text: error.message }] });
  const tools = {
    register() {},
    get(name, agent) { return visible(agent).get(name); },
    schemas(agent) { return [...visible(agent).values()].map(({ name, description, parameters }) => structuredClone({ name, description, parameters })); },
    async execute(exec) {
      executions.push(exec);
      try {
        exec.signal.throwIfAborted();
        for (const guard of guards) {
          const denied = guard(exec);
          if (denied) return failure(new Error(denied));
        }
        const hooks = [...events.get('tools/execute') || []];
        const dispatch = async index => index < hooks.length ? hooks[index].callback(exec, () => dispatch(index + 1)) : body();
        const body = async () => {
          exec.signal.throwIfAborted();
          const definition = tools.get(exec.name, exec.agent);
          if (!definition) throw new Error(`UNKNOWN_TOOL: ${exec.name}`);
          const value = await definition.execute(exec.arguments, exec);
          return { isError: false, value, content: value.content };
        };
        return await dispatch(0);
      } catch (error) { return failure(error); }
    }
  };
  function context(scope) {
    const effects = new Set();
    const ctx = {
      scope, tools, skills: { registerProvider() {} },
      get(key) { return this[key] ?? (this === rootCtx ? undefined : rootCtx[key]); },
      effect(setup) {
        const cleanup = setup();
        let stopping;
        let inactive = false;
        const dispose = () => {
          if (inactive) return stopping;
          inactive = true;
          effects.delete(dispose);
          return stopping = Promise.resolve().then(() => cleanup?.());
        };
        effects.add(dispose);
        return dispose;
      },
      inject(keys, callback) { if (keys.every(key => this.get(key))) return callback(this); },
      on(name, callback) {
        const hooks = events.get(name) || [];
        events.set(name, hooks);
        const hook = { callback, scope };
        hooks.push(hook);
        return this.effect(() => () => hooks.splice(hooks.indexOf(hook), 1));
      },
      plugin(plugin, config) {
        const kind = 'serverName' in config ? 'mcp' : 'skills';
        const call = { kind, config, plugin, scope };
        calls.push(call);
        let stopped = false;
        const owned = [];
        const promise = new Promise((resolvePromise, reject) => setImmediate(() => {
          if ((kind === 'mcp' && rootCtx.failMcp) || (kind === 'skills' && rootCtx.failSkills)) {
            reject(new Error(`fixture ${kind.toUpperCase()} startup failed`));
            return;
          }
          if (!stopped && kind === 'mcp') {
            const layer = layers.get(scope) || new Map();
            layers.set(scope, layer);
            for (const rawName of toolNames) {
              const name = prefix + rawName;
              const definition = { name, description: `${rawName} fixture`, parameters: { type: 'object', properties: {} },
                async execute(args, exec) {
                  if (rootCtx.respond) return rootCtx.respond(args, exec, config);
                  return { content: [{ type: 'text', text: 'canonical result' }],
                    structuredContent: { settings: { workspaceRoot: config.cwd }, operation: rawName } };
                } };
              layer.set(name, definition);
              owned.push([name, definition]);
            }
          }
          resolvePromise();
        }));
        const dispose = this.effect(() => async () => {
          stopped = true;
          disposed.push(kind);
          for (const [name, definition] of owned) if (layers.get(scope)?.get(name) === definition) layers.get(scope).delete(name);
          await promise.catch(() => {});
        });
        return call.child = { then: promise.then.bind(promise), dispose };
      },
      async unload() { await Promise.allSettled([...effects].reverse().map(dispose => dispose())); }
    };
    return ctx;
  }
  rootCtx = context();
  Object.assign(rootCtx, {
    calls, disposed, executions, guards, warnings, failMcp, failSkills,
    logger: { warn: message => warnings.push(message) },
    commands: { register(definition) {
      assert.ok(!commands.has(definition.name), 'command may be registered only once');
      commands.set(definition.name, definition);
      return rootCtx.effect(() => () => commands.delete(definition.name));
    } },
    workingDirectory: { get(session) { return session.currentCwd || session.header?.cwd || session.request?.cwd; } },
    commandDefinition: () => commands.get('sanmu'),
    command(request, agent, { signal = new AbortController().signal, commandId = 'command-fixture' } = {}) {
      return commands.get('sanmu').handler({ agent, rawInput: typeof request === 'string' ? request : JSON.stringify(request), commandId, signal, attachments: [] });
    },
    agent(cwd, parent) {
      const agent = { session: { header: { cwd } }, parent };
      agent.ctx = context(agent);
      return agent;
    },
    async announce(agent) { for (const hook of events.get('agent/created') || []) await hook.callback({ agent }); }
  });
  return rootCtx;
}

async function runNode(source, directory, environment = {}) {
  const child = spawn(runtimeExecutable, ['--expose-internals', '--input-type=module', '-e', source], {
    cwd: directory, env: { ...process.env, ...environment,
      ...(runtimeExecutable !== process.execPath ? { ELECTRON_RUN_AS_NODE: '1' } : {}) },
    stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
  });
  let stdout = '';
  let stderr = '';
  child.stdout.on('data', data => { stdout += data; });
  child.stderr.on('data', data => { stderr += data; });
  const timer = setTimeout(() => child.kill(), 90000);
  try {
    const code = await new Promise((resolveExit, reject) => {
      child.once('error', reject);
      child.once('exit', resolveExit);
    });
    assert.equal(code, 0, `isolated official runtime failed\n${stdout}\n${stderr}`);
    return { stdout, stderr };
  } finally { clearTimeout(timer); }
}

test('DSH bundle manifests use package exports, installable patches, and optional host peers', async () => {
  for (const directory of [root, canonical]) {
    const pkg = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
    assert.equal(pkg.version, manifest.version);
    assert.equal(pkg.dsh.manifestVersion, 1);
    const patch = await readFile(join(directory, pkg.dsh.bundle.patch), 'utf8');
    assert.match(patch, /id: sanmu-skills/);
    const request = patch.match(/name:\s*["']([^"']+)["']/)?.[1];
    assert.equal(request, pkg.name);
    assert.ok(existsSync(resolve(directory, pkg.exports['.'])));
    for (const peer of ['@deepseek-ai/dsh-mcp-client', '@deepseek-ai/dsh-skill-filesystem']) {
      assert.equal(pkg.peerDependenciesMeta[peer].optional, true);
      assert.equal(pkg.peerDependencies[peer], pkg.engines.dsh);
    }
  }
});

test('apply mounts packaged skills and awaits MCP in root and expanded-shell layouts', async t => {
  for (const shell of [false, true]) await t.test(shell ? 'expanded plugin cache' : 'repository/npm package', async t => {
    const directory = await temporary(t);
    const workspace = join(directory, 'different workspace');
    await mkdir(workspace);
    const adapter = await fixturePackage(directory, { shell });
    const ctx = fixtureContext();
    t.after(() => ctx.unload());
    ctx.session = { request: { cwd: workspace } };
    ctx.profileContext = { cwd: join(directory, 'incorrect-launch-cwd'), home: join(directory, 'isolated-dsh') };
    await adapter.apply(ctx, { env: { USER_CONFIG_EXA_API_KEY: 'fixture-key' } });
    assert.deepEqual(ctx.calls.map(call => call.kind), ['skills', 'mcp']);
    assert.deepEqual(ctx.calls[0].config, { providerName: 'sanmu-skills', includeDefaultRoots: false,
      bundledSkillDir: join(directory, 'skills'), watch: false });
    const mcpConfig = ctx.calls[1].config;
    assert.equal(mcpConfig.command, process.execPath);
    assert.equal(mcpConfig.cwd, workspace);
    assert.equal(mcpConfig.env.SANMU_WORKSPACE_ROOT, workspace);
    assert.equal(mcpConfig.env.DSH_WORKSPACE_ROOT, workspace);
    assert.equal(mcpConfig.env.SANMU_PLUGIN_ROOT, directory);
    assert.equal(mcpConfig.env.SANMU_SKILLS_ROOT, join(directory, 'skills'));
    assert.equal(mcpConfig.env.USER_CONFIG_EXA_API_KEY, 'fixture-key');
    assert.equal(mcpConfig.failOnStartupError, false);
    assert.deepEqual(mcpConfig.args, shell ? [join(directory, 'scripts/mcp-server.mjs')] : [join(directory, 'scripts/sanmu.mjs'), 'mcp']);
    assert.equal(ctx.commandDefinition().recordInput, false);
    assert.deepEqual(ctx.disposed, []);
  });
});

test('Desktop Node 22.20 and bundled Electron invocation are supported without passing pnpm arguments', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  assert.doesNotThrow(() => adapter.assertSupportedRuntime({ node: '22.20.0', electron: '38.0.0' }));
  assert.doesNotThrow(() => adapter.assertSupportedRuntime({ node: '24.8.0' }));
  assert.throws(() => adapter.assertSupportedRuntime({ node: '22.19.0' }), /Node.js >=22\.20/);
  const electron = join(directory, 'DeepSeek Harness.exe');
  const ctx = { profileContext: { packageManager: { command: electron,
    args: ['--expose-internals', join(directory, 'pnpm.cjs')],
    env: { ELECTRON_RUN_AS_NODE: '1', DSH_DESKTOP_NODE_EXECUTABLE: electron, PATH: 'bundled-node-path' } } } };
  const config = adapter.createMcpConfig({ DSH_WORKSPACE_ROOT: directory }, { pluginRoot: directory,
    env: { ELECTRON_RUN_AS_NODE: '0' } }, ctx, { execPath: 'incorrect-node', versions: { node: '22.20.0', electron: '38.0.0' } });
  assert.equal(config.command, electron);
  assert.equal(config.env.ELECTRON_RUN_AS_NODE, '1');
  assert.equal(config.env.PATH, 'bundled-node-path');
  assert.ok(!config.args.some(arg => arg.includes('pnpm') || arg === '--expose-internals'));
  assert.equal(adapter.resolveNodeInvocation({}, { nodeCommand: 'explicit-node' }, ctx).command, 'explicit-node');
  assert.equal(adapter.resolveNodeInvocation({}, {}, { profileContext: { packageManager: { command: 'pnpm', args: [], env: {} } } },
    { execPath: 'real-node', versions: {} }).command, 'real-node');
  const fallback = adapter.createMcpConfig({ DSH_WORKSPACE_ROOT: directory }, { pluginRoot: directory }, {},
    { execPath: electron, versions: { node: '22.20.0', electron: '38.0.0' } });
  assert.equal(fallback.env.ELECTRON_RUN_AS_NODE, '1');
});

test('host peers resolve from install anchors and older Electron launch anchors without pluginPackages', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory, { peers: false });
  const installation = join(directory, 'host-installation');
  await fixturePeers(installation);
  const ctx = { profileContext: { installAnchor: join(installation, 'package.json') } };
  Object.defineProperty(ctx, 'pluginPackages', { get() { throw new Error('undocumented API accessed'); } });
  assert.equal(typeof (await adapter.hostPlugin(ctx, '@deepseek-ai/dsh-mcp-client')).apply, 'function');
  const old = { profileContext: { dir: join(directory, 'profile-without-peers') } };
  const peer = await adapter.hostPlugin(old, '@deepseek-ai/dsh-skill-filesystem', {
    versions: { electron: '38.0.0' }, argv: ['electron', join(installation, 'desktop-host.mjs'), installation]
  });
  assert.equal(typeof peer.Config, 'function');
});

test('default startup failure is contained, retains native command, and can retry MCP', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const ctx = fixtureContext({ failMcp: true });
  ctx.profileContext = { cwd: directory };
  t.after(() => ctx.unload());
  await adapter.apply(ctx);
  const agent = ctx.agent(directory);
  await assert.doesNotReject(ctx.announce(agent));
  const unavailable = await ctx.command({ name: 'open_panel', arguments: {} }, agent);
  assert.equal(unavailable.kind, 'error');
  assert.match(unavailable.text, /MCP unavailable.*fixture MCP startup failed.*retry/);
  assert.deepEqual(ctx.disposed, ['mcp']);
  assert.ok(ctx.commandDefinition());
  ctx.failMcp = false;
  const retry = await ctx.command({ action: 'retry' }, agent);
  assert.equal(retry.kind, 'success');
  assert.deepEqual(JSON.parse(retry.text), { ready: true, workspaceRoot: directory });
  assert.equal((await ctx.command({ name: 'open_panel', arguments: {} }, agent)).kind, 'success');
  await ctx.unload();
  assert.equal(ctx.commandDefinition(), undefined);
  assert.equal(ctx.tools.get(prefix + 'open_panel', agent), undefined);
});

test('incomplete bundles and missing peers are visible through the retained command; strict startup cleans up', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const missing = join(directory, 'skills/sanmu-paper-reader');
  await rm(missing, { recursive: true });
  const ctx = fixtureContext();
  t.after(() => ctx.unload());
  await adapter.apply(ctx);
  assert.match((await ctx.command({ action: 'tools/list' }, ctx.agent(directory))).text, /incomplete bundle.*sanmu-paper-reader/);
  await mkdir(missing);
  await writeFile(join(missing, 'SKILL.md'), 'restored fixture');
  const strict = fixtureContext({ failMcp: true });
  t.after(() => strict.unload());
  await assert.rejects(adapter.apply(strict, { failOnStartupError: true }), /fixture MCP startup failed/);
  assert.deepEqual(strict.disposed, ['mcp', 'skills']);
  const noPeers = await fixturePackage(await temporary(t), { peers: false });
  const noPeerContext = fixtureContext();
  t.after(() => noPeerContext.unload());
  await noPeers.apply(noPeerContext);
  assert.match((await noPeerContext.command({ action: 'tools/list' }, noPeerContext.agent(directory))).text, /DeepSeek Harness must supply @deepseek-ai\/dsh-/);
  await assert.rejects(adapter.apply({ plugin() {} }), /missing DeepSeek tools\/skills provider APIs/);
});

test('packaged panel is returned unchanged in both layouts even when MCP startup fails', async t => {
  for (const shell of [false, true]) await t.test(shell ? 'expanded plugin cache' : 'repository package fallback', async t => {
    const directory = await temporary(t);
    const adapter = await fixturePackage(directory, { shell });
    await assert.rejects(adapter.readPackagedPanel({}, { pluginRoot: directory }), /missing assets\/panel\.html/);
    const panelDirectory = shell ? join(directory, 'assets') : join(directory, 'distribution/zcode/sanmu-paper-search/assets');
    await mkdir(panelDirectory, { recursive: true });
    const html = '<!doctype html>\n<html lang="zh"><body>完整三木面板<script>window.fixture = true;</script></body></html>\n';
    await writeFile(join(panelDirectory, 'panel.html'), html);
    const ctx = fixtureContext({ failMcp: true });
    ctx.profileContext = { cwd: directory };
    t.after(() => ctx.unload());
    await adapter.apply(ctx);
    const response = await ctx.command({ action: 'panel/read' }, ctx.agent(directory));
    assert.equal(response.kind, 'success');
    assert.deepEqual(JSON.parse(response.text), { html, version: manifest.version });
    assert.equal(ctx.executions.length, 0, 'static panel access must not require tool execution or MCP readiness');
    assert.equal(ctx.calls.filter(call => call.kind === 'mcp').length, 1);
  });
});

test('late MCP tool discovery clears the initial unavailable state without retry or another process', { timeout: 5000 }, async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const ctx = fixtureContext();
  ctx.profileContext = { cwd: directory };
  t.after(() => ctx.unload());
  const schemas = ctx.tools.schemas;
  let published = false;
  ctx.tools.schemas = agent => published ? schemas(agent) : [];
  await adapter.apply(ctx);
  const agent = ctx.agent(directory);
  const initial = await ctx.command({ action: 'tools/list' }, agent);
  assert.equal(initial.kind, 'error');
  assert.match(initial.text, /MCP startup did not register tools/);
  published = true;
  const recovered = await ctx.command({ action: 'tools/list' }, agent);
  assert.equal(recovered.kind, 'success');
  assert.equal(JSON.parse(recovered.text).tools.length, 4);
  assert.equal(ctx.calls.filter(call => call.kind === 'mcp').length, 1);
  assert.deepEqual(ctx.disposed, []);
});

test('native command returns canonical MCP values and schemas through the exact Agent policy pipeline', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const ctx = fixtureContext();
  ctx.profileContext = { cwd: directory };
  t.after(() => ctx.unload());
  await adapter.apply(ctx);
  const agent = ctx.agent(directory);
  const signal = new AbortController().signal;
  const canonicalResult = { content: [{ type: 'text', text: 'original payload' },
    { type: 'image', mimeType: 'image/png', data: 'fixture-base64' }], structuredContent: { value: [1, false, null] } };
  ctx.respond = async () => canonicalResult;
  const result = await ctx.command({ name: 'save_config', arguments: { exaApiKey: 'sensitive-fixture' } }, agent, { signal, commandId: 'command-123' });
  assert.equal(result.kind, 'success');
  assert.deepEqual(JSON.parse(result.text), canonicalResult);
  assert.equal(ctx.commandDefinition().recordInput, false);
  assert.deepEqual(ctx.executions[0], { name: prefix + 'save_config', callId: 'command-123', arguments: { exaApiKey: 'sensitive-fixture' }, agent, signal });
  const catalog = await ctx.command({ action: 'tools/list' }, agent);
  assert.equal(catalog.kind, 'success');
  const tools = JSON.parse(catalog.text).tools;
  assert.deepEqual(tools.map(tool => tool.name), ['open_panel', 'save_config', 'search_papers', 'doc2md']);
  assert.deepEqual(tools[0].inputSchema, { type: 'object', properties: {} });
  ctx.guards.push(exec => exec.agent === agent && exec.name === prefix + 'save_config' ? 'fixture policy denied saving credentials' : undefined);
  const denied = await ctx.command({ name: 'save_config', arguments: {} }, agent);
  assert.deepEqual(denied, { kind: 'error', text: 'fixture policy denied saving credentials' });
  const bad = await ctx.command('{"exaApiKey":"sensitive-fixture"', agent);
  assert.equal(bad.kind, 'error');
  assert.ok(!bad.text.includes('sensitive-fixture'));
  for (const request of [{ name: 'mcp__foreign__tool' }, { name: 'open_panel', arguments: [] }, { action: 'arbitrary' }, null]) {
    assert.equal((await ctx.command(request, agent)).kind, 'error');
  }
  const aborted = AbortSignal.abort(new Error('request cancelled'));
  assert.match((await ctx.command({ name: 'open_panel' }, agent, { signal: aborted })).text, /request cancelled/);
});

test('model and native commands use committed Session directories, share ancestor bridges, and rebind after cwd changes', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const ctx = fixtureContext();
  ctx.profileContext = { cwd: join(directory, 'launch-cwd') };
  t.after(() => ctx.unload());
  await adapter.apply(ctx);
  const agent = ctx.agent(join(directory, 'workspace-a'));
  await ctx.announce(agent);
  assert.equal(ctx.calls.filter(call => call.kind === 'mcp').length, 2);
  assert.equal(ctx.calls.at(-1).scope, agent);
  assert.equal(ctx.calls.at(-1).config.env.SANMU_WORKSPACE_ROOT, agent.session.header.cwd);
  const model = await ctx.tools.execute({ name: prefix + 'open_panel', callId: 'model-first-call', arguments: {}, agent, signal: new AbortController().signal });
  assert.equal(model.value.structuredContent.settings.workspaceRoot, agent.session.header.cwd);
  const child = ctx.agent(agent.session.header.cwd, agent);
  await ctx.announce(child);
  assert.equal(ctx.calls.filter(call => call.kind === 'mcp').length, 2, 'same-directory descendant reuses its visible ancestor bridge');
  assert.equal(ctx.tools.schemas(agent).filter(tool => tool.name.startsWith(prefix)).length, 4, 'scoped tools shadow globals');
  const other = ctx.agent(join(directory, 'workspace-b'));
  await ctx.announce(other);
  const native = await ctx.command({ name: 'open_panel' }, other);
  assert.equal(JSON.parse(native.text).structuredContent.settings.workspaceRoot, other.session.header.cwd);
  agent.session.currentCwd = join(directory, 'committed-cwd-after-change');
  const changed = await ctx.command({ name: 'open_panel' }, agent);
  assert.equal(JSON.parse(changed.text).structuredContent.settings.workspaceRoot, agent.session.currentCwd);
  assert.equal(ctx.calls.at(-1).config.cwd, agent.session.currentCwd);
  assert.equal(ctx.calls.at(-1).config.env.DSH_WORKSPACE_ROOT, agent.session.currentCwd);
  assert.equal(ctx.disposed.filter(kind => kind === 'mcp').length, 1);
  await other.ctx.unload();
  assert.equal(ctx.tools.get(prefix + 'open_panel', other), ctx.tools.get(prefix + 'open_panel'), 'agent disposal removes its scoped tools');
  await ctx.unload();
  assert.equal(ctx.tools.get(prefix + 'open_panel', agent), undefined);
  assert.equal(ctx.commandDefinition(), undefined);
});

test('retry waits for the current Session operation; unload removes command, scoped bridges and children', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const ctx = fixtureContext();
  ctx.profileContext = { cwd: directory };
  t.after(() => ctx.unload());
  await adapter.apply(ctx);
  const agent = ctx.agent(join(directory, 'session-workspace'));
  await ctx.announce(agent);
  const entered = Promise.withResolvers();
  const release = Promise.withResolvers();
  ctx.respond = async () => { entered.resolve(); await release.promise; return { content: [{ type: 'text', text: 'finished' }] }; };
  const operation = ctx.command({ name: 'search_papers', arguments: {} }, agent);
  await entered.promise;
  const retry = ctx.command({ action: 'retry' }, agent);
  await new Promise(setImmediate);
  assert.deepEqual(ctx.disposed, [], 'retry cannot close an in-flight operation');
  release.resolve();
  assert.equal((await operation).kind, 'success');
  assert.equal((await retry).kind, 'success');
  assert.deepEqual(ctx.disposed, ['mcp']);
  const handler = ctx.commandDefinition().handler;
  await ctx.unload();
  assert.equal(ctx.commandDefinition(), undefined);
  assert.equal(ctx.tools.schemas(agent).length, 0);
  assert.equal(ctx.disposed.filter(kind => kind === 'skills').length, 1);
  assert.equal(ctx.disposed.filter(kind => kind === 'mcp').length, 3);
  assert.match((await handler({ agent, rawInput: '{}', signal: new AbortController().signal })).text, /unloaded/);
});

test('explicit workspace and credential forwarding survive host env scrubbing', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  const config = adapter.createMcpConfig({
    DSH_HOME: join(directory, 'dsh-home'), DSH_WORKSPACE_ROOT: directory,
    USER_CONFIG_EXA_API_KEY: 'fixture-exa-key', SCIVERSE_API_TOKEN: 'fixture-sciverse-token',
    OPENALEX_API_KEY: '${user_config.openalexApiKey}', UNRELATED_SECRET: 'must-not-forward'
  });
  assert.equal(config.env.USER_CONFIG_EXA_API_KEY, 'fixture-exa-key');
  assert.equal(config.env.SCIVERSE_API_TOKEN, 'fixture-sciverse-token');
  assert.equal(config.env.OPENALEX_API_KEY, undefined);
  assert.equal(config.env.UNRELATED_SECRET, undefined);
  assert.equal(config.cwd, directory);
  assert.equal(config.env.DSH_PLUGIN_DATA, join(directory, 'dsh-home/plugin-data/sanmu-paper-search'));
  const session = { header: { cwd: join(directory, 'original-session-cwd') } };
  const current = join(directory, 'current-session-cwd');
  const scoped = adapter.createMcpConfig({ SANMU_WORKSPACE_ROOT: 'stale-ambient' }, { workspaceRoot: 'stale-config', env: { SANMU_WORKSPACE_ROOT: 'stale-env' } },
    { workingDirectory: { get: received => { assert.equal(received, session); return current; } } }, process, { session });
  assert.equal(scoped.cwd, current);
  assert.equal(scoped.env.SANMU_WORKSPACE_ROOT, current);
  assert.equal(scoped.env.DSH_WORKSPACE_ROOT, current);
});

test('authenticated workbench route enforces real Connection trust and signed-cookie checks', {
  skip: !runtimeAnchor && 'Set SANMU_DSH_TEST_RUNTIME for official Connection authentication checks', timeout: 20000
}, async t => {
  const directory = await temporary(t);
  const html = '<!doctype html><html><body>Unmodified packaged workbench<script>window.fullPanel = true;</script></body></html>\n';
  await mkdir(join(directory, 'assets'));
  await writeFile(join(directory, 'assets/panel.html'), html);
  await writeFile(join(directory, 'package.json'), JSON.stringify({ private: true, type: 'module', version: manifest.version }));
  const source = `
    import assert from 'node:assert/strict';
    import { createRequire } from 'node:module';
    import { pathToFileURL } from 'node:url';
    const require = createRequire(${JSON.stringify(runtimeAnchor)});
    const load = name => import(pathToFileURL(require.resolve(name)).href);
    const [{ Context }, Connection, adapter] = await Promise.all([
      load('@deepseek-ai/cordis'), load('@deepseek-ai/dsh-client-connection'), import(${JSON.stringify(pathToFileURL(adapterPath).href)})
    ]);
    const ctx = new Context();
    const routes = new Map();
    let credential;
    ctx.provide('credentials', { async modifyRecord(_key, update) {
      const next = await update(credential);
      if (next !== undefined) credential = next;
      return credential;
    } });
    ctx.provide('webServer', { host: '127.0.0.1', protocol: 'http:', register(route) {
      assert.ok(!routes.has(route.path));
      routes.set(route.path, route);
      return () => routes.delete(route.path);
    } });
    ctx.provide('tools', { async execute() { throw new Error('static resources must not execute model tools'); }, schemas() { return []; } });
    ctx.provide('skills', { registerProvider() {} });
    const reply = () => ({ status: 0, headers: {}, body: '', writeHead(status, headers = {}) {
      this.status = status; this.headers = headers;
    }, end(body = '') { this.body = body; } });
    try {
      await ctx.plugin(Connection);
      const fiber = ctx.plugin(adapter, { pluginRoot: ${JSON.stringify(directory)} });
      await fiber;
      const path = '/sanmu-paper-search/workbench';
      const route = routes.get(path);
      assert.equal(route.kind, 'exact');
      const authority = '127.0.0.1:32100';
      const request = async (headers = {}, method = 'GET', url = path) => {
        const response = reply();
        await route.handler({ method, url, headers: { host: authority, ...headers } }, response);
        assert.equal(response.headers['cache-control'], 'no-store');
        assert.equal(response.headers['access-control-allow-origin'], undefined);
        return response;
      };
      assert.equal((await request()).status, 401);
      assert.equal((await request({ cookie: 'dsh-auth-invalid=not-signed' })).status, 401);
      assert.equal((await request({ host: 'untrusted.example:32100' })).status, 403);
      assert.equal((await request({ origin: 'https://untrusted.example' })).status, 403);
      assert.equal((await request({ 'sec-fetch-site': 'cross-site' })).status, 403);
      const connection = ctx.get('connection');
      const login = new URL(connection.authenticatedUrl('http://' + authority + '/'));
      assert.equal((await request({}, 'GET', path + login.search)).status, 401, 'resource route never accepts launch-query authentication');
      const loginResponse = reply();
      assert.equal(connection.authorizeIndex({ method: 'GET', url: login.pathname + login.search, headers: { host: authority } }, loginResponse), false);
      assert.equal(loginResponse.status, 303);
      const cookie = loginResponse.headers['set-cookie'].split(';', 1)[0];
      const response = await request({ cookie, origin: 'http://' + authority });
      assert.equal(response.status, 200);
      assert.equal(response.headers['content-type'], 'application/json; charset=utf-8');
      assert.equal(response.headers['x-content-type-options'], 'nosniff');
      assert.deepEqual(JSON.parse(response.body), { html: ${JSON.stringify(html)}, version: ${JSON.stringify(manifest.version)} });
      const tampered = cookie.slice(0, -1) + (cookie.endsWith('x') ? 'y' : 'x');
      assert.equal((await request({ cookie: tampered })).status, 401);
      assert.equal((await request({ host: 'localhost:32100', cookie })).status, 401);
      assert.equal((await request({ cookie, origin: 'https://untrusted.example' })).status, 403);
      const post = await request({ cookie }, 'POST');
      assert.equal(post.status, 405);
      assert.equal(post.headers.allow, 'GET');
      await fiber.dispose();
      assert.equal(routes.has(path), false, 'plugin unload removes its authenticated resource route');
      console.log('official Connection: workbench Host/Origin/signed-cookie enforcement and route cleanup passed');
    } finally { await ctx.fiber.dispose(); }
  `;
  const result = await runNode(source, directory);
  assert.match(result.stdout, /signed-cookie enforcement and route cleanup passed/);
});

test('official runtime discovers packaged skills, executes real MCP via native command/policy, binds workspaces and disposes', {
  skip: !runtimeAnchor && 'Set SANMU_DSH_TEST_RUNTIME to an isolated official runtime or Desktop app.asar/dsh', timeout: 100000
}, async t => {
  const directory = await temporary(t);
  const workspace = join(directory, 'workspace');
  const secondWorkspace = join(directory, 'second workspace');
  const changedWorkspace = join(directory, 'changed workspace');
  await Promise.all([workspace, secondWorkspace, changedWorkspace].map(path => mkdir(path)));
  const bootFile = join(directory, 'cordis.yml');
  await writeFile(bootFile, '[]\n');
  await writeFile(join(directory, 'package.json'), JSON.stringify({ private: true, type: 'module' }));
  await mkdir(join(directory, 'node_modules'));
  await symlink(root, join(directory, 'node_modules', manifest.name), process.platform === 'win32' ? 'junction' : 'dir');
  const source = `
    import assert from 'node:assert/strict';
    import { createRequire } from 'node:module';
    import { pathToFileURL } from 'node:url';
    import { randomUUID } from 'node:crypto';
    const require = createRequire(${JSON.stringify(runtimeAnchor)});
    const load = name => import(pathToFileURL(require.resolve(name)).href);
    const [{ Context }, { default: SystemPrompt }, { default: Tools }, { default: Skills }, Boot, { Session }, Scope] = await Promise.all([
      load('@deepseek-ai/cordis'), load('@deepseek-ai/dsh-system-prompt'), load('@deepseek-ai/dsh-tools'), load('@deepseek-ai/dsh-skill'),
      load('@deepseek-ai/dsh-app-boot'), load('@deepseek-ai/dsh-session'), load('@deepseek-ai/dsh-scope')
    ]);
    const ctx = new Context();
    const profile = { cwd: ${JSON.stringify(workspace)}, home: process.env.DSH_HOME, dir: ${JSON.stringify(directory)}, installAnchor: ${JSON.stringify(runtimeAnchor)} };
    const commands = await load('@deepseek-ai/dsh-commands').catch(() => undefined);
    const cwdOf = new WeakMap();
    ctx.provide('profileContext', profile);
    ctx.provide('workingDirectory', { get: session => cwdOf.get(session) || session.header.cwd });
    const agentAt = cwd => {
      const id = randomUUID();
      const session = Session.create(id);
      cwdOf.set(session, cwd);
      const agent = { id, session, options: {} };
      agent.ctx = Scope.createScope(ctx, agent).ctx;
      return agent;
    };
    const announce = agent => ctx.serial(Scope.scopeTarget(agent, agent), 'agent/created', { agent, source: 'startup' });
    try {
      await ctx.plugin(SystemPrompt);
      await ctx.plugin(Tools);
      await ctx.plugin(Skills);
      if (commands) await ctx.plugin(commands.default);
      const entries = Boot.composeEntries([Boot.loadOverlayPatches('sanmu-test', ${JSON.stringify(join(root, 'cordis.patch.yml'))})]);
      assert.equal(entries.length, 1);
      assert.equal(entries[0].name, ${JSON.stringify(manifest.name)});
      const adapter = await import(${JSON.stringify(pathToFileURL(adapterPath).href)});
      const fiber = ctx.plugin(adapter);
      await fiber;
      const skills = await ctx.skills.list({ cwd: profile.cwd });
      assert.deepEqual(skills.map(skill => skill.name).sort(), ${JSON.stringify(expectedSkills)});
      assert.equal((await ctx.skills.get('sanmu-paper-reader', { cwd: profile.cwd })).resourceBase.path, ${JSON.stringify(join(root, 'skills/sanmu-paper-reader'))});
      const first = agentAt(profile.cwd);
      const second = agentAt(${JSON.stringify(secondWorkspace)});
      await announce(first);
      await announce(second);
      const result = await ctx.tools.execute({ name: '${prefix}open_panel', callId: 'model-call', arguments: {}, agent: second, signal: AbortSignal.timeout(20000) });
      assert.equal(result.isError, false, JSON.stringify(result));
      assert.equal(result.value.structuredContent.settings.workspaceRoot, ${JSON.stringify(secondWorkspace)});
      assert.equal(result.value.structuredContent.configStatus.exa_api_key.configured, true);
      assert.ok(!JSON.stringify(result).includes('SANMU_FIXTURE_EXA_KEY'));
      assert.equal(ctx.tools.schemas(second).filter(tool => tool.name.startsWith('${prefix}')).length,
        ctx.tools.schemas().filter(tool => tool.name.startsWith('${prefix}')).length);
      if (commands) {
        const run = request => ctx.commands.execute(second, '/sanmu ' + JSON.stringify(request), [], AbortSignal.timeout(20000));
        const list = await run({ action: 'tools/list' });
        assert.equal(list.result.kind, 'success', list.result.text);
        assert.ok(JSON.parse(list.result.text).tools.some(tool => tool.name === 'run_doc2md'));
        const saved = await run({ name: 'save_config', arguments: { exaApiKey: 'NEW_PRIVATE_API_KEY_FOR_TEST' } });
        assert.equal(saved.result.kind, 'success', saved.result.text);
        assert.ok(!JSON.stringify(second.session.ownEvents()).includes('NEW_PRIVATE_API_KEY_FOR_TEST'), 'recordInput:false keeps command payload out of Session log');
        const deny = ctx.tools.guard(exec => exec.agent === second && exec.name === '${prefix}save_config' ? 'test policy denies this operation' : undefined);
        const refused = await run({ name: 'save_config', arguments: {} });
        assert.equal(refused.result.kind, 'error');
        assert.match(refused.result.text, /test policy denies/);
        deny();
        cwdOf.set(second.session, ${JSON.stringify(changedWorkspace)});
        const moved = await run({ name: 'open_panel', arguments: {} });
        assert.equal(moved.result.kind, 'success', moved.result.text);
        assert.equal(JSON.parse(moved.result.text).structuredContent.settings.workspaceRoot, ${JSON.stringify(changedWorkspace)});
        console.log('official DSH: native command, private input logging, policy and workspace rebinding passed');
      }
      await fiber.dispose();
      assert.equal(ctx.tools.get('${prefix}open_panel', second), undefined);
      assert.equal(ctx.tools.get('${prefix}open_panel'), undefined);
      assert.deepEqual(await ctx.skills.list({ cwd: profile.cwd }), []);
      if (commands) assert.equal(ctx.commands.find(second, 'sanmu'), undefined);
      console.log('official DSH: root adapter -> three skills + real scoped MCP call -> disposal passed');
    } finally { await ctx.fiber.dispose(); }
    const booted = await Boot.boot('sanmu-test', ${JSON.stringify(bootFile)},
      Boot.loadOverlayPatches('sanmu-test', ${JSON.stringify(join(root, 'cordis.patch.yml'))}),
      async host => {
        host.provide('profileContext', profile);
        await host.plugin(SystemPrompt);
        await host.plugin(Tools);
        await host.plugin(Skills);
      }, pathToFileURL(${JSON.stringify(join(directory, 'package.json'))}).href);
    try {
      assert.deepEqual((await booted.skills.list({ cwd: profile.cwd })).map(skill => skill.name).sort(), ${JSON.stringify(expectedSkills)});
      assert.ok(booted.tools.get('${prefix}open_panel'));
      console.log('official DSH: Loader boot from package-name bundle passed');
    } finally { await booted.fiber.dispose(); }
  `;
  const result = await runNode(source, workspace, {
    DSH_HOME: join(directory, 'dsh-home'), DSH_PLUGIN_DATA: join(directory, 'plugin-data'),
    DSH_WORKSPACE_ROOT: workspace, USER_CONFIG_EXA_API_KEY: 'SANMU_FIXTURE_EXA_KEY',
    SANMU_PLUGIN_ROOT: root, SANMU_SKILLS_ROOT: join(root, 'skills')
  });
  assert.match(result.stdout, /disposal passed/);
  if (runtimeExecutable !== process.execPath) assert.match(result.stdout, /private input logging, policy and workspace rebinding passed/);
});

test('official install-spec parser accepts npm/git/tgz and refuses ZIP URLs', {
  skip: !runtimeRequire && 'Set SANMU_DSH_TEST_RUNTIME for official parser validation' || !process.env.SANMU_DSH_INSTALL_SPEC_SOURCE && 'Set SANMU_DSH_INSTALL_SPEC_SOURCE for official parser validation'
}, async () => {
  const sourcePath = process.env.SANMU_DSH_INSTALL_SPEC_SOURCE;
  if (!sourcePath) return;
  const { stripTypeScriptTypes } = await import('node:module');
  const code = stripTypeScriptTypes(await readFile(sourcePath, 'utf8'), { mode: 'transform' });
  const { parseInstallSpec } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.equal(parseInstallSpec(`sanmu-skills@${manifest.version}`).kind, 'registry');
  assert.equal(parseInstallSpec('git+https://github.com/luffysolution-svg/sanmu-skills.git#main').kind, 'git');
  assert.equal(parseInstallSpec(`https://example.test/sanmu-skills-${manifest.version}.tgz`).kind, 'tarball');
  assert.throws(() => parseInstallSpec(`https://example.test/sanmu-skills-${manifest.version}.zip`), /URL must point/);
});
