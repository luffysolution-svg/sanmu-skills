import assert from "node:assert/strict";
import { after, test } from "node:test";
import { copyFileSync, existsSync, mkdirSync, mkdtempSync, readFileSync, readdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";
import { startMcpServer } from "./mcp-driver.mjs";

const here = dirname(fileURLToPath(import.meta.url));
const serverPath = resolve(here, "../../distribution/zcode/sanmu-paper-search/scripts/mcp-server.mjs");
const root = mkdtempSync(join(tmpdir(), "sanmu-mcp-unit-"));
const plugin = join(root, "plugin");
const data = join(root, "data");
const workspace = join(root, "workspace");
mkdirSync(join(plugin, "scripts"), { recursive: true });
mkdirSync(join(plugin, "assets"), { recursive: true });
mkdirSync(workspace, { recursive: true });
copyFileSync(join(here, "mcp-python-fixture.py"), join(plugin, "scripts/paper_search.py"));
copyFileSync(join(here, "mcp-python-fixture.py"), join(plugin, "scripts/fetch_pdf.py"));
writeFileSync(join(plugin, "assets/panel.html"), "<!doctype html><title>Fixture panel</title>");
const env = {
  SANMU_PLUGIN_ROOT: plugin, ZCODE_PLUGIN_DATA: data, ZCODE_WORKSPACE_ROOT: workspace,
  USER_CONFIG_ZOTERO_URL: "http://127.0.0.1:1", USER_CONFIG_EXPORT_SUBDIR: "inherited-export",
  USER_CONFIG_EXA_API_KEY: "FAKE_UNIT_USER_KEY", EXA_API_KEY: "FAKE_UNIT_SYSTEM_KEY",
  OPENALEX_EMAIL: "system@example.test", USER_CONFIG_POLITE_EMAIL: "user@example.test"
};
Object.assign(process.env, env);
const originalFetch = globalThis.fetch;
let fetchImpl = async () => { throw new Error("offline fixture"); };
globalThis.fetch = (...args) => fetchImpl(...args);
const { handleRequest } = await import(pathToFileURL(serverPath).href);
let id = 1;
const call = async (name, args = {}) => (await handleRequest({ jsonrpc: "2.0", id: id++, method: "tools/call", params: { name, arguments: args } })).result;
const json = (path, value) => writeFileSync(path, JSON.stringify(value));
const reset = () => {
  rmSync(data, { recursive: true, force: true });
  mkdirSync(join(data, "runs"), { recursive: true });
  fetchImpl = async () => { throw new Error("offline fixture"); };
};
const paper = (doi = "one", title = "Same title") => ({ title, year: 2025, identifiers: { doi: [`10.1234/${doi}`] }, authors: [{ name: "Alice Lee" }], abstract: "Returned abstract" });
const makeRun = (runId = "fixture-run", papers = [paper()], extra = {}) => {
  const dir = join(data, "runs", runId);
  mkdirSync(dir, { recursive: true });
  json(join(dir, "run.json"), { command: "search", status: "success", query: {}, ...extra });
  json(join(dir, "papers.json"), papers);
  return dir;
};
const expectError = (result, pattern) => {
  assert.equal(result.isError, true);
  if (pattern) assert.match(result.content[0].text, pattern);
};
after(() => { globalThis.fetch = originalFetch; rmSync(root, { recursive: true, force: true }); });

test("MCP offline regressions", async (t) => {
  await t.test("stdio survives null/malformed requests and negotiates a supported protocol", async () => {
    reset();
    const client = startMcpServer(serverPath, { env, cwd: workspace });
    try {
      client.child.stdin.write("null\n{broken\n");
      const init = await client.request("initialize", { protocolVersion: "unknown" });
      assert.equal(init.result.protocolVersion, "2025-11-25");
      client.notify("notifications/initialized");
      const list = await client.request("tools/list");
      assert.ok(list.result.tools.find((tool) => tool.name === "search_serper").inputSchema.properties.params);
      assert.equal((await client.request("ping")).result.constructor, Object);
      assert.equal((await client.request("resources/read", { uri: "ui://sanmu-paper-search/panel.html" })).result.contents[0].text.includes("Fixture panel"), true);
    } finally { await client.close(); }
  });

  await t.test("configuration inherits userConfig and panel overrides without exposing keys", async () => {
    reset();
    const initial = await call("open_panel");
    assert.equal(initial.structuredContent.settings.zoteroUrl, env.USER_CONFIG_ZOTERO_URL);
    assert.equal(initial.structuredContent.settings.exportSubdir, "inherited-export");
    assert.equal(initial.structuredContent.configStatus.exa_api_key.source, "userConfig");
    const saved = await call("save_config", { exaApiKey: "FAKE_UNIT_PANEL_KEY", politeEmail: "panel@example.test" });
    assert.equal(saved.structuredContent.configStatus.exa_api_key.source, "panel");
    assert.equal(JSON.stringify(saved).includes("FAKE_UNIT_PANEL_KEY"), false);
    const doctor = await call("doctor_status");
    assert.equal(doctor.structuredContent.doctorReport.doctor.emails.OPENALEX_EMAIL, "panel@example.test");
    const cleared = await call("save_config", { clearKeys: ["exaApiKey"] });
    assert.equal(cleared.structuredContent.configStatus.exa_api_key.source, "userConfig");
    assert.equal(readdirSync(data).some((file) => file.endsWith(".tmp")), false);
  });

  await t.test("argument validation rejects destructive defaults and malformed values", async () => {
    reset();
    for (const [name, args] of [
      ["search_papers", { query: " " }], ["search_papers", { query: "x", limit: -1 }],
      ["search_papers", { query: "x", sources: ["bad"] }], ["search_papers", { query: "x", yearFrom: 2026, yearTo: 2020 }],
      ["manage_basket", { action: "remove" }], ["manage_basket", { action: "add", paper: { title: "x", identifiers: { doi: "oops" } } }],
      ["save_config", { zoteroUrl: "https://remote.example.test" }], ["save_config", { clearKeys: ["exportSubdir"] }],
      ["open_panel", []], ["search_author", { provider: "openalex" }], ["search_epmc", {}]
    ]) expectError(await call(name, args));
    assert.equal(existsSync(join(data, "state.json")), false);
  });

  await t.test("run IDs cannot delete root or escape; invalid batch deletes are all-or-nothing", async () => {
    reset(); makeRun();
    for (const runId of [".", "..", "../fixture-run", "folder/fixture-run", "C:\\outside"]) expectError(await call("manage_runs", { operation: "delete", runId }));
    expectError(await call("manage_runs", { operation: "delete", runIds: ["fixture-run", "."] }));
    assert.equal(existsSync(join(data, "runs/fixture-run/run.json")), true);
    expectError(await call("manage_runs", { operation: "merge", runIds: ["missing"] }));
    expectError(await call("manage_runs", { operation: "load", runId: "missing" }));
    const deleted = await call("manage_runs", { operation: "delete", runIds: ["fixture-run", "fixture-run"] });
    assert.equal(deleted.structuredContent.deletedCount, 1);
  });

  await t.test("run files stay inside their run and private state/config/keys never return plaintext", async () => {
    reset(); const dir = makeRun();
    json(join(data, "config.json"), { exaApiKey: "FAKE_UNIT_PANEL_KEY" });
    writeFileSync(join(workspace, "note.txt"), "readable external note FAKE_UNIT_PANEL_KEY");
    expectError(await call("read_run_file", { runId: "fixture-run", relativePath: "../../config.json" }));
    expectError(await call("read_run_file", { relativePath: join(data, "config.json") }));
    const normal = await call("read_run_file", { relativePath: join(workspace, "note.txt") });
    assert.equal(normal.structuredContent.content, "readable external note [REDACTED]");
    const report = await call("read_run_file", { runId: "fixture-run", relativePath: "papers.json" });
    assert.equal(report.structuredContent.fullPath, join(dir, "papers.json"));
  });

  await t.test("damaged state cannot be silently replaced by an empty basket", async () => {
    reset(); const corrupt = '{"basket": [';
    writeFileSync(join(data, "state.json"), corrupt);
    expectError(await call("manage_basket", { action: "clear" }), /保留原文件/);
    assert.equal(readFileSync(join(data, "state.json"), "utf8"), corrupt);
  });

  await t.test("failed Python bundles return MCP errors; partial results remain usable and secrets redacted", async () => {
    reset();
    const failed = await call("search_papers", { query: "fail" });
    expectError(failed, /fixture source failed/);
    assert.ok(failed.structuredContent.error.runId);
    assert.equal(JSON.stringify(failed).includes(env.USER_CONFIG_EXA_API_KEY), false);
    const partial = await call("search_papers", { query: "partial" });
    assert.equal(partial.isError, undefined);
    assert.equal(partial.structuredContent.activeRun.run.status, "partial");
    const author = await call("search_author", { provider: "semantic-scholar", authorId: "42", query: "Alice Lee", fromPaper: "10.1234/fixture" });
    expectError(author);
    assert.equal(readdirSync(join(data, "runs")).some((name) => name.startsWith("author-oa")), false);
  });

  await t.test("concurrent operations preserve baskets and evidence across awaited Python/network work", async () => {
    reset();
    const results = await Promise.all([
      call("manage_basket", { action: "add", paper: paper("one") }),
      call("explore_related", { id: "slow" }),
      call("manage_basket", { action: "add", paper: paper("two") }),
      call("manage_basket", { action: "update_evidence", paperKey: "doi:10.1234/one", stage: "read" })
    ]);
    for (const result of results) assert.equal(result.isError, undefined);
    const state = JSON.parse(readFileSync(join(data, "state.json"), "utf8"));
    assert.equal(state.basket.length, 2);
    assert.equal(state.evidenceMap["doi:10.1234/one"].stage, "read");
  });

  await t.test("metadata and snippets never auto-claim reading or attach EPMC files to every hit", async () => {
    reset(); const dir = makeRun("fixture-run", [paper("one"), paper("two")], { command: "epmc" });
    writeFileSync(join(dir, "fulltext.xml"), "<article/>");
    json(join(dir, "results.json"), { type: "maps", results: [{ data: { title: "Map result", snippet: "DOI 10.1234/mention" }, type: "maps" }] });
    const state = (await call("open_panel", { runId: "fixture-run" })).structuredContent;
    assert.equal(state.activeRun.papers[0].handoff_stage, "file_obtained");
    assert.equal(state.activeRun.papers[1].handoff_stage, "discovered");
    assert.equal(state.activeRun.results[0].candidate_paper, null);
    json(join(dir, "results.json"), { type: "scholar", results: [{ data: { title: "Scholar result", snippet: "brief excerpt" }, type: "scholar" }] });
    const candidate = (await call("open_panel", { runId: "fixture-run" })).structuredContent.activeRun.results[0].candidate_paper;
    assert.equal(candidate.evidence_level, "metadata_only");
    assert.equal(candidate.abstract, "");
    assert.equal(candidate.search_snippet, "brief excerpt");
    assert.equal(candidate.handoff_stage, "discovered");
  });

  await t.test("abstract enrichment refuses the first unrelated hit and leaves identity unchanged", async () => {
    reset();
    const result = await call("manage_basket", { action: "enrich_abstract", paper: { title: "Wrong target", identifiers: {}, authors: [] } });
    expectError(result, /未找到/);
    assert.equal(existsSync(join(data, "state.json")), false);
    assert.equal(readdirSync(join(data, "runs")).some((name) => name.startsWith(".enrich")), false);
  });

  await t.test("export_to_workspace is removed and citations preview provides three formats with unique BibTeX keys", async () => {
    reset();
    const notFound = await handleRequest({ jsonrpc: "2.0", id: id++, method: "tools/call", params: { name: "export_to_workspace", arguments: { exportType: "markdown_cards" } } });
    assert.equal(notFound.error.code, -32601);
    await call("manage_basket", { action: "add", papers: [paper("one"), paper("two")] });
    const state = (await call("open_panel")).structuredContent;
    const cites = state.citationsPreview;
    assert.ok(cites.gbt7714);
    assert.ok(cites.bibtex);
    assert.ok(cites.apa);
    assert.equal(cites.ris, undefined);
    const keys = [...cites.bibtex.matchAll(/@article\{([^,]+)/g)].map((m) => m[1]);
    assert.equal(new Set(keys).size, 2);
  });

  await t.test("EPMC suppl copying preserves SI bytes and does not copy fulltext.xml into user saveDir", async () => {
    reset();
    const result = await call("search_epmc", { query: "fixture", fetchMode: "suppl", saveDir: join(workspace, "epmc-export") });
    assert.equal(result.isError, undefined);
    const active = result.structuredContent.activeRun;
    assert.ok(active.run.exported_save_dir);
    assert.deepEqual(readFileSync(join(active.run.exported_save_dir, "supplementary", "figure.webp")), Buffer.from("RIFF-fixture"));
    assert.equal(existsSync(join(active.run.exported_save_dir, "fulltext.xml")), false);
    assert.equal(JSON.parse(readFileSync(join(active.runDir, "run.json"), "utf8")).exported_save_dir, undefined);
  });

  await t.test("PDF saves avoid existing files and do not downgrade completed reading", async () => {
    reset(); const dest = join(workspace, "pdf-export"); mkdirSync(dest, { recursive: true });
    writeFileSync(join(dest, "2025-Same title.pdf"), "existing PDF");
    json(join(data, "state.json"), { evidenceMap: { "doi:10.1234/one": { stage: "read" } }, basket: [] });
    const result = await call("resolve_oa_and_pdf", { action: "download_pdf", pdfUrl: "https://example.test/paper.pdf", title: "Same title", year: 2025, doi: "10.1234/one", saveDir: dest });
    assert.equal(result.isError, undefined);
    assert.equal(readFileSync(join(dest, "2025-Same title.pdf"), "utf8"), "existing PDF");
    assert.notEqual(result.structuredContent.lastDownload.pdfPath, join(dest, "2025-Same title.pdf"));
    assert.equal(JSON.parse(readFileSync(join(data, "state.json"), "utf8")).evidenceMap["doi:10.1234/one"].stage, "read");
    assert.doesNotMatch(result.content[0].text, /核验成功/);
  });

  await t.test("Serper webpage and scholar params reach the Python route and unsupported types are rejected", async () => {
    reset();
    expectError(await call("search_serper", { type: "reviews", query: "place" }));
    const result = await call("search_serper", { type: "scholar", query: "photocatalysis", params: { hl: "en", sortBy: "newest" } });
    assert.equal(result.isError, undefined);
    const args = result.structuredContent.activeRun.run.query.args;
    assert.deepEqual(JSON.parse(args[args.indexOf("--params") + 1]), { hl: "en", sortBy: "newest" });
  });

  await t.test("ordinary HTTP services cannot masquerade as Zotero", async () => {
    reset(); fetchImpl = async () => new Response("{}", { status: 200 });
    assert.equal((await call("open_panel")).structuredContent.configStatus.zotero_local.online, false);
    fetchImpl = async () => new Response("{}", { status: 404, headers: { "Zotero-Server-ID": "fixture-server" } });
    assert.equal((await call("open_panel")).structuredContent.configStatus.zotero_local.online, false);
  });

  await t.test("undo refuses arbitrary keys and legacy evidence matches before touching Zotero", async () => {
    reset(); let touched = 0;
    fetchImpl = async () => { touched++; throw new Error("should not fetch"); };
    json(join(data, "state.json"), { basket: [], evidenceMap: { x: { zoteroMatch: { matched: true, createdByPlugin: true, itemKey: "ABCD2345" } } } });
    expectError(await call("manage_basket", { action: "undo_zotero_save", zoteroItemKey: "ABCD2345" }), /拒绝删除/);
    assert.equal(touched, 0);
  });

  await t.test("Zotero exact DOI matching, trusted create/readback and undo version protection", async () => {
    reset();
    let version = 7;
    let created = false;
    let deleted = 0;
    let postCount = 0;
    const record = () => ({ key: "ABCD2345", version, data: { itemType: "journalArticle", title: "Same title", DOI: "10.1234/one", creators: [{ name: "Alice Lee" }], tags: [{ tag: "sanmu-paper-search" }] } });
    fetchImpl = async (raw, options = {}) => {
      const url = new URL(raw);
      const response = (obj, status = 200) => new Response(JSON.stringify(obj), { status, headers: { "Zotero-Server-ID": "fixture-server", "Zotero-API-Version": "3" } });
      if (url.pathname === "/api/") return response({});
      if (url.pathname.endsWith("/collections")) return response([]);
      if (url.pathname === "/api/local/authorize") return response({ key: "FAKE_ZOTERO_WRITE_KEY", remember: true });
      if (url.pathname.endsWith("/children")) return response([]);
      if (url.pathname.endsWith("/ABCD2345") && options.method === "DELETE") { deleted++; return new Response(null, { status: 204 }); }
      if (url.pathname.endsWith("/ABCD2345")) return response(record());
      if (options.method === "POST") { postCount++; created = true; return response({ successful: { "0": record() } }); }
      // A prefix DOI with the same title must not suppress a distinct creation.
      return response(created ? [record()] : [{ key: "ZZZZ2345", data: { title: "Same title", DOI: "10.1234/one-extra", collections: [] } }]);
    };
    const saved = await call("manage_basket", { action: "save_to_zotero", paper: paper("one") });
    assert.equal(saved.isError, undefined);
    assert.equal(postCount, 1);
    assert.equal(saved.structuredContent.zoteroSaveResult.undoAvailable, true);
    assert.equal(JSON.stringify(saved).includes("FAKE_ZOTERO_WRITE_KEY"), false);
    version = 8;
    expectError(await call("manage_basket", { action: "undo_zotero_save", zoteroItemKey: "ABCD2345" }), /版本已改变/);
    assert.equal(deleted, 0);
    version = 7;
    const undone = await call("manage_basket", { action: "undo_zotero_save", zoteroItemKey: "ABCD2345" });
    assert.equal(undone.isError, undefined);
    assert.equal(deleted, 1);
    assert.equal(JSON.parse(readFileSync(join(data, "state.json"), "utf8")).zoteroImports.ABCD2345, undefined);
  });
});
