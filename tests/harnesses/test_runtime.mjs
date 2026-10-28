import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, mkdirSync, writeFileSync, existsSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import { resolveRuntimePaths, resolveConfigEnvironment, resolvePythonRuntime, ensureRuntime, runProcess } from "../../scripts/runtime.mjs";
const root = new URL("../../", import.meta.url);
const cli = new URL("../../scripts/sanmu.mjs", import.meta.url);
const temp = t => { const dir = mkdtempSync(join(tmpdir(), "sanmu-runtime-")); t.after(() => rmSync(dir, { recursive: true, force: true })); return dir; };

test("runtime paths ignore unresolved host placeholders and preserve explicit Unicode paths", t => {
  const dir = temp(t);
  const paths = resolveRuntimePaths({ SANMU_DATA_DIR: join(dir, "数据 space"), SANMU_PLUGIN_ROOT: "${PLUGIN_ROOT}", SANMU_WORKSPACE_ROOT: dir }, dir);
  assert.equal(paths.pluginRoot, dir);
  assert.equal(paths.dataDir, join(dir, "数据 space"));
  assert.equal(paths.workspaceRoot, dir);
});

test("panel credentials override user config and environment without leaking unrelated properties", t => {
  const dir = temp(t);
  writeFileSync(join(dir, "config.json"), JSON.stringify({ exaApiKey: "panel-test-key", mineruApiToken: "ocr-test", unexpected: "do not export" }));
  const env = resolveConfigEnvironment({ dataDir: dir, env: { EXA_API_KEY: "env-test", USER_CONFIG_EXA_API_KEY: "user-test", KEEP: "keep" } });
  assert.equal(env.EXA_API_KEY, "panel-test-key");
  assert.equal(env.MINERU_API_TOKEN, "ocr-test");
  assert.equal(env.unexpected, undefined);
  assert.equal(env.KEEP, "keep");
});

test("an explicitly invalid interpreter cannot fall back to the system Python", t => {
  const dir = temp(t);
  const runtime = resolvePythonRuntime({ dataDir: dir, env: { ...process.env, SANMU_PYTHON: join(dir, "missing python.exe") } });
  assert.equal(runtime.supported, false);
  assert.match(runtime.error, /SANMU_PYTHON/);
});

test("automatic setup can be disabled without writing a runtime", async t => {
  const dir = temp(t);
  await assert.rejects(ensureRuntime({ dataDir: dir, env: { ...process.env, SANMU_NO_AUTO_SETUP: "1" } }), /automatic setup is disabled/);
  assert.equal(existsSync(join(dir, "python")), false);
  assert.equal(existsSync(join(dir, "runtime-ready.json")), false);
});

test("subprocess argv preserves Unicode, quotes, and shell metacharacters", async () => {
  const args = ["中文 space", 'quote"inside', "$(never-execute)", "`literal`", "a;b"];
  const result = await runProcess(process.execPath, ["-e", "process.stdout.write(JSON.stringify(process.argv.slice(1)))", ...args]);
  assert.equal(result.code, 0);
  assert.deepEqual(JSON.parse(result.stdout), args);
});

test("setup failure leaves no ready marker and releases its lock", async t => {
  const dir = temp(t);
  await assert.rejects(ensureRuntime({ dataDir: dir, env: process.env, python: false, katex: true, explicit: true,
    npmRuntime: { cmd: "fake-npm", prefixArgs: [] }, runner: async () => ({ code: 17 }), log() {} }), /exit 17/);
  assert.equal(existsSync(join(dir, "runtime-ready.json")), false);
  assert.equal(existsSync(join(dir, ".setup.lock")), false);
});

test("a live setup lock is never forcibly stolen", async t => {
  const dir = temp(t);
  mkdirSync(join(dir, ".setup.lock"));
  const token = `${process.pid}-test-live.json`;
  const { hostname } = await import("node:os");
  writeFileSync(join(dir, ".setup.lock", token), JSON.stringify({ pid: process.pid, host: hostname(), token }));
  await assert.rejects(ensureRuntime({ dataDir: dir, env: process.env, python: false, katex: true, explicit: true, lockTimeoutMs: 50, lockPollMs: 10, log() {} }), /never forcibly removed/);
  assert.equal(existsSync(join(dir, ".setup.lock", token)), true);
});

test("CLI help and all script help work without setting up dependencies", t => {
  const dir = temp(t);
  for (const args of [["--help"], ["search", "--help"], ["fetch-pdf", "--help"], ["reader", "evidence", "--help"], ["doc2md", "build-research-document", "--help"]]) {
    const result = spawnSync(process.execPath, [fileURLToPath(cli), ...args], { encoding: "utf8", env: { ...process.env, SANMU_DATA_DIR: dir, SANMU_NO_AUTO_SETUP: "1" } });
    assert.equal(result.status, 0, result.stderr);
  }
  assert.equal(existsSync(join(dir, "python")), false);
});
