import { existsSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, isAbsolute, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const name = 'sanmu-skills';
export const inject = ['tools', 'skills'];
export const skillNames = Object.freeze(['sanmu-paper-search', 'sanmu-doc2md', 'sanmu-paper-reader']);

const toolPrefix = 'mcp__sanmu-paper-search__';
const moduleDir = dirname(fileURLToPath(import.meta.url));
const credentialNames = [
  'POLITE_EMAIL', 'OPENALEX_EMAIL', 'CROSSREF_EMAIL', 'UNPAYWALL_EMAIL',
  'OPENALEX_API_KEY', 'S2_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY',
  'EXA_API_KEY', 'SERPER_API_KEY', 'EASYSCHOLAR_SECRET_KEY', 'AI4SCHOLAR_API_KEY',
  'SCIVERSE_API_KEY', 'SCIVERSE_API_TOKEN', 'MINERU_API_TOKEN',
  'SANMU_DATA_DIR', 'SANMU_PLUGIN_DATA', 'SANMU_PYTHON', 'SANMU_NO_AUTO_SETUP', 'SANMU_PANEL_WORKER',
  'HTTP_PROXY', 'HTTPS_PROXY', 'ALL_PROXY', 'NO_PROXY', 'NODE_EXTRA_CA_CERTS', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
  'USER_CONFIG_POLITE_EMAIL', 'USER_CONFIG_OPENALEX_API_KEY', 'USER_CONFIG_S2_API_KEY',
  'USER_CONFIG_EXA_API_KEY', 'USER_CONFIG_SERPER_API_KEY',
  'USER_CONFIG_EASYSCHOLAR_SECRET_KEY', 'USER_CONFIG_AI4SCHOLAR_API_KEY',
  'USER_CONFIG_SCIVERSE_API_KEY', 'USER_CONFIG_MINERU_API_TOKEN',
  'USER_CONFIG_ZOTERO_URL', 'USER_CONFIG_EXPORT_SUBDIR', 'USER_CONFIG_DOC2MD_SUBDIR'
];

function text(value) {
  return typeof value === 'string' && value.trim() && !/\$\{[^}]+\}/.test(value) ? value.trim() : undefined;
}

function service(ctx, key) {
  return typeof ctx?.get === 'function' ? ctx.get(key) : ctx?.[key];
}

function errorText(error) {
  const message = error instanceof Error ? error.message : String(error);
  return error?.cause ? `${message}: ${errorText(error.cause)}` : message;
}

function serverPath(root) {
  return [join(root, 'scripts', 'mcp-server.mjs'),
    join(root, 'distribution', 'zcode', 'sanmu-paper-search', 'scripts', 'mcp-server.mjs')]
    .find(path => existsSync(path));
}

/** Resolve assets from the installed package, never from the host process cwd. */
export function resolvePackagePaths(hostEnv = process.env, config = {}) {
  const explicitRoot = text(config.pluginRoot) || text(hostEnv.SANMU_PLUGIN_ROOT);
  const candidates = explicitRoot ? [resolve(explicitRoot)] : [resolve(moduleDir, '..'), resolve(moduleDir, '../../../..')];
  const pluginRoot = candidates.find(root => serverPath(root) && existsSync(join(root, 'skills')))
    || candidates.find(root => serverPath(root)) || candidates[0];
  const skillsRoot = resolve(text(config.skillsRoot) || text(hostEnv.SANMU_SKILLS_ROOT) || join(pluginRoot, 'skills'));
  const cli = join(pluginRoot, 'scripts', 'sanmu.mjs');
  const server = serverPath(pluginRoot);
  return { pluginRoot, skillsRoot, server, cli: existsSync(cli) ? cli : undefined };
}

/** Return the bundled workbench unchanged so the native sidebar uses the same UI. */
export async function readPackagedPanel(hostEnv = process.env, config = {}) {
  const { pluginRoot } = resolvePackagePaths(hostEnv, config);
  const panel = [join(pluginRoot, 'assets', 'panel.html'),
    join(pluginRoot, 'distribution', 'zcode', 'sanmu-paper-search', 'assets', 'panel.html')]
    .find(path => existsSync(path));
  if (!panel) throw new Error('sanmu-skills: the installed bundle is missing assets/panel.html.');
  const [html, manifestText] = await Promise.all([
    readFile(panel, 'utf8'), readFile(join(pluginRoot, 'package.json'), 'utf8')
  ]);
  const version = text(JSON.parse(manifestText).version);
  if (!version) throw new Error('sanmu-skills: the installed package manifest has no version.');
  return { html, version };
}

