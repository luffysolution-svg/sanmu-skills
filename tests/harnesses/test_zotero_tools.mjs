import assert from "node:assert/strict";
import { test } from "node:test";
import { executeZoteroLibrary } from "../../scripts/zotero-tools.mjs";

const key = "ABCD2345", childKey = "EFGH2345";
function fixture() {
  const calls = [];
  let item = { key, version: 7, library: { type: "user", id: 0 }, data: { itemType: "journalArticle", title: "Before", tags: [] } };
  let note;
  const fetchImpl = async (url, options) => {
    calls.push({ url, options });
    const path = new URL(url).pathname;
    if (path === "/api/") return new Response("{}", { headers: { "zotero-server-id": "test-server" } });
    if (options.method === "PATCH") {
      assert.equal(options.headers["If-Unmodified-Since-Version"], "7");
      assert.equal(options.headers["Zotero-API-Key"], "private-test-key");
      item = { ...item, version: 8, data: { ...item.data, ...JSON.parse(options.body) } };
      return new Response(null, { status: 204 });
    }
    if (options.method === "POST" && path.endsWith("/items")) {
      note = { key: childKey, version: 1, library: item.library, data: JSON.parse(options.body)[0] };
      return Response.json({ successful: { 0: { key: childKey } }, failed: {} });
    }
    if (path.endsWith("/children")) return Response.json(note ? [note] : []);
    if (path.endsWith(`/items/${key}`)) return Response.json(item);
    if (path.endsWith(`/items/${childKey}`)) return Response.json(note);
    throw new Error("Unexpected route: " + path);
  };
  const authorize = async (_url, serverId) => ({ key: "private-test-key", serverId });
  return { calls, fetchImpl, authorize, setItem: value => { item = value; } };
}

test("metadata writes use a fresh version, authorization, and verified readback", async () => {
  const f = fixture();
  const result = await executeZoteroLibrary({ action: "update_item", key, expectedVersion: 7, fields: { title: "After" }, addTags: ["research"] }, f);
  assert.equal(result.structured.item.data.title, "After");
  assert.equal(result.structured.item.data.tags[0].tag, "research");
  assert.equal(JSON.stringify(result).includes("private-test-key"), false);
});

test("stale versions and different server identities never write", async () => {
  for (const extra of [{ expectedVersion: 6 }, { expectedVersion: 7, expectedServerId: "wrong-server" }]) {
    const f = fixture();
    await assert.rejects(executeZoteroLibrary({ action: "update_item", key, fields: { title: "After" }, ...extra }, f), /changed|identity/);
    assert.equal(f.calls.some(call => call.options.method === "PATCH"), false);
  }
});

test("sync note HTML is preserved and repeated note creation is idempotent", async () => {
  const f = fixture();
  const input = { action: "add_note", parentKey: key, expectedVersion: 7, markdown: "KB-SYNC:test\n\n**Note**", html: '<div data-schema-version="9"><p>KB-SYNC:test</p><p><strong>Note</strong></p></div>', marker: "KB-SYNC:test" };
  const first = await executeZoteroLibrary(input, f);
  assert.equal(first.structured.item.data.note, input.html);
  const second = await executeZoteroLibrary(input, f);
  assert.equal(second.structured.alreadyExists, true);
  assert.equal(f.calls.filter(call => call.options.method === "POST").length, 1);
});

test("group identity mismatches and active HTML are rejected", async () => {
  const f = fixture();
  await assert.rejects(executeZoteroLibrary({ action: "get_item", library: "g123", key }, f), /library identity/);
  await assert.rejects(executeZoteroLibrary({ action: "add_note", parentKey: key, markdown: "note", html: "<script>bad()</script>" }, f), /active content/);
  await assert.rejects(executeZoteroLibrary({ action: "search" }, { baseUrl: "https://example.com", ...f }), /loopback/);
});
