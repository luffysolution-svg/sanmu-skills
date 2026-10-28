import assert from "node:assert/strict";
import { test } from "node:test";
import { mkdtempSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { startPanelServer } from "../../scripts/panel-server.mjs";
import { fileURLToPath } from "node:url";
import { request as httpRequest } from "node:http";

test("portable browser workbench exposes complete MCP and protects local RPC", { timeout: 30000 }, async t => {
  const directory = mkdtempSync(join(tmpdir(), "sanmu-panel-"));
  const panel = await startPanelServer({ openBrowser: false, hostRoot: fileURLToPath(new URL("../../", import.meta.url)),
    env: { ...process.env, SANMU_DATA_DIR: directory, SANMU_NO_AUTO_SETUP: "1" } });
  t.after(async () => { await panel.close(); rmSync(directory, { recursive: true, force: true }); });
  assert.equal(panel.tools.length, 24);
  const unauthorized = await fetch(panel.origin);
  assert.equal(unauthorized.status, 401);
  const page = await fetch(panel.url);
  assert.equal(page.status, 200);
  const html = await page.text();
  assert.match(html, /sanmu-browser-bridge/);
  assert.match(html, /文档解析/);
  assert.match(html, /复制精读/);
  const cookie = page.headers.get("set-cookie").split(";")[0];
  assert.match(page.headers.get("set-cookie"), /HttpOnly; SameSite=Strict/);
  const headers = { "Content-Type": "application/json", Cookie: cookie, Origin: panel.origin };
  const rpc = async (method, params = {}) => (await fetch(panel.origin + "/rpc", { method: "POST", headers,
    body: JSON.stringify({ jsonrpc: "2.0", id: 1, method, params }) })).json();
  const initialized = await rpc("ui/initialize");
  assert.equal(initialized.result.hostInfo.name, "sanmu-browser");
  const state = await rpc("tools/call", { name: "open_panel", arguments: {} });
  assert.equal(state.result.isError, undefined);
  assert.equal(state.result.structuredContent.settings.runsDir, join(directory, "runs"));
  assert.equal(state.result.structuredContent.panelUrl, undefined);
  const failed = await rpc("tools/call", { name: "search_papers", arguments: { query: "" } });
  assert.equal(failed.result.isError, true);
  const handoff = await rpc("ui/message", { content: [{ type: "text", text: "read this paper" }] });
  assert.equal(handoff.result.delivered, false);
  assert.equal(handoff.result.fallback, "copy");
  const crossSite = await fetch(panel.origin + "/rpc", { method: "POST", headers: { ...headers, Origin: "https://example.invalid" }, body: "{}" });
  assert.equal(crossSite.status, 403);
  const missingCookie = await fetch(panel.origin + "/rpc", { method: "POST", headers: { Origin: panel.origin, "Content-Type": "application/json" }, body: "{}" });
  assert.equal(missingCookie.status, 401);
  const reboundStatus = await new Promise((resolve, reject) => {
    const request = httpRequest(panel.origin, { headers: { Host: "example.invalid", Cookie: cookie } }, response => { response.resume(); resolve(response.statusCode); });
    request.once("error", reject); request.end();
  });
  assert.equal(reboundStatus, 403);
  await panel.close();
  await panel.close();
  assert.equal(panel.child.exitCode, 0);
});