/** Desktop's supported Electron runtime is Node 22.20, even when the CLI requires Node 24. */
export function assertSupportedRuntime(versions = process.versions) {
  const [major, minor] = String(versions.node).split('.').map(Number);
  if (!Number.isInteger(major) || !Number.isInteger(minor) || major < 22 || major === 22 && minor < 20) {
    throw new Error('sanmu-skills: Node.js >=22.20.0 is required, including the bundled DeepSeek Harness Desktop Electron runtime.');
  }
}

/** A Desktop package-manager invocation contains an executable plus a pnpm script, not Node flags. */
export function resolveNodeInvocation(hostEnv = process.env, config = {}, ctx, runtime = process) {
  const packaged = service(ctx, 'profileContext')?.packageManager;
  const desktop = packaged?.env?.ELECTRON_RUN_AS_NODE === '1';
  const env = desktop ? { ...packaged.env } : {};
  const command = text(config.nodeCommand) || (desktop
    ? text(packaged.env.DSH_DESKTOP_NODE_EXECUTABLE) || text(packaged.command) : undefined) || runtime.execPath;
  if (runtime.versions?.electron || desktop || hostEnv.ELECTRON_RUN_AS_NODE === '1') env.ELECTRON_RUN_AS_NODE = '1';
  return { command, env };
}

export function resolveWorkspace(ctx, config = {}, hostEnv = process.env, agent = service(ctx, 'agent')) {
  const session = agent?.session || service(ctx, 'session');
  const committed = session && service(ctx, 'workingDirectory')?.get?.(session);
  return resolve(text(committed) || text(session?.header?.cwd) || text(session?.request?.cwd)
    || text(config.workspaceRoot) || text(hostEnv.SANMU_WORKSPACE_ROOT) || text(hostEnv.DSH_WORKSPACE_ROOT)
    || text(service(ctx, 'profileContext')?.cwd) || process.cwd());
}

/** Explicit env survives the official MCP client's credential and DSH_* scrub. */
export function createMcpConfig(hostEnv = process.env, config = {}, ctx, runtime = process, agent) {
  const paths = resolvePackagePaths(hostEnv, config);
  const profile = service(ctx, 'profileContext');
  const workspaceRoot = resolveWorkspace(ctx, config, hostEnv, agent);
  const node = resolveNodeInvocation(hostEnv, config, ctx, runtime);
  const env = { ...node.env };
  for (const key of credentialNames) if (text(hostEnv[key])) env[key] = hostEnv[key];
  for (const [key, value] of Object.entries(config.env || {})) {
    if (typeof value !== 'string') throw new TypeError(`sanmu-skills: env.${key} must be a string`);
    env[key] = value;
  }
  // A stale ambient SANMU_WORKSPACE_ROOT must never override this Session's directory.
  env.SANMU_HOST = 'dsh';
  env.SANMU_PLUGIN_ROOT = paths.pluginRoot;
  env.SANMU_SKILLS_ROOT = paths.skillsRoot;
  env.SANMU_WORKSPACE_ROOT = workspaceRoot;
  env.DSH_HOME = resolve(text(config.dshHome) || text(profile?.home) || text(hostEnv.DSH_HOME) || join(homedir(), '.dsh'));
  env.DSH_PLUGIN_DATA = resolve(text(config.dataDir) || text(hostEnv.DSH_PLUGIN_DATA)
    || join(env.DSH_HOME, 'plugin-data', 'sanmu-paper-search'));
  env.DSH_WORKSPACE_ROOT = workspaceRoot;
  if (node.env.ELECTRON_RUN_AS_NODE === '1') env.ELECTRON_RUN_AS_NODE = '1';
  return {
    serverName: 'sanmu-paper-search', transport: 'stdio', command: node.command,
    args: paths.cli ? [paths.cli, 'mcp'] : [paths.server || join(paths.pluginRoot, 'scripts', 'mcp-server.mjs')],
    cwd: workspaceRoot, env, toolCallTimeoutMs: config.toolCallTimeoutMs ?? 300000,
    failOnStartupError: config.failOnStartupError ?? false
  };
}

