import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { execFileSync } from 'node:child_process';
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
const {
  handleRequest,
  resolveEnvPath,
  readUserConfigFromEnv,
  resolveHostPaths,
  buildRelationshipGraph
} = await import(pathToFileURL(serverPath).href);
let id = 1;
const call = async (name, args = {}) => (await handleRequest({ jsonrpc: "2.0", id: id++, method: "tools/call", params: { name, arguments: args } })).result;
const json = (path, value) => writeFileSync(path, JSON.stringify(value));
const reset = () => {
  rmSync(data, { recursive: true, force: true });
  mkdirSync(join(data, "runs"), { recursive: true });
  fetchImpl = async () => { throw new Error("offline fixture"); };
};
const paper = (doi = "one", title = "Same title") => ({ title, year: 2025, identifiers: { doi: [`10.1234/${doi}`] }, authors: [{ name: "Alice Lee" }], abstract: "Returned abstract" });
const writePdf = (path, text, metadata = '') => {
  const content = `BT /F1 12 Tf 30 700 Td (${text}) Tj ET`;
  const objects = [
    '<< /Type /Catalog /Pages 2 0 R >>',
    '<< /Type /Pages /Count 1 /Kids [3 0 R] >>',
    '<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>',
    `<< /Length ${Buffer.byteLength(content)} >>\nstream\n${content}\nendstream`,
    '<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
    `<< /Subject (${metadata}) >>`
  ];
  let pdf = '%PDF-1.7\n';
  const offsets = [0];
  objects.forEach((obj, idx) => { offsets.push(Buffer.byteLength(pdf)); pdf += `${idx + 1} 0 obj\n${obj}\nendobj\n`; });
  const xref = Buffer.byteLength(pdf);
  pdf += `xref\n0 7\n0000000000 65535 f \n${offsets.slice(1).map(n => `${String(n).padStart(10, '0')} 00000 n \n`).join('')}`;
  pdf += `trailer\n<< /Size 7 /Root 1 0 R /Info 6 0 R >>\nstartxref\n${xref}\n%%EOF\n`;
  writeFileSync(path, pdf);
};
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

  await t.test("batch basket additions validate every paper before changing stored state", async () => {
    for (const invalid of [
      { title: 'Bad identifiers', identifiers: { doi: '10.1234/not-an-array' } },
      { title: 'Bad authors', authors: 'Alice' },
      { title: ' ' },
      { title: 'Invalid key', paper_key: '__proto__', user_note: 'lost note' }
    ]) {
      reset();
      expectError(await call('manage_basket', { action: 'add', papers: [paper(), invalid] }));
      assert.equal(existsSync(join(data, 'state.json')), false);
      const panel = await call('open_panel');
      assert.equal(panel.isError, undefined);
      assert.deepEqual(panel.structuredContent.basket, []);
    }
  });

  await t.test('legacy note RPC applies the same key and type checks as the public basket tool', async () => {
    for (const params of [
      { paperKey: '__proto__', userNote: 'lost note' },
      { paperKey: 'doi:10.1234/one', userTags: [42] },
      { paperKey: 'doi:10.1234/one', userNote: { unexpected: true } }
    ]) {
      reset();
      const reply = await handleRequest({ jsonrpc: '2.0', id: id++, method: '_set_paper_note_tags', params });
      expectError(reply.result);
      assert.equal(existsSync(join(data, 'state.json')), false);
    }
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

  await t.test("rank enrichment updates papers without invalidating original manifest or navigation", async () => {
    reset();
    const dir = makeRun();
    const entries = ["run.json", "papers.json"].map((path) => {
      const bytes = readFileSync(join(dir, path));
      return { path, sha256: createHash("sha256").update(bytes).digest("hex"), size: bytes.length };
    });
    json(join(dir, "manifest.json"), { algorithm: "sha256", files: entries });
    await call("open_panel", { runId: "fixture-run" });
    const result = await call("lookup_venue_rank", { runId: "fixture-run", preserveActiveRun: true });
    assert.equal(result.isError, undefined);
    assert.equal(result.structuredContent.activeRun.runId, "fixture-run");
    assert.equal(JSON.parse(readFileSync(join(dir, "papers.json"), "utf8"))[0].venue_rank.if, 20);
    const updated = JSON.parse(readFileSync(join(dir, "manifest.json"), "utf8"));
    for (const entry of updated.files) {
      const bytes = readFileSync(join(dir, entry.path));
      assert.equal(entry.sha256, createHash("sha256").update(bytes).digest("hex"));
      assert.equal(entry.size, bytes.length);
    }
    assert.deepEqual(updated.files.find((entry) => entry.path === "run.json"), entries[0]);
    // A damaged manifest must be rejected before touching original papers.
    json(join(dir, "manifest.json"), { algorithm: "sha256", files: [] });
    const original = readFileSync(join(dir, "papers.json"), "utf8");
    expectError(await call("lookup_venue_rank", { runId: "fixture-run" }), /哈希清单/);
    assert.equal(readFileSync(join(dir, "papers.json"), "utf8"), original);
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
    assert.equal(result.structuredContent.recentRuns[0].savedPath, result.structuredContent.lastDownload.pdfPath);
    assert.equal(JSON.parse(readFileSync(join(result.structuredContent.lastDownload.outDir, "acquisition.json"), "utf8")).saved_pdf_path, result.structuredContent.lastDownload.pdfPath);
    assert.equal(JSON.parse(readFileSync(join(data, "state.json"), "utf8")).evidenceMap["doi:10.1234/one"].stage, "read");
    assert.doesNotMatch(result.content[0].text, /核验成功/);
  });

  await t.test("direct PDF URL detection, machine TDM filtering, DOI suffix stripping and payload compaction", async () => {
    reset();
    const richPaper = {
      ...paper("frontiers", "Frontiers OA Paper"),
      versions: [{ title: "Duplicate version" }],
      conflicts: [{ field: "year", values: [2024, 2025] }],
      authors: [{
        name: "Alice Lee",
        affiliations: ["South China Agricultural University (CN)", "College of Materials and Energy South China Agricultural University Guangzhou Guangdong 510643 P. R. China"],
        raw_affiliation_strings: ["Raw long affiliation string"]
      }],
      venue_rank: { if: 15.2, badges: ["IF 15.2"], official_all: { huge: "payload" }, official_select: { huge: "payload" } },
      oa_locations: [
        { url: "https://www.frontiersin.org/articles/10.3389/fchem.2022.1048504/pdf", provider: "openalex", is_oa: true, license_status: "open" },
        { url: "https://www.mdpi.com/1422-0067/25/16/8842/pdf?version=1", provider: "openalex", is_oa: true, license_status: "open" },
        { url: "https://api.elsevier.com/content/article/PII:S1005030223007259?httpAccept=text/xml", provider: "crossref" }
      ]
    };
    const dir = makeRun("compact-run", [richPaper]);
    json(join(dir, "results.json"), {
      type: "scholar",
      results: [{
        type: "scholar",
        data: {
          title: "ACS Paper",
          link: "https://pubs.acs.org/aaemcq/article-abstract/doi/10.1021/acsaem.6c02744/5450803",
          snippet: "Body text\n## References\nRef 1 doi:10.1038/ncomms8938"
        }
      }]
    });
    const sc = (await call("open_panel", { runId: "compact-run" })).structuredContent;
    const enriched = sc.activeRun.papers[0];
    assert.equal(enriched.versions, undefined);
    assert.equal(enriched.conflicts, undefined);
    assert.equal(enriched.authors[0].raw_affiliation_strings, undefined);
    assert.deepEqual(enriched.authors[0].affiliations, ["South China Agricultural University (CN)"]);
    assert.equal(enriched.venue_rank.official_all, undefined);
    assert.equal(enriched.candidate_pdf_urls.length, 2);
    assert.equal(enriched.clickable_links.some((l) => l.url.includes("api.elsevier.com")), false);
    assert.deepEqual(sc.activeRun.results[0].extracted_dois, ["10.1021/acsaem.6c02744"]);
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

  await t.test("v1.7.0 Req 0: cross-harness env resolution ignores unexpanded ${...} templates and falls back across ZCode/DSH/Claude", async () => {
    assert.equal(resolveEnvPath(""), "");
    assert.equal(resolveEnvPath("  "), "");
    assert.equal(resolveEnvPath("${ZCODE_PROJECT_DIR}"), "");
    assert.equal(resolveEnvPath("prefix/${user_config.politeEmail}"), "");
    assert.equal(resolveEnvPath("F:\\knowledge-base"), "F:\\knowledge-base");
    assert.equal(readUserConfigFromEnv("USER_CONFIG_POLITE_EMAIL", { USER_CONFIG_POLITE_EMAIL: "${user_config.politeEmail}" }), "");
    assert.equal(readUserConfigFromEnv("USER_CONFIG_POLITE_EMAIL", { USER_CONFIG_POLITE_EMAIL: "real@scau.edu.cn" }), "real@scau.edu.cn");

    const dshPaths = resolveHostPaths({
      ZCODE_WORKSPACE_ROOT: "${ZCODE_PROJECT_DIR}",
      DSH_WORKSPACE_ROOT: "/tmp/dsh-workspace",
      ZCODE_PLUGIN_DATA: "${ZCODE_PLUGIN_DATA}",
      DSH_HOME: "/tmp/dsh-home"
    });
    assert.equal(dshPaths.workspaceRoot, "/tmp/dsh-workspace");
    assert.equal(dshPaths.pluginData, join("/tmp/dsh-home", "plugin-data", "sanmu-paper-search"));

    const claudePaths = resolveHostPaths({
      ZCODE_PROJECT_DIR: "${ZCODE_PROJECT_DIR}",
      CLAUDE_PROJECT_DIR: "/tmp/claude-proj",
      CLAUDE_PLUGIN_DATA: "/tmp/claude-data"
    });
    assert.equal(claudePaths.workspaceRoot, "/tmp/claude-proj");
    assert.equal(claudePaths.pluginData, "/tmp/claude-data");
  });

  await t.test('DSH bundle starts outside its package and explicitly passes credentials through the host environment scrub', async () => {
    const patch = resolve(here, '../../distribution/zcode/sanmu-paper-search/cordis.patch.yml');
    const patchText = readFileSync(patch, 'utf8');
    const idMatch = patchText.match(/-\s*id:\s*([^\r\n#]+)/);
    const nameMatch = patchText.match(/name:\s*["']?([^"'\r\n#]+)["']?/);
    const row = { id: idMatch?.[1]?.trim(), name: nameMatch?.[1]?.trim() };
    const hostEnv = { DSH_HOME: join(root, 'dsh-home'), DSH_WORKSPACE_ROOT: workspace,
      USER_CONFIG_EXA_API_KEY: 'FAKE_DSH_EXA_KEY', USER_CONFIG_SCIVERSE_API_KEY: 'FAKE_DSH_SCIVERSE_KEY', USER_CONFIG_POLITE_EMAIL: 'dsh@example.test' };
    let config = row.config;
    if (row.name.startsWith('./')) {
      const adapter = resolve(here, '../../distribution/zcode/sanmu-paper-search', row.name);
      const localAdapter = join(plugin, 'scripts/dsh-plugin.mjs');
      copyFileSync(adapter, localAdapter);
      copyFileSync(serverPath, join(plugin, 'scripts/mcp-server.mjs'));
      const module = await import(pathToFileURL(localAdapter).href);
      config = module.createMcpConfig(hostEnv);
    }
    const childEnv = Object.fromEntries(Object.keys(process.env).filter(k => /API_KEY|USER_CONFIG|ZCODE_|CLAUDE_|DSH_/.test(k)).map(k => [k, '']));
    const client = startMcpServer(config.args[0], { env: { ...childEnv, ...config.env }, cwd: config.cwd || workspace });
    try {
      await client.request('initialize');
      const panel = await client.call('open_panel');
      assert.equal(panel.isError, undefined);
      assert.equal(panel.structuredContent.settings.workspaceRoot, workspace);
      assert.equal(panel.structuredContent.settings.runsDir, join(hostEnv.DSH_HOME, 'plugin-data/sanmu-paper-search/runs'));
      assert.equal(panel.structuredContent.configStatus.exa_api_key.configured, true);
      assert.equal(JSON.stringify(panel).includes('FAKE_DSH_EXA_KEY'), false);
      const doctor = await client.call('doctor_status');
      assert.equal(doctor.structuredContent.doctorReport.doctor.emails.POLITE_EMAIL, 'dsh@example.test');
    } finally { await client.close(); }
  });

  await t.test("v1.7.0 Req 1 & 2: search_papers --sort and search_author --topic/--sort reach Python CLI", async () => {
    reset();
    const sRes = await call("search_papers", { query: "single atom Ni", sort: "cited", limit: 8 });
    assert.equal(sRes.isError, undefined);
    const sArgs = sRes.structuredContent.activeRun.run.query.args;
    assert.equal(sArgs[sArgs.indexOf("--sort") + 1], "cited");

    const aRes = await call("search_author", {
      provider: "openalex",
      authorId: "A5047449616",
      topic: "photocatalytic",
      sort: "cited",
      limit: 6
    });
    assert.equal(aRes.isError, undefined);
    const aArgs = aRes.structuredContent.activeRun.run.query.args;
    assert.equal(aArgs[aArgs.indexOf("--topic") + 1], "photocatalytic");
    assert.equal(aArgs[aArgs.indexOf("--sort") + 1], "cited");
  });

  await t.test("v1.7.0 Req 3: claim_browser_pdf claims matched DOI/title PDF and unique recent fallback PDF", async () => {
    reset();
    const dlDir = join(root, "mock-downloads");
    const saveDir = join(workspace, "claimed-pdfs");
    mkdirSync(dlDir, { recursive: true });
    process.env.SANMU_BROWSER_DOWNLOAD_DIR = dlDir;
    copyFileSync(resolve(here, '../../skills/sanmu-paper-search/scripts/fetch_pdf.py'), join(plugin, 'scripts/fetch_pdf.py'));
    try {
      // 1. Exact DOI/title match in first-page text
      const matchedFile = join(dlDir, "browser-download-1.pdf");
      writePdf(
        matchedFile,
        "Single Atom Ni Photocatalytic Hydrogen Evolution DOI: 10.1002/adma.202313513"
      );
      const claimRes = await call("resolve_oa_and_pdf", {
        action: "claim_browser_pdf",
        doi: "10.1002/adma.202313513",
        title: "Single Atom Ni Photocatalytic Hydrogen Evolution",
        year: 2024,
        paperKey: "doi:10.1002/adma.202313513",
        saveDir
      });
      assert.equal(claimRes.isError, undefined);
      assert.equal(claimRes.structuredContent.lastDownload.identity_status, "matched");
      assert.ok(existsSync(claimRes.structuredContent.lastDownload.pdfPath));
      assert.match(claimRes.structuredContent.lastDownload.pdfPath, /2024-Single Atom Ni Photocatalytic Hydrogen Evolution-02313513\.pdf$/);
      const savedState = JSON.parse(readFileSync(join(data, "state.json"), "utf8"));
      assert.equal(savedState.evidenceMap["doi:10.1002/adma.202313513"].stage, "identity_checked");

      // 2. Unique recent (<3 min) scanned/unreadable PDF fallback -> needs_review & file_obtained
      rmSync(dlDir, { recursive: true, force: true });
      mkdirSync(dlDir, { recursive: true });
      const fallbackSaveDir = join(workspace, "claimed-fallback");
      writeFileSync(join(dlDir, "scanned-only.pdf"), "%PDF-1.7\n<binary-image-stream-only>\n%%EOF");
      const rpcFallback = await handleRequest({
        jsonrpc: "2.0",
        id: id++,
        method: "_claim_browser_pdf",
        params: {
          doi: "10.1021/acscatal.4c00001",
          title: "Scanned Paper Title",
          year: 2025,
          paperKey: "doi:10.1021/acscatal.4c00001",
          saveDir: fallbackSaveDir
        }
      });
      assert.equal(rpcFallback.result.isError, undefined);
      assert.equal(rpcFallback.result.structuredContent.lastDownload.identity_status, "needs_review");
      const savedState2 = JSON.parse(readFileSync(join(data, "state.json"), "utf8"));
      assert.equal(savedState2.evidenceMap["doi:10.1021/acscatal.4c00001"].stage, "file_obtained");
    } finally {
      delete process.env.SANMU_BROWSER_DOWNLOAD_DIR;
      copyFileSync(join(here, 'mcp-python-fixture.py'), join(plugin, 'scripts/fetch_pdf.py'));
    }
  });

  await t.test('browser PDF claim never verifies raw metadata and refuses a conflicting first-page DOI', async () => {
    reset();
    const dlDir = join(root, 'identity-downloads');
    mkdirSync(dlDir, { recursive: true });
    process.env.SANMU_BROWSER_DOWNLOAD_DIR = dlDir;
    copyFileSync(resolve(here, '../../skills/sanmu-paper-search/scripts/fetch_pdf.py'), join(plugin, 'scripts/fetch_pdf.py'));
    try {
      writePdf(join(dlDir, 'wrong-paper.pdf'), 'Unrelated study DOI: 10.1234/wrong', 'Expected Paper Title 10.1234/right');
      const result = await call('resolve_oa_and_pdf', { action: 'claim_browser_pdf', doi: '10.1234/right',
        title: 'Expected Paper Title', paperKey: 'doi:10.1234/right', saveDir: join(workspace, 'claim-identity') });
      expectError(result);
      assert.equal(existsSync(join(data, 'state.json')), false);
    } finally {
      delete process.env.SANMU_BROWSER_DOWNLOAD_DIR;
      copyFileSync(join(here, 'mcp-python-fixture.py'), join(plugin, 'scripts/fetch_pdf.py'));
    }
  });

  await t.test("v1.7.0 Req 5: user_tags and user_note persist, sync to Zotero parent tags + child note, and clean up on undo", async () => {
    reset();
    const targetPaper = paper("ni-sac", "Single Atom Ni/NiOx on g-C3N4");
    await call("manage_basket", { action: "add", paper: targetPaper });
    const noteRes = await handleRequest({
      jsonrpc: "2.0",
      id: id++,
      method: "_set_paper_note_tags",
      params: {
        paperKey: "doi:10.1234/ni-sac",
        user_tags: ["单原子Ni", "光催化析氢", "单原子Ni"],
        user_note: "含 XPS Ni 2p 分峰拟合参数与 AQY 波长依赖表"
      }
    });
    assert.equal(noteRes.result.isError, undefined);
    const basketItem = noteRes.result.structuredContent.basket[0];
    assert.deepEqual(basketItem.user_tags, ["单原子Ni", "光催化析氢"]);
    assert.equal(basketItem.user_note, "含 XPS Ni 2p 分峰拟合参数与 AQY 波长依赖表");
    const rawState = JSON.parse(readFileSync(join(data, "state.json"), "utf8"));
    assert.deepEqual(rawState.evidence["doi:10.1234/ni-sac"].user_tags, ["单原子Ni", "光催化析氢"]);
    assert.equal(rawState.evidence["doi:10.1234/ni-sac"].user_note, "含 XPS Ni 2p 分峰拟合参数与 AQY 波长依赖表");

    let parentPayload = null;
    let notePayload = null;
    const deletedKeys = [];
    const parentRec = () => ({
      key: "PRNT2345",
      version: 11,
      data: {
        itemType: "journalArticle",
        title: targetPaper.title,
        DOI: "10.1234/ni-sac",
        tags: parentPayload?.tags || [{ tag: "sanmu-paper-search" }, { tag: "单原子Ni" }, { tag: "光催化析氢" }]
      }
    });
    const childNoteRec = () => ({
      key: "NOTE5678",
      version: 12,
      data: {
        itemType: "note",
        parentItem: "PRNT2345",
        note: "<p>含 XPS Ni 2p 分峰拟合参数与 AQY 波长依赖表</p>",
        tags: [{ tag: "单原子Ni" }, { tag: "光催化析氢" }]
      }
    });
    fetchImpl = async (raw, options = {}) => {
      const url = new URL(raw);
      const response = (obj, status = 200) =>
        new Response(JSON.stringify(obj), {
          status,
          headers: { "Zotero-Server-ID": "fixture-server", "Zotero-API-Version": "3" }
        });
      if (url.pathname === "/api/") return response({});
      if (url.pathname.endsWith("/collections")) return response([]);
      if (url.pathname === "/api/local/authorize") return response({ key: "FAKE_ZOTERO_WRITE_KEY", remember: true });
      if (url.pathname.endsWith("/PRNT2345/children")) return response([childNoteRec()]);
      if (options.method === "DELETE") {
        deletedKeys.push(url.pathname.split("/").pop());
        return new Response(null, { status: 204 });
      }
      if (url.pathname.endsWith("/PRNT2345")) return response(parentRec());
      if (url.pathname.endsWith("/NOTE5678")) return response(childNoteRec());
      if (options.method === "POST" && url.pathname.endsWith("/items")) {
        const bodyArr = JSON.parse(options.body);
        if (bodyArr[0].itemType === "note") {
          notePayload = bodyArr[0];
          return response({ successful: { "0": childNoteRec() } });
        }
        parentPayload = bodyArr[0];
        return response({ successful: { "0": parentRec() } });
      }
      return response(parentPayload ? [parentRec()] : []);
    };

    const zSave = await call("manage_basket", { action: "save_to_zotero", paper: basketItem });
    assert.equal(zSave.isError, undefined);
    assert.ok(parentPayload.tags.some((t) => t.tag === "单原子Ni"));
    assert.ok(parentPayload.tags.some((t) => t.tag === "光催化析氢"));
    assert.ok(notePayload);
    assert.equal(notePayload.parentItem, "PRNT2345");
    assert.match(notePayload.note, /XPS Ni 2p/);
    assert.deepEqual(zSave.structuredContent.zoteroSaveResult.createdZoteroItems, ["PRNT2345", "NOTE5678"]);

    const zUndo = await call("manage_basket", { action: "undo_zotero_save", zoteroItemKey: "PRNT2345" });
    assert.equal(zUndo.isError, undefined);
    assert.deepEqual(deletedKeys, ["NOTE5678", "PRNT2345"]);
  });

  await t.test("v1.7.0 Req 6: buildRelationshipGraph identifies Co-cited Hubs and Bridge Papers", async () => {
    reset();
    const hubPaper = {
      paper_key: "doi:10.1000/hub",
      title: "Foundational Co-Cited Hub Paper",
      year: 2020,
      citation_count: 800,
      identifiers: { doi: ["10.1000/hub"] },
      authors: [{ name: "Hub Author" }],
      relations: [
        { type: "references", seed: "10.1000/seed1" },
        { type: "references", seed: "10.1000/seed2" }
      ]
    };
    const graph = buildRelationshipGraph(
      {
        runId: "rel-test",
        run: { command: "related", query: { id: "10.1000/seed1", relation: "references" } },
        papers: [hubPaper]
      },
      [],
      [],
      { evidenceMap: {} },
      {}
    );
    const hubNode = graph.nodes.find((n) => n.id === "doi:10.1000/hub");
    assert.ok(hubNode);
    assert.equal(hubNode.inDegree, 2);
    assert.equal(hubNode.connectedSourcesCount, 2);
    assert.equal(hubNode.isCoCitedHub, true);
    assert.equal(hubNode.isBridgeNode, true);
    assert.equal(graph.counts.coCitedHubs, 1);
    assert.equal(graph.counts.bridgeNodes, 1);
  });

  await t.test("v1.8.0 SciVerse MCP tool: search_sciverse content/figures & schema-materials export and config redaction", async () => {
    reset();
    await call("save_config", { sciverseApiKey: "sci_secret_unit_key_777" });
    const cfgRaw = readFileSync(join(data, "config.json"), "utf8");
    assert.match(cfgRaw, /sci_secret_unit_key_777/);

    const contentRes = await call("search_sciverse", {
      mode: "content",
      docId: "a".repeat(64),
      offset: 300,
      chars: 4000,
      downloadFigures: true,
      paperKey: "doi:10.1234/fixture",
      preserveActiveRun: true
    });
    assert.equal(contentRes.isError, undefined);
    assert.equal(contentRes.structuredContent.configStatus.sciverse_api_key.configured, true);
    const sc = contentRes.structuredContent.sciverseContent;
    assert.ok(sc);
    assert.equal(sc.offset, 300);
    assert.ok(sc.exported_excerpt_path);
    assert.ok(existsSync(sc.exported_excerpt_path));
    assert.equal(sc.downloaded_figures.length, 1);
    assert.ok(existsSync(sc.downloaded_figures[0].saved_path));

    const matRes = await call("search_sciverse", {
      mode: "schema-materials",
      schemaId: "2008.12187v1-f78968c8e105",
      goal: "reproduction"
    });
    assert.equal(matRes.isError, undefined);
    const sm = matRes.structuredContent.sciverseMaterials;
    assert.ok(sm);
    assert.equal(sm.goal, "reproduction");
    assert.ok(sm.exported_materials_path);
    assert.ok(existsSync(sm.exported_materials_path));
    assert.ok(!JSON.stringify(matRes).includes("sci_secret_unit_key_777"));
  });

  await t.test("v1.9.0 doc2md: pick_system_path, list_zotero_pdf_items, run_doc2md research+SI & preview server", async () => {
    reset();
    process.env.SANMU_TEST_NO_BROWSER = "1";
    await call("save_config", {
      mineruApiToken: "mineru_secret_token_999",
      doc2mdSubdir: "custom-kb-out"
    });

    const docsDir = join(workspace, "source-docs");
    mkdirSync(docsDir, { recursive: true });
    const mainPdfPath = join(docsDir, "Photocatalytic-Ni.pdf");
    const siPdfPath = join(docsDir, "Photocatalytic-Ni-SI.pdf");
    writePdf(mainPdfPath, "Single Atom Ni Photocatalyst. See Figure S1 and formula $E = h\\nu$.");
    writePdf(siPdfPath, "Supporting Information. Figure S1 TEM image of single atom Ni.");

    // 1. Test pick_system_path with automatic sibling SI detection
    process.env.SANMU_TEST_PICK_PATH = mainPdfPath;
    const picked = await call("pick_system_path", { mode: "file", filter: "pdf" });
    delete process.env.SANMU_TEST_PICK_PATH;
    assert.equal(picked.isError, undefined);
    assert.equal(picked.structuredContent.canceled, false);
    assert.equal(picked.structuredContent.path, mainPdfPath);
    assert.equal(picked.structuredContent.siblingSiPdfPath, siPdfPath);

    // 2. Test list_zotero_pdf_items with main + SI PDF redirect resolution
    fetchImpl = async (url) => {
      const u = String(url);
      if (u.endsWith("/api/")) {
        return new Response("", { status: 200, headers: { "zotero-server-id": "zot-doc2md", "zotero-api-version": "3" } });
      }
      if (u.includes("/collections?")) {
        return Response.json([{ key: "COL12345", data: { name: "Photocatalysis", parentCollection: false } }]);
      }
      if (u.includes("/items/top")) {
        return Response.json([
          {
            key: "ZITEM001",
            data: {
              key: "ZITEM001",
              itemType: "journalArticle",
              title: "Single Atom Ni Photocatalyst",
              creators: [
                { creatorType: "author", firstName: "Sen", lastName: "Lin" },
                { creatorType: "author", firstName: "Alice", lastName: "Wang" }
              ],
              date: "2025-05",
              publicationTitle: "Advanced Materials",
              DOI: "10.1002/adma.202500001",
              collections: ["COL12345"],
              tags: [{ tag: "Ni-SAC" }]
            }
          }
        ]);
      }
      if (u.includes("/items/ZITEM001/children")) {
        return Response.json([
          {
            key: "ZATTMAIN",
            data: { key: "ZATTMAIN", itemType: "attachment", contentType: "application/pdf", title: "Full Text PDF", filename: "main.pdf" }
          },
          {
            key: "ZATTSI01",
            data: { key: "ZATTSI01", itemType: "attachment", contentType: "application/pdf", title: "Supporting Information", filename: "main-SI.pdf" }
          }
        ]);
      }
      if (u.endsWith("/items/ZATTMAIN/file")) {
        return new Response("", { status: 302, headers: { location: pathToFileURL(mainPdfPath).href } });
      }
      if (u.endsWith("/items/ZATTSI01/file")) {
        return new Response("", { status: 302, headers: { location: pathToFileURL(siPdfPath).href } });
      }
      return new Response("not found", { status: 404 });
    };

    const zotList = await call("list_zotero_pdf_items", { onlyWithPdf: true });
    assert.equal(zotList.isError, undefined);
    const zItems = zotList.structuredContent.zoteroPdfList.items;
    assert.equal(zItems.length, 1);
    assert.equal(zItems[0].itemKey, "ZITEM001");
    assert.equal(zItems[0].mainPdfPath, mainPdfPath);
    assert.equal(zItems[0].siPdfPath, siPdfPath);
    assert.match(zItems[0].suggestedFolderName, /Lin 等 - 2025 - Single Atom Ni Photocatalyst/);

    // 3. Test run_doc2md (research mode + SI + metadata binding + custom saveDir)
    const customSaveDir = join(workspace, "01-科研基石", "光催化");
    const docRes = await call("run_doc2md", {
      mode: "research",
      ocrEngine: "local_fast",
      pdfPath: mainPdfPath,
      siPdfPath,
      saveDir: customSaveDir,
      paperKey: "doi:10.1002/adma.202500001",
      zoteroItemKey: "ZITEM001",
      metadata: zItems[0].metadata
    });
    assert.equal(docRes.isError, undefined);
    assert.ok(!JSON.stringify(docRes).includes("mineru_secret_token_999"));
    const job = docRes.structuredContent.activeDoc2mdJob;
    assert.ok(job);
    assert.equal(job.status, "completed");
    assert.ok(existsSync(job.outDir));
    assert.ok(existsSync(job.siOutDir));
    assert.ok(job.stats.primaryMarkdownPath && existsSync(job.stats.primaryMarkdownPath));
    assert.equal(job.stats.mathPassed, true);

    const mainMdText = readFileSync(job.stats.primaryMarkdownPath, "utf8");
    assert.match(mainMdText, /doi:\s*10\.1002\/adma\.202500001/i);
    assert.match(mainMdText, /\[\[.*- SI#Figure S1\|Figure S1\]\]/);
    const siMdText = readFileSync(job.stats.siMarkdownPath, "utf8");
    assert.match(siMdText, /关联主文/);

    // Check paper evidenceMap automatically updated with doc2md_out_dir
    const ev = docRes.structuredContent.evidenceMap["doi:10.1002/adma.202500001"];
    assert.ok(ev);
    assert.equal(ev.doc2md_out_dir, job.outDir);
    assert.equal(ev.stage, "identity_checked");

    // 4. Test manage_doc2md preview & stop_preview, doctor, rebuild & slides/book modes
    const docDoctor = await call("manage_doc2md", { action: "doctor" });
    assert.equal(docDoctor.isError, undefined);
    assert.equal(docDoctor.structuredContent.doc2mdDoctor.status, "pass");

    const prevRes = await call("manage_doc2md", { action: "preview", jobId: job.jobId, openInBrowser: false });
    assert.equal(prevRes.isError, undefined);
    assert.ok(prevRes.structuredContent.previewServer?.url?.startsWith("http://127.0.0.1:"));
    const stopRes = await call("manage_doc2md", { action: "stop_preview", jobId: job.jobId });
    assert.equal(stopRes.isError, undefined);
    assert.equal(stopRes.structuredContent.stoppedPreviews, 1);

    // 5. Test slides mode with real .pptx generated via python-pptx
    const pptxPath = join(docsDir, "Lecture-01.pptx");
    execFileSync("python", [
      "-c",
      "import sys; from pptx import Presentation; prs = Presentation(); slide = prs.slides.add_slide(prs.slide_layouts[0]); slide.shapes.title.text = 'Photocatalysis Lecture 1'; slide.placeholders[1].text = 'Bandgap formula $E_g = 2.7\\\\text{ eV}$'; prs.save(sys.argv[1])",
      pptxPath
    ]);
    const slideRes = await call("run_doc2md", {
      mode: "slides",
      ocrEngine: "local_fast",
      pptxPath,
      saveDir: customSaveDir
    });
    assert.equal(slideRes.isError, undefined);
    const slideJob = slideRes.structuredContent.activeDoc2mdJob;
    assert.equal(slideJob.status, "completed");
    assert.ok(existsSync(slideJob.stats.primaryMarkdownPath));
    assert.match(readFileSync(slideJob.stats.primaryMarkdownPath, "utf8"), /Photocatalysis Lecture 1/);

    // 6. Test book mode
    const bookRes = await call("run_doc2md", {
      mode: "book",
      ocrEngine: "local_fast",
      pdfPath: mainPdfPath,
      saveDir: customSaveDir
    });
    assert.equal(bookRes.isError, undefined);
    assert.equal(bookRes.structuredContent.activeDoc2mdJob.status, "completed");

    // 7. Test delete job record preserves delivered Markdown
    const delRes = await call("manage_doc2md", { action: "delete", jobId: slideJob.jobId });
    assert.equal(delRes.isError, undefined);
    assert.equal(existsSync(slideJob.stats.primaryMarkdownPath), true);
  });
});
