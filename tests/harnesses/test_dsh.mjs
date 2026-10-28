import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { cp, mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
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
const runtimeDir = process.env.SANMU_DSH_TEST_RUNTIME;
const runtimeAnchor = runtimeDir && join(resolve(runtimeDir), 'package.json');
const runtimeRequire = runtimeAnchor && createRequire(runtimeAnchor);

async function temporary(t) {
  const directory = await mkdtemp(join(tmpdir(), 'sanmu-dsh-test-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  return directory;
}

async function fixturePackage(directory, { shell = false, peers = true } = {}) {
  const adapter = shell ? join(directory, 'scripts/dsh-plugin.mjs')
    : join(directory, 'distribution/zcode/sanmu-paper-search/scripts/dsh-plugin.mjs');
  await mkdir(dirname(adapter), { recursive: true });
  await cp(adapterPath, adapter);
  await writeFile(join(dirname(adapter), 'mcp-server.mjs'), '// fixture path; fake peer owns startup\n');
  await writeFile(join(directory, 'package.json'), JSON.stringify({ name: 'sanmu-skills', type: 'module', version: '1.10.2' }));
  if (!shell) {
    await mkdir(join(directory, 'scripts'), { recursive: true });
    await writeFile(join(directory, 'scripts/sanmu.mjs'), '// shared CLI fixture\n');
  }
  for (const skill of expectedSkills) {
    await mkdir(join(directory, 'skills', skill), { recursive: true });
    await writeFile(join(directory, 'skills', skill, 'SKILL.md'), `---\nname: ${skill}\ndescription: ${skill} fixture\n---\n\nFixture body.\n`);
  }
  if (peers) {
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
  return await import(pathToFileURL(adapter).href);
}

function fixtureContext({ failMcp = false } = {}) {
  const calls = [];
  const disposed = [];
  const ctx = {
    tools: { register() {} }, skills: { registerProvider() {} }, calls, disposed,
    get(key) { return this[key]; },
    plugin(plugin, config) {
      const kind = 'serverName' in config ? 'mcp' : 'skills';
      calls.push({ kind, config, plugin });
      let complete = false;
      const promise = new Promise((resolvePromise, reject) => setImmediate(() => {
        complete = true;
        if (kind === 'mcp' && failMcp) reject(new Error('fixture MCP startup failed'));
        else resolvePromise();
      }));
      return { then: promise.then.bind(promise), async dispose() { disposed.push(kind); }, get complete() { return complete; } };
    }
  };
  return ctx;
}

async function runNode(source, directory, environment = {}) {
  const child = spawn(process.execPath, ['--input-type=module', '-e', source], {
    cwd: directory, env: { ...process.env, ...environment }, stdio: ['ignore', 'pipe', 'pipe'], windowsHide: true
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

test('DSH bundle manifests use installable patches and optional host peers', async () => {
  for (const directory of [root, canonical]) {
    const manifest = JSON.parse(await readFile(join(directory, 'package.json'), 'utf8'));
    assert.equal(manifest.version, '1.10.2');
    assert.equal(manifest.dsh.manifestVersion, 1);
    const patch = await readFile(join(directory, manifest.dsh.bundle.patch), 'utf8');
    assert.match(patch, /id: sanmu-skills/);
    const request = patch.match(/name:\s*["']([^"']+)["']/)?.[1];
    assert.ok(request?.startsWith('./'));
    assert.ok(existsSync(resolve(directory, request)));
    for (const peer of ['@deepseek-ai/dsh-mcp-client', '@deepseek-ai/dsh-skill-filesystem']) {
      assert.equal(manifest.peerDependenciesMeta[peer].optional, true);
      assert.match(manifest.peerDependencies[peer], /0\.2\.1-rc\.1/);
    }
  }
});

test('apply mounts all packaged skills and awaits MCP in root and expanded-shell layouts', async t => {
  for (const shell of [false, true]) await t.test(shell ? 'expanded plugin cache' : 'repository/npm package', async t => {
    const directory = await temporary(t);
    const workspace = join(directory, 'different workspace');
    await mkdir(workspace);
    const adapter = await fixturePackage(directory, { shell });
    const ctx = fixtureContext();
    ctx.session = { request: { cwd: workspace } };
    ctx.profileContext = { cwd: join(directory, 'incorrect-launch-cwd'), home: join(directory, 'isolated-dsh') };
    await adapter.apply(ctx, { env: { USER_CONFIG_EXA_API_KEY: 'fixture-key' } });
    assert.deepEqual(ctx.calls.map(call => call.kind), ['skills', 'mcp']);
    const skillConfig = ctx.calls[0].config;
    assert.equal(skillConfig.providerName, 'sanmu-skills');
    assert.equal(skillConfig.includeDefaultRoots, false);
    assert.equal(skillConfig.bundledSkillDir, join(directory, 'skills'));
    const mcpConfig = ctx.calls[1].config;
    assert.equal(mcpConfig.command, process.execPath);
    assert.equal(mcpConfig.cwd, workspace);
    assert.equal(mcpConfig.env.SANMU_PLUGIN_ROOT, directory);
    assert.equal(mcpConfig.env.SANMU_SKILLS_ROOT, join(directory, 'skills'));
    assert.equal(mcpConfig.env.USER_CONFIG_EXA_API_KEY, 'fixture-key');
    assert.equal(mcpConfig.failOnStartupError, true);
    assert.deepEqual(mcpConfig.args, shell ? [join(directory, 'scripts/mcp-server.mjs')] : [join(directory, 'scripts/sanmu.mjs'), 'mcp']);
    assert.deepEqual(ctx.disposed, []);
  });
});

test('apply rejects incomplete bundles and cleans up a failed MCP activation', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory);
  await rm(join(directory, 'skills/sanmu-paper-reader'), { recursive: true });
  await assert.rejects(adapter.apply(fixtureContext()), /incomplete bundle.*sanmu-paper-reader/);
  await mkdir(join(directory, 'skills/sanmu-paper-reader'));
  await writeFile(join(directory, 'skills/sanmu-paper-reader/SKILL.md'), 'restored fixture');
  const ctx = fixtureContext({ failMcp: true });
  await assert.rejects(adapter.apply(ctx), /fixture MCP startup failed/);
  assert.deepEqual(ctx.disposed, ['mcp', 'skills']);
  await assert.rejects(adapter.apply({ plugin() {} }), /missing DeepSeek tools\/skills provider APIs/);
});

test('apply reports a missing host peer without installing a package', async t => {
  const directory = await temporary(t);
  const adapter = await fixturePackage(directory, { peers: false });
  await assert.rejects(adapter.apply(fixtureContext()), /DeepSeek Harness must supply @deepseek-ai\/dsh-/);
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
});

test('official runtime loads root bundle, discovers three skills, calls real MCP and disposes', {
  skip: !runtimeAnchor && 'Set SANMU_DSH_TEST_RUNTIME to an isolated official runtime npm project', timeout: 100000
}, async t => {
  const directory = await temporary(t);
  const workspace = join(directory, 'workspace');
  await mkdir(workspace);
  const bootFile = join(directory, 'cordis.yml');
  await writeFile(bootFile, '[]\n');
  const source = `
    import assert from 'node:assert/strict';
    import { createRequire } from 'node:module';
    import { pathToFileURL } from 'node:url';
    import { join } from 'node:path';
    const require = createRequire(${JSON.stringify(runtimeAnchor)});
    const load = name => import(pathToFileURL(require.resolve(name)).href);
    const [{ Context }, { default: SystemPrompt }, { default: Tools }, { default: Skills }, Boot] = await Promise.all([
      load('@deepseek-ai/cordis'), load('@deepseek-ai/dsh-system-prompt'), load('@deepseek-ai/dsh-tools'), load('@deepseek-ai/dsh-skill'), load('@deepseek-ai/dsh-app-boot')
    ]);
    const ctx = new Context();
    const profile = { cwd: ${JSON.stringify(workspace)}, home: process.env.DSH_HOME, dir: ${JSON.stringify(runtimeDir)}, installAnchor: ${JSON.stringify(runtimeAnchor)} };
    ctx.provide('profileContext', profile);
    try {
      await ctx.plugin(SystemPrompt);
      await ctx.plugin(Tools);
      await ctx.plugin(Skills);
      const entries = Boot.composeEntries([Boot.loadOverlayPatches('sanmu-test', ${JSON.stringify(join(root, 'cordis.patch.yml'))})]);
      assert.equal(entries.length, 1);
      assert.match(entries[0].name, /^file:/);
      const adapter = await import(entries[0].name);
      const fiber = ctx.plugin(adapter);
      await fiber;
      const skills = await ctx.skills.list({ cwd: profile.cwd });
      assert.deepEqual(skills.map(skill => skill.name).sort(), ${JSON.stringify(expectedSkills)});
      assert.equal((await ctx.skills.get('sanmu-paper-reader', { cwd: profile.cwd })).resourceBase.path, ${JSON.stringify(join(root, 'skills/sanmu-paper-reader'))});
      const open = ctx.tools.get('mcp__sanmu-paper-search__open_panel');
      assert.ok(open, 'MCP tool discovery must finish before apply activates');
      const result = await open.execute({}, { signal: AbortSignal.timeout(20000) });
      assert.equal(result.structuredContent.settings.workspaceRoot, profile.cwd);
      assert.equal(result.structuredContent.configStatus.exa_api_key.configured, true);
      assert.ok(!JSON.stringify(result).includes('SANMU_FIXTURE_EXA_KEY'));
      await fiber.dispose();
      assert.equal(ctx.tools.get('mcp__sanmu-paper-search__open_panel'), undefined);
      assert.deepEqual(await ctx.skills.list({ cwd: profile.cwd }), []);
      console.log('official DSH: root patch -> apply -> three skills + real MCP call -> disposal passed');
    } finally { await ctx.fiber.dispose(); }
    const booted = await Boot.boot('sanmu-test', ${JSON.stringify(bootFile)},
      Boot.loadOverlayPatches('sanmu-test', ${JSON.stringify(join(root, 'cordis.patch.yml'))}),
      async host => {
        host.provide('profileContext', profile);
        await host.plugin(SystemPrompt);
        await host.plugin(Tools);
        await host.plugin(Skills);
      }, pathToFileURL(${JSON.stringify(runtimeAnchor)}).href);
    try {
      assert.deepEqual((await booted.skills.list({ cwd: profile.cwd })).map(skill => skill.name).sort(), ${JSON.stringify(expectedSkills)});
      assert.ok(booted.tools.get('mcp__sanmu-paper-search__open_panel'));
      console.log('official DSH: Loader boot from root bundle passed');
    } finally { await booted.fiber.dispose(); }
  `;
  const result = await runNode(source, workspace, {
    DSH_HOME: join(directory, 'dsh-home'), DSH_PLUGIN_DATA: join(directory, 'plugin-data'),
    DSH_WORKSPACE_ROOT: workspace, USER_CONFIG_EXA_API_KEY: 'SANMU_FIXTURE_EXA_KEY',
    SANMU_PLUGIN_ROOT: root, SANMU_SKILLS_ROOT: join(root, 'skills')
  });
  assert.match(result.stdout, /disposal passed/);
});

test('official install-spec parser accepts npm/git/tgz and refuses ZIP URLs', {
  skip: !runtimeRequire && 'Set SANMU_DSH_TEST_RUNTIME for official parser validation'
}, async () => {
  // Published plugin-manager keeps install-spec private; use the pinned source
  // only when the caller supplies it, without duplicating its parser in a test.
  const sourcePath = process.env.SANMU_DSH_INSTALL_SPEC_SOURCE;
  if (!sourcePath) return;
  const { stripTypeScriptTypes } = await import('node:module');
  const code = stripTypeScriptTypes(await readFile(sourcePath, 'utf8'), { mode: 'transform' });
  const { parseInstallSpec } = await import('data:text/javascript;base64,' + Buffer.from(code).toString('base64'));
  assert.equal(parseInstallSpec('sanmu-skills@1.10.2').kind, 'registry');
  assert.equal(parseInstallSpec('git+https://github.com/luffysolution-svg/sanmu-skills.git#main').kind, 'git');
  assert.equal(parseInstallSpec('https://example.test/sanmu-skills-1.10.2.tgz').kind, 'tarball');
  assert.throws(() => parseInstallSpec('https://example.test/sanmu-skills-1.10.2.zip'), /URL must point/);
});