export async function hostPlugin(ctx, specifier, runtime = process) {
  let failure;
  try { return await import(specifier); } catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND' && error.code !== 'MODULE_NOT_FOUND') throw error;
    failure = error;
  }
  // Use only launch-supplied resolution anchors. Electron can resolve files
  // inside app.asar, so do not search global installations or unpack the app.
  const profile = service(ctx, 'profileContext');
  const anchors = [profile?.installAnchor, profile?.dir && join(profile.dir, 'package.json'),
    runtime.versions?.electron && runtime.argv?.[1],
    runtime.versions?.electron && runtime.argv?.[2] && join(runtime.argv[2], 'package.json')]
    .filter(anchor => typeof anchor === 'string' && isAbsolute(anchor));
  for (const anchor of new Set(anchors)) {
    let filename;
    try { filename = createRequire(anchor).resolve(specifier); } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND' && error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
      continue;
    }
    return await import(pathToFileURL(filename).href);
  }
  throw new Error(`sanmu-skills: DeepSeek Harness must supply ${specifier}. Use a complete DeepSeek Harness >=0.2.1-rc.3 installation and retain this bundle's optional peer dependencies.`, { cause: failure });
}

/** Keep MCP/skills failures contained while retaining a native command for recovery. */
export async function apply(ctx, config = {}) {
  if (typeof ctx?.plugin !== 'function' || typeof ctx?.effect !== 'function' || typeof ctx?.on !== 'function'
    || typeof service(ctx, 'tools')?.execute !== 'function' || typeof service(ctx, 'tools')?.schemas !== 'function'
    || typeof service(ctx, 'skills')?.registerProvider !== 'function') {
    throw new Error('sanmu-skills: missing DeepSeek tools/skills provider APIs; use a base-backed DeepSeek Harness >=0.2.1-rc.3 profile.');
  }
  const tools = service(ctx, 'tools');
  const children = new Set();
  const bridges = new Set();
  const definitions = new WeakMap();
  const sessions = new Map();
  let mcpClient;
  let startupError;
  let stopped = false;
  let globalBridge;
  let initialize;
  const unavailable = error => new Error(`sanmu-skills: MCP unavailable${error ? `: ${errorText(error)}` : ''}. Retry with /sanmu {"action":"retry"}.`);
  const check = signal => {
    signal?.throwIfAborted();
    if (stopped) throw new Error('sanmu-skills: plugin is unloaded.');
  };
  const disposeChild = async child => {
    if (!child) return;
    children.delete(child);
    await child.dispose();
  };
  const mount = (owner, plugin, options) => {
    const child = owner.plugin(plugin, options);
    if (typeof child?.then !== 'function' || typeof child?.dispose !== 'function') {
      throw new Error('sanmu-skills: DeepSeek ctx.plugin must return an awaitable lifecycle fiber.');
    }
    children.add(child);
    return child;
  };
  function refresh(bridge) {
    bridge.names = [];
    for (const schema of tools.schemas(bridge.agent)) {
      if (!schema.name.startsWith(toolPrefix)) continue;
      const definition = tools.get(schema.name, bridge.agent);
      if (!definition) continue;
      if (!definitions.has(definition)) definitions.set(definition, bridge);
      if (definitions.get(definition) === bridge) bridge.names.push(schema.name);
    }
    if (bridge.names.length) bridge.error = undefined;
  }
  async function startBridge(owner, agent) {
    const options = createMcpConfig(process.env, config, ctx, process, agent);
    const bridge = { agent, workspace: options.cwd, names: [] };
    bridges.add(bridge);
    try {
      bridge.child = mount(owner, mcpClient, options);
      await bridge.child;
      refresh(bridge);
      if (!bridge.names.length) bridge.error = new Error('MCP startup did not register tools; check the Harness plugin log.');
    } catch (error) {
      bridge.error = error;
      await disposeChild(bridge.child);
      bridge.child = undefined;
    }
    return bridge;
  }
  function sessionState(agent) {
    if (!agent?.session || typeof agent.ctx?.plugin !== 'function' || typeof agent.ctx?.effect !== 'function') {
      throw new Error('sanmu-skills: this operation requires an invoking Harness Session.');
    }
    let state = sessions.get(agent);
    if (state) return state;
    state = { agent, queue: Promise.resolve(), closed: false };
    sessions.set(agent, state);
    state.release = agent.ctx.effect(() => async () => {
      state.closed = true;
      await disposeChild(state.own?.child);
      bridges.delete(state.own);
      sessions.delete(agent);
    }, 'sanmu-skills.session');
    return state;
  }
  function inSession(agent, signal, operation) {
    const state = sessionState(agent);
    const result = state.queue.then(async () => {
      check(signal);
      if (state.closed) throw new Error('sanmu-skills: the invoking Session is disposed.');
      return await operation(state);
    });
    state.queue = result.catch(() => {});
    return result;
  }
  async function ensure(state, signal, retry = false) {
    await initialize;
    check(signal);
    if (startupError) throw unavailable(startupError);
    const workspace = resolveWorkspace(ctx, config, process.env, state.agent);
    // Oldest scopes first: a nested agent can share its parent's bridge when
    // the inherited tool definitions already target the same directory.
    for (const bridge of bridges) refresh(bridge);
    if (state.own && (retry || state.own.workspace !== workspace)) {
      await disposeChild(state.own.child);
      bridges.delete(state.own);
      state.own = undefined;
    }
    if (!state.own && !retry) {
      for (const schema of tools.schemas(state.agent)) {
        if (!schema.name.startsWith(toolPrefix)) continue;
        const inherited = definitions.get(tools.get(schema.name, state.agent));
        if (inherited?.workspace === workspace && !inherited.error) return inherited;
      }
      if (globalBridge?.workspace === workspace && !globalBridge.names.length) throw unavailable(globalBridge.error);
    }
    if (!state.own) state.own = await startBridge(state.agent.ctx, state.agent);
    check(signal);
    if (state.closed) {
      await disposeChild(state.own.child);
      throw new Error('sanmu-skills: the invoking Session is disposed.');
    }
    if (state.own.error || !state.own.names.length) throw unavailable(state.own.error);
    return state.own;
  }
  ctx.effect(() => async () => {
    stopped = true;
    const activeSessions = [...sessions.values()];
    await Promise.allSettled(activeSessions.map(state => state.release()));
    await Promise.allSettled([...children].map(disposeChild));
    await Promise.allSettled(activeSessions.map(state => state.queue));
    sessions.clear();
    bridges.clear();
  }, 'sanmu-skills.children');

  const command = {
    name: 'sanmu', description: '三木科研工作台：检索、文档解析、配置和环境恢复',
    input: { hint: '{"name":"open_panel","arguments":{}}' }, recordInput: false,
    async handler({ agent, rawInput, commandId, signal }) {
      try {
        check(signal);
        let request;
        try { request = JSON.parse(rawInput); } catch { throw new Error('sanmu-skills: expected a JSON command object.'); }
        if (!request || typeof request !== 'object' || Array.isArray(request)) throw new Error('sanmu-skills: expected a JSON command object.');
        if (request.action !== undefined) {
          if (!['tools/list', 'retry', 'panel/read'].includes(request.action) || request.name !== undefined || request.arguments !== undefined) {
            throw new Error('sanmu-skills: supported command actions are tools/list, retry and panel/read.');
          }
          if (request.action === 'panel/read') {
            const panel = await readPackagedPanel(process.env, config);
            check(signal);
            return { kind: 'success', text: JSON.stringify(panel) };
          }
          const result = await inSession(agent, signal, async state => {
            if (request.action === 'retry' && startupError) {
              initialize = initializeHost();
              await initialize;
            }
            await ensure(state, signal, request.action === 'retry');
            return request.action === 'retry' ? { ready: true, workspaceRoot: resolveWorkspace(ctx, config, process.env, agent) }
              : { tools: tools.schemas(agent).filter(tool => tool.name.startsWith(toolPrefix)).map(tool => ({
                name: tool.name.slice(toolPrefix.length), description: tool.description, inputSchema: tool.parameters
              })) };
          });
          return { kind: 'success', text: JSON.stringify(result) };
        }
        if (typeof request.name !== 'string' || !/^[A-Za-z0-9_-]+$/.test(request.name)
          || request.name.startsWith('mcp__')) throw new Error('sanmu-skills: name must be an unqualified Sanmu tool name.');
        const args = request.arguments ?? {};
        if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('sanmu-skills: arguments must be a JSON object.');
        const outcome = await tools.execute({ name: toolPrefix + request.name, callId: commandId, arguments: args, agent, signal });
        if (outcome.isError) return { kind: 'error', text: outcome.error?.message || outcome.content?.filter(block => block.type === 'text').map(block => block.text).join('\n') || 'Sanmu tool failed.' };
        return { kind: 'success', text: JSON.stringify(outcome.value) };
      } catch (error) { return { kind: 'error', text: errorText(error) }; }
    }
  };
  // Optional in CLI/headless profiles; a native Desktop command is available
  // before MCP startup settles and remains registered after startup failure.
  ctx.inject(['commands'], inner => inner.commands.register(command));
  ctx.inject(['webServer', 'connection'], inner => inner.webServer.register({
    kind: 'exact', path: '/sanmu-paper-search/workbench',
    async handler(request, response) {
      const send = (status, payload, extraHeaders = {}) => {
        response.writeHead(status, { 'content-type': 'application/json; charset=utf-8',
          'cache-control': 'no-store', 'x-content-type-options': 'nosniff', ...extraHeaders });
        response.end(JSON.stringify(payload));
      };
      try {
        const rejection = inner.connection.requestRejection(request);
        if (rejection !== undefined) {
          send(rejection, { error: rejection === 401 ? 'Authentication required.' : 'Request origin is not allowed.' });
          return;
        }
        if (request.method !== 'GET') {
          send(405, { error: 'Method not allowed.' }, { allow: 'GET' });
          return;
        }
        check();
        const panel = await readPackagedPanel(process.env, config);
        check();
        send(200, panel);
      } catch (error) { send(500, { error: errorText(error) }); }
    }
  }));
  ctx.on('tools/execute', async (exec, next) => {
    if (!exec.name.startsWith(toolPrefix) || !exec.agent) return next();
    return inSession(exec.agent, exec.signal, async state => {
      await ensure(state, exec.signal);
      return next();
    });
  });
  ctx.on('agent/created', async ({ agent, signal }) => {
    try { await inSession(agent, signal, state => ensure(state, signal)); }
    catch (error) { if (!signal?.aborted && !stopped) ctx.logger?.warn?.(errorText(error)); }
  });

  async function initializeHost() {
    startupError = undefined;
    try {
      assertSupportedRuntime();
      const paths = resolvePackagePaths(process.env, config);
      const missing = skillNames.filter(skill => !existsSync(join(paths.skillsRoot, skill, 'SKILL.md')));
      if (!paths.server || missing.length) throw new Error(`sanmu-skills: incomplete bundle at ${paths.pluginRoot}; ${!paths.server ? 'missing MCP server; ' : ''}missing skills: ${missing.join(', ') || '(none)'}. Install the complete sanmu-skills package or the built three-skill plugin artifact.`);
      const [mcp, skillFilesystem] = await Promise.all([
        hostPlugin(ctx, '@deepseek-ai/dsh-mcp-client'), hostPlugin(ctx, '@deepseek-ai/dsh-skill-filesystem')
      ]);
      if (typeof mcp.apply !== 'function' || typeof mcp.Config !== 'function'
        || typeof skillFilesystem.apply !== 'function' || typeof skillFilesystem.Config !== 'function') {
        throw new Error('sanmu-skills: unsupported DeepSeek MCP/skill-filesystem plugin API; upgrade DeepSeek Harness to >=0.2.1-rc.3.');
      }
      mcpClient = mcp;
      const skillConfig = { providerName: 'sanmu-skills', includeDefaultRoots: false,
        bundledSkillDir: paths.skillsRoot, watch: config.watchSkills ?? false };
      const parsed = skillFilesystem.Config(skillConfig);
      if (parsed.providerName !== skillConfig.providerName || parsed.includeDefaultRoots !== false
        || parsed.bundledSkillDir !== paths.skillsRoot) throw new Error('sanmu-skills: this DeepSeek skill-filesystem version cannot mount isolated packaged skills.');
      await mount(ctx, skillFilesystem, skillConfig);
      check();
      globalBridge = await startBridge(ctx);
      if (globalBridge.error && config.failOnStartupError) throw globalBridge.error;
      if (globalBridge.error) ctx.logger?.warn?.(errorText(unavailable(globalBridge.error)));
    } catch (error) {
      startupError = error;
      await Promise.allSettled([...children].map(disposeChild));
      bridges.clear();
      globalBridge = undefined;
      ctx.logger?.warn?.(errorText(unavailable(error)));
      if (config.failOnStartupError) throw error;
    }
  }
  initialize = initializeHost();
  await initialize;
}
