import { homedir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

export function createMcpConfig(hostEnv = process.env) {
  const env = {};
  const names = [
    'POLITE_EMAIL', 'OPENALEX_EMAIL', 'CROSSREF_EMAIL', 'UNPAYWALL_EMAIL',
    'OPENALEX_API_KEY', 'S2_API_KEY', 'SEMANTIC_SCHOLAR_API_KEY',
    'EXA_API_KEY', 'SERPER_API_KEY', 'EASYSCHOLAR_SECRET_KEY', 'AI4SCHOLAR_API_KEY',
    'USER_CONFIG_POLITE_EMAIL', 'USER_CONFIG_OPENALEX_API_KEY', 'USER_CONFIG_S2_API_KEY',
    'USER_CONFIG_EXA_API_KEY', 'USER_CONFIG_SERPER_API_KEY',
    'USER_CONFIG_EASYSCHOLAR_SECRET_KEY', 'USER_CONFIG_AI4SCHOLAR_API_KEY',
    'USER_CONFIG_ZOTERO_URL', 'USER_CONFIG_EXPORT_SUBDIR'
  ];
  for (const name of names) if (hostEnv[name]) env[name] = hostEnv[name];
  env.SANMU_PLUGIN_ROOT = fileURLToPath(new URL('../', import.meta.url));
  env.DSH_HOME = hostEnv.DSH_HOME || join(homedir(), '.dsh');
  env.DSH_PLUGIN_DATA = hostEnv.DSH_PLUGIN_DATA || join(env.DSH_HOME, 'plugin-data', 'sanmu-paper-search');
  env.DSH_WORKSPACE_ROOT = hostEnv.DSH_WORKSPACE_ROOT || process.cwd();
  return {
    serverName: 'sanmu-paper-search', transport: 'stdio', command: 'node',
    args: [fileURLToPath(new URL('./mcp-server.mjs', import.meta.url))],
    cwd: env.DSH_WORKSPACE_ROOT, env, toolCallTimeoutMs: 300000, failOnStartupError: false
  };
}

export async function apply(ctx) {
  const mcpClient = await import('@deepseek-ai/dsh-mcp-client');
  ctx.plugin(mcpClient, createMcpConfig());
}
