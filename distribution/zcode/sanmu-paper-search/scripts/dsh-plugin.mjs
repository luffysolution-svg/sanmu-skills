import { existsSync } from 'node:fs';
import { createRequire } from 'node:module';
import { homedir } from 'node:os';
import { dirname, join, resolve } from 'node:path';
import { fileURLToPath, pathToFileURL } from 'node:url';

export const name = 'sanmu-skills';
export const inject = ['tools', 'skills'];
export const skillNames = Object.freeze(['sanmu-paper-search', 'sanmu-doc2md', 'sanmu-paper-reader']);

const moduleDir = dirname(fileURLToPath(import.meta.url));
const credentialNames = [
  'POLITE_EMAIL', 'OPENALEX_EMAIL', 'CROSSREF_EMAIL', 'UNPAYWALL_EMAIL',
  'OPENALEX_API_KEY', 'S2_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY',
  'EXA_API_KEY', 'SERPER_API_KEY', 'EASYSCHOLAR_SECRET_KEY', 'AI4SCHOLAR_API_KEY',
  'SCIVERSE_API_KEY', 'SCIVERSE_API_TOKEN', 'MINERU_API_TOKEN',
  'SANMU_DATA_DIR', 'SANMU_PLUGIN_DATA', 'SANMU_WORKSPACE_ROOT', 'SANMU_PYTHON', 'SANMU_NO_AUTO_SETUP', 'SANMU_PANEL_WORKER',
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

/** Explicit env survives the official MCP client's credential and DSH_* scrub. */
export function createMcpConfig(hostEnv = process.env, config = {}, ctx) {
  const paths = resolvePackagePaths(hostEnv, config);
  const profile = service(ctx, 'profileContext');
  const agent = service(ctx, 'agent');
  const session = service(ctx, 'session') || agent?.session;
  const workspaceRoot = resolve(text(config.workspaceRoot) || text(session?.request?.cwd)
    || text(session?.header?.cwd) || text(agent?.options?.cwd) || text(hostEnv.SANMU_WORKSPACE_ROOT) || text(hostEnv.DSH_WORKSPACE_ROOT)
    || text(profile?.cwd) || process.cwd());
  const env = {};
  for (const key of credentialNames) if (text(hostEnv[key])) env[key] = hostEnv[key];
  for (const [key, value] of Object.entries(config.env || {})) {
    if (typeof value !== 'string') throw new TypeError(`sanmu-skills: env.${key} must be a string`);
    env[key] = value;
  }
  env.SANMU_HOST = 'dsh';
  env.SANMU_PLUGIN_ROOT = paths.pluginRoot;
  env.SANMU_SKILLS_ROOT = paths.skillsRoot;
  env.DSH_HOME = resolve(text(config.dshHome) || text(profile?.home) || text(hostEnv.DSH_HOME) || join(homedir(), '.dsh'));
  env.DSH_PLUGIN_DATA = resolve(text(config.dataDir) || text(hostEnv.DSH_PLUGIN_DATA)
    || join(env.DSH_HOME, 'plugin-data', 'sanmu-paper-search'));
  env.DSH_WORKSPACE_ROOT = workspaceRoot;
  return {
    serverName: 'sanmu-paper-search', transport: 'stdio',
    command: text(config.nodeCommand) || process.execPath,
    args: paths.cli ? [paths.cli, 'mcp'] : [paths.server || join(paths.pluginRoot, 'scripts', 'mcp-server.mjs')],
    cwd: workspaceRoot, env, toolCallTimeoutMs: config.toolCallTimeoutMs ?? 300000,
    failOnStartupError: config.failOnStartupError ?? true
  };
}

async function hostPlugin(ctx, specifier) {
  let failure;
  try { return await import(specifier); } catch (error) {
    if (error.code !== 'ERR_MODULE_NOT_FOUND' && error.code !== 'MODULE_NOT_FOUND') throw error;
    failure = error;
  }
  // Packaged Desktop and externally linked packages need the installation's
  // resolver rather than a search through arbitrary global node_modules trees.
  const profile = service(ctx, 'profileContext');
  const pkg = service(ctx, 'pluginPackages')?.packageOf?.(specifier, import.meta.url);
  const anchors = [pkg?.manifestPath, profile?.installAnchor,
    profile?.dir && join(profile.dir, 'package.json')].filter(Boolean);
  for (const anchor of new Set(anchors)) {
    let filename;
    try { filename = createRequire(anchor).resolve(specifier); } catch (error) {
      if (error.code !== 'MODULE_NOT_FOUND' && error.code !== 'ERR_MODULE_NOT_FOUND') throw error;
      continue;
    }
    return await import(pathToFileURL(filename).href);
  }
  throw new Error(`sanmu-skills: DeepSeek Harness must supply ${specifier}. Use a complete DeepSeek Harness >=0.2.2-alpha.2 installation and retain this bundle's optional peer dependencies.`, { cause: failure });
}

/** Mount both capabilities and await real startup; unload children on failure. */
export async function apply(ctx, config = {}) {
  const [major, minor] = process.versions.node.split('.').map(Number);
  if (major < 24 || major === 24 && minor < 9) {
    throw new Error('sanmu-skills: the DeepSeek adapter requires Node.js >=24.9.0 (the DeepSeek Harness runtime requirement). The standalone sanmu CLI supports Node.js >=22.20.0.');
  }
  if (typeof ctx?.plugin !== 'function' || typeof service(ctx, 'tools')?.register !== 'function'
    || typeof service(ctx, 'skills')?.registerProvider !== 'function') {
    throw new Error('sanmu-skills: missing DeepSeek tools/skills provider APIs; use a base-backed DeepSeek Harness >=0.2.2-alpha.2 profile.');
  }
  const paths = resolvePackagePaths(process.env, config);
  const missing = skillNames.filter(skill => !existsSync(join(paths.skillsRoot, skill, 'SKILL.md')));
  if (!paths.server || missing.length) {
    throw new Error(`sanmu-skills: incomplete bundle at ${paths.pluginRoot}; ${!paths.server ? 'missing MCP server; ' : ''}missing skills: ${missing.join(', ') || '(none)'}. Install the complete sanmu-skills package or the built three-skill plugin artifact.`);
  }
  const [mcpClient, skillFilesystem] = await Promise.all([
    hostPlugin(ctx, '@deepseek-ai/dsh-mcp-client'), hostPlugin(ctx, '@deepseek-ai/dsh-skill-filesystem')
  ]);
  if (typeof mcpClient.apply !== 'function' || typeof mcpClient.Config !== 'function'
    || typeof skillFilesystem.apply !== 'function' || typeof skillFilesystem.Config !== 'function') {
    throw new Error('sanmu-skills: unsupported DeepSeek MCP/skill-filesystem plugin API; upgrade DeepSeek Harness to >=0.2.2-alpha.2.');
  }
  const skillConfig = { providerName: 'sanmu-skills', includeDefaultRoots: false,
    bundledSkillDir: paths.skillsRoot, watch: config.watchSkills ?? false };
  const parsed = skillFilesystem.Config(skillConfig);
  if (parsed.providerName !== skillConfig.providerName || parsed.includeDefaultRoots !== false
    || parsed.bundledSkillDir !== paths.skillsRoot) {
    throw new Error('sanmu-skills: this DeepSeek skill-filesystem version cannot mount isolated packaged skills; upgrade DeepSeek Harness to >=0.2.2-alpha.2.');
  }
  const children = [];
  try {
    for (const [plugin, options] of [[skillFilesystem, skillConfig], [mcpClient, createMcpConfig(process.env, config, ctx)]]) {
      const child = ctx.plugin(plugin, options);
      children.push(child);
      if (typeof child?.then !== 'function' || typeof child?.dispose !== 'function') {
        throw new Error('sanmu-skills: DeepSeek ctx.plugin must return an awaitable lifecycle fiber; upgrade DeepSeek Harness to >=0.2.2-alpha.2.');
      }
      await child;
    }
  } catch (error) {
    for (const child of children.reverse()) await child?.dispose?.();
    throw error;
  }
}
