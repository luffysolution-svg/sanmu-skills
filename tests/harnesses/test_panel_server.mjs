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
  await t.test("browser responses keep connections and embedding on the same origin", async () => {
    assert.equal(page.headers.get("x-frame-options"), "SAMEORIGIN");
    const policy = page.headers.get("content-security-policy");
    assert.match(policy, /(?:^|;)\s*connect-src 'self'\s*(?:;|$)/);
    assert.match(policy, /(?:^|;)\s*frame-src 'self'\s*(?:;|$)/);
    assert.match(policy, /(?:^|;)\s*frame-ancestors 'self'\s*(?:;|$)/);
    assert.equal(page.headers.get("access-control-allow-origin"), null);
    assert.equal(page.headers.get("access-control-allow-credentials"), null);
    for (const path of ["/", "/panel", "/standalone", "/host"]) {
      const response = await fetch(panel.origin + path, { headers: { Cookie: cookie } });
      assert.equal(response.status, 200, path);
      assert.match(await response.text(), /sanmu-browser-bridge/);
      assert.equal(response.headers.get("set-cookie"), null, "An existing session does not need a new cookie");
    }
  });
  await t.test("loopback hints cannot replace an exact, single launch token", async () => {
    const token = new URL(panel.url).searchParams.get("token");
    const attempts = [
      ["/?token=wrong", {}, 403],
      ["/?token=%5BREDACTED%5D", {}, 403],
      ["/?token=redacted", {}, 403],
      ["/?token=", {}, 403],
      [`/?token=${token}&token=${token}`, {}, 403],
      [`/?token=${token}&token=wrong`, { Cookie: cookie }, 403],
      [`/?auth=${token}`, {}, 401],
      [`/?sid=${token}`, {}, 401],
      ["/", { "Sec-Fetch-Dest": "iframe" }, 401],
      ["/?token=wrong", { "Sec-Fetch-Dest": "iframe" }, 403]
    ];
    const previous = process.env.SANMU_ALLOW_ANONYMOUS_LOOPBACK;
    process.env.SANMU_ALLOW_ANONYMOUS_LOOPBACK = "1";
    try {
      const anonymous = await fetch(panel.origin);
      assert.equal(anonymous.status, 401, "An environment flag cannot grant an anonymous session");
      assert.equal(anonymous.headers.get("set-cookie"), null);
      for (const [path, requestHeaders, status] of attempts) {
        const response = await fetch(panel.origin + path, { headers: requestHeaders });
        assert.equal(response.status, status, path);
        assert.equal(response.headers.get("set-cookie"), null, path);
      }
    } finally {
      if (previous === undefined) delete process.env.SANMU_ALLOW_ANONYMOUS_LOOPBACK;
      else process.env.SANMU_ALLOW_ANONYMOUS_LOOPBACK = previous;
    }
  });
  await t.test("authenticated RPC rejects foreign and opaque origins and cross-site fetches", async () => {
    const otherPort = new URL(panel.origin);
    otherPort.port = otherPort.port === "65535" ? "65534" : String(Number(otherPort.port) + 1);
    const origins = [undefined, "null", "https://example.invalid", otherPort.origin,
      panel.origin.replace("127.0.0.1", "localhost"), "file://", "dsh-app://desktop", "vscode-webview://panel"];
    for (const origin of origins) {
      const requestHeaders = { "Content-Type": "application/json", Cookie: cookie };
      if (origin !== undefined) requestHeaders.Origin = origin;
      const response = await fetch(panel.origin + "/rpc", {
        method: "POST", headers: requestHeaders,
        body: JSON.stringify({ jsonrpc: "2.0", id: 2, method: "ui/initialize" })
      });
      assert.equal(response.status, 403, `Origin: ${origin}`);
      assert.equal(response.headers.get("access-control-allow-origin"), null);
      assert.equal(response.headers.get("access-control-allow-credentials"), null);
    }
    for (const site of ["cross-site", "same-site", "none", "same-origin, cross-site"]) {
      const response = await fetch(panel.origin + "/rpc", {
        method: "POST", headers: { ...headers, "Sec-Fetch-Site": site }, body: "{}"
      });
      assert.equal(response.status, 403, `Sec-Fetch-Site: ${site}`);
    }
    const sameOrigin = await fetch(panel.origin + "/rpc", {
      method: "POST", headers: { ...headers, "Sec-Fetch-Site": "same-origin" },
      body: JSON.stringify({ jsonrpc: "2.0", id: 3, method: "ui/initialize" })
    });
    assert.equal(sameOrigin.status, 200);
    assert.equal((await sameOrigin.json()).result.hostInfo.name, "sanmu-browser");
    assert.equal(sameOrigin.headers.get("access-control-allow-origin"), null);
    const foreignNavigation = await fetch(panel.url, { headers: { Origin: otherPort.origin, "Sec-Fetch-Dest": "iframe" } });
    assert.equal(foreignNavigation.status, 403);
    assert.equal(foreignNavigation.headers.get("set-cookie"), null);
    for (const origin of [panel.origin, "null", otherPort.origin]) {
      const response = await fetch(panel.origin + "/rpc", { method: "OPTIONS", headers: {
        Origin: origin, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"
      } });
      assert.equal(response.status, origin === panel.origin ? 405 : 403);
      assert.equal(response.headers.get("access-control-allow-origin"), null);
      assert.equal(response.headers.get("access-control-allow-credentials"), null);
    }
  });
  await panel.close();
  await panel.close();
  assert.equal(panel.child.exitCode, 0);
});
