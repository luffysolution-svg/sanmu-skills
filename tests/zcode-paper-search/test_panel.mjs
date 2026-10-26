import test from 'node:test';
import assert from 'node:assert/strict';
import { makePanel, initialize, flush, emptyState, paper } from './panel-harness.mjs';

const tools = (p) => p.posted.filter((msg) => msg.method === 'tools/call');
const answer = (p, sc = emptyState()) => p.reply(tools(p).at(-1), { structuredContent: sc });
const set = (p, id, value) => { p.document.getElementById(id).value = value; };
const click = async (p, id) => { p.document.getElementById(id).click(); await flush(); };

test('host bridge ignores other windows and request-shaped echoes, pins parent origin, clears RPC timers', async () => {
  const p = makePanel({ resizeObserver: false });
  const ready = p.ensureInitialized();
  const req = p.posted.at(-1);
  p.reply(req, {}, { source: {} });
  assert.equal(p.pendingRpc.size, 1);
  p.window.dispatchEvent({ type: 'message', source: p.window.parent, origin: 'https://host.test', data: req });
  assert.equal(p.pendingRpc.size, 1);
  p.reply(req, { hostContext: { theme: 'dark' } });
  await ready;
  assert.equal(p.document.documentElement.dataset.theme, 'dark');
  assert.equal(p.pendingRpc.size, 0);
  assert.equal(p.timers.size, 0);
  const request = p.sendRpcRequest('test');
  assert.equal(p.posted.at(-1).targetOrigin, 'https://host.test');
  p.reply(p.posted.at(-1), { ok: true }, { origin: 'https://untrusted.test' });
  assert.equal(p.pendingRpc.size, 1);
  p.reply(p.posted.at(-1), { ok: true });
  assert.equal((await request).ok, true);
  p.notify(emptyState(), {});
  assert.equal(p.state.panelData, null);
  p.notify(emptyState());
  assert.equal(p.state.panelData.activeRun, null);
});

test('initialization timeout can retry and tool calls serialize before and after handshake', async () => {
  const p = makePanel();
  const firstInit = p.ensureInitialized();
  const rejection = assert.rejects(firstInit, /请求超时/);
  [...p.timers.values()][0].fn();
  await rejection;
  assert.equal(p.pendingRpc.size, 0);
  await initialize(p);
  const a = p.callTool('search_papers', { query: 'a' });
  const b = p.callTool('manage_basket', { action: 'clear' });
  await flush();
  assert.equal(tools(p).length, 1);
  assert.equal(p.state.busy, true);
  answer(p);
  await a;
  await flush();
  assert.equal(tools(p).length, 2);
  assert.equal(p.state.busy, true);
  answer(p);
  await b;
  assert.equal(p.state.busy, false);
  assert.equal(p.pendingRpc.size, 0);
});

test('failed async UI action reports error without navigation or stuck loading, next action succeeds', async () => {
  const p = makePanel();
  await initialize(p);
  set(p, 'qSearch', 'photocatalytic hydrogen');
  await click(p, 'btnExecSearch');
  p.reply(tools(p).at(-1), { isError: true, structuredContent: { error: { message: 'source unavailable' } } });
  await flush();
  assert.match(p.document.getElementById('bannerText').textContent, /source unavailable/);
  assert.equal(p.state.activeTab, 'search');
  assert.equal(p.state.busy, false);
  assert.equal(p.document.getElementById('btnExecSearch').disabled, false);
  await click(p, 'btnExecSearch');
  answer(p);
  await flush();
  assert.equal(p.state.activeTab, 'results');
});

test('stable IDs and publisher URLs route to lookup with canonical identifiers', async () => {
  const p = makePanel();
  await initialize(p);
  const cases = [
    ['doi:10.1234/example', '10.1234/example'],
    ['https://doi.org/10.1038/s41586-021-03819-2', '10.1038/s41586-021-03819-2'],
    ['https://dx.doi.org/10.1234/example', '10.1234/example'],
    ['https://arxiv.org/pdf/2401.00001.pdf', 'arxiv:2401.00001'],
    ['2401.00001v2', 'arxiv:2401.00001v2'],
    ['https://pubmed.ncbi.nlm.nih.gov/39575807/', 'pmid:39575807'],
    ['39575807', 'pmid:39575807'],
    ['W123456', 'openalex:W123456'],
    ['https://openalex.org/W123456', 'openalex:W123456'],
    ['pmcid:PMC12710600', 'pmcid:PMC12710600']
  ];
  for (const [raw, id] of cases) {
    assert.equal(p.normalizeDirectId(raw), id);
    assert.equal(p.isDirectPaperIdentifier(raw), true);
    set(p, 'qSearch', raw);
    await click(p, 'btnExecSearch');
    assert.equal(tools(p).at(-1).params.name, 'lookup_paper');
    assert.equal(tools(p).at(-1).params.arguments.id, id);
    answer(p);
    await flush();
  }
  assert.equal(p.isDirectPaperIdentifier('doi:'), false);
  assert.equal(p.isDirectPaperIdentifier('single atom Ni'), false);
});

test('all forms reject out-of-range numbers before MCP; reversed years and IME Enter do not search', async () => {
  const p = makePanel();
  await initialize(p);
  for (const [queryId, buttonId, limitId] of [
    ['qSearch', 'btnExecSearch', 'qSearchLimit'],
    ['qExtQuery', 'btnExecExtSearch', 'qExtLimit'],
    ['qAuthorName', 'btnExecAuthor', 'qAuthorLimit'],
    ['qEpmcQuery', 'btnExecEpmc', 'qEpmcLimit']
  ]) {
    set(p, queryId, 'research topic');
    for (const invalid of ['0', '-1', '21', '1.5', 'Infinity']) {
      set(p, limitId, invalid);
      await click(p, buttonId);
      assert.equal(tools(p).length, 0);
      assert.match(p.document.getElementById('bannerText').textContent, /1 至 20/);
    }
    set(p, limitId, '10');
  }
  set(p, 'qSearchYearFrom', '2026');
  set(p, 'qSearchYearTo', '2020');
  await click(p, 'btnExecSearch');
  assert.equal(tools(p).length, 0);
  set(p, 'qSearchYearFrom', '');
  set(p, 'qSearchYearTo', '');
  p.document.getElementById('qSearch').dispatchEvent({ type: 'keydown', key: 'Enter', isComposing: true });
  await flush();
  assert.equal(tools(p).length, 0);
});

test('Serper/Exa, author seed and EPMC forms send the supported route and canonical PMID', async () => {
  const p = makePanel();
  await initialize(p);
  set(p, 'qExtQuery', 'NiO hydrogen');
  set(p, 'qExtChannel', 'exa:auto');
  await click(p, 'btnExecExtSearch');
  assert.equal(tools(p).at(-1).params.name, 'search_exa');
  answer(p); await flush();
  set(p, 'qExtChannel', 'serper:patents');
  await click(p, 'btnExecExtSearch');
  assert.equal(tools(p).at(-1).params.arguments.type, 'patents');
  answer(p); await flush();
  set(p, 'qAuthorFromPaper', 'https://doi.org/10.1234/example');
  await click(p, 'btnExecAuthor');
  assert.equal(tools(p).at(-1).params.arguments.fromPaper, '10.1234/example');
  answer(p); await flush();
  assert.equal(p.document.getElementById('qEpmcFetch'), null);
  set(p, 'qEpmcQuery', '39575807');
  await click(p, 'btnExecEpmc');
  const args = tools(p).at(-1).params.arguments;
  assert.equal(args.id, 'pmid:39575807');
  assert.equal(args.query, undefined);
  assert.equal(args.fetchMode, 'none');
  answer(p); await flush();
});

test('inline drawers render for second and later papers in main and basket lists, and streamlined UI removes redundant export buttons', async () => {
  const p = makePanel();
  await initialize(p);
  const papers = [paper('first'), paper('second'), paper('third')];
  const sub = { runId: 'sub', papers: [paper('related')] };
  for (const item of papers) p.state.inlineSubRuns.set(item.paper_key, { bundle: sub, label: 'Related' });
  p.updatePanelData({ ...emptyState(), activeRun: { runId: 'main', papers }, basket: papers, recentRuns: [{ runId: 'main' }, { runId: 'sub' }] });
  for (const id of ['mainPapersList', 'basketListContainer']) {
    for (const key of ['first', 'second', 'third']) {
      assert.match(p.document.getElementById(id).innerHTML, new RegExp(`data-sub-parent="${key}"`));
    }
  }
  for (const removedId of ['btnExportMdCards', 'btnExportCitations', 'btnExportRunBundle', 'btnExportGraphSvg', 'qEpmcFetch', 'btnSaveDefaultDir']) {
    assert.equal(p.document.getElementById(removedId), null);
  }
  assert.equal(p.document.querySelector('[data-cite="ris"]'), null);
  assert.ok(p.document.getElementById('btnBatchSaveBasketToZotero'));
  p.inspectPaper(papers[0]);
  assert.equal(p.document.getElementById('drwExportSingleMd'), null);
  assert.equal(p.document.getElementById('drwEvStage'), null);
  assert.equal(p.document.getElementById('drwEvBasis'), null);
  assert.equal(p.document.getElementById('drwEvReason'), null);
  assert.ok(p.document.getElementById('drwFetchEpmcAll'));
  await click(p, 'drwFetchEpmcAll');
  assert.equal(tools(p).at(-1).params.name, 'search_epmc');
  assert.equal(tools(p).at(-1).params.arguments.fetchMode, 'suppl');
  answer(p); await flush();
});

test('source status, candidate affiliations, paper labels and SVG text escape external HTML', () => {
  const p = makePanel();
  const attack = '<img src=x onerror=alert(1)>';
  const item = { ...paper(), title: attack, index: attack };
  p.state.inlineSubRuns.set(item.paper_key, { label: attack, bundle: { runId: 'sub', run: { author_candidates: [{ name: attack, affiliations: [attack] }] } } });
  const card = p.renderPaperCardHtml(item);
  assert.ok(!card.includes(attack));
  assert.match(card, /&lt;img/);
  p.state.panelData = emptyState();
  p.renderActiveRun({ runId: 'test', run: { source_status: { x: { status: attack, error: attack } }, diff: { counts: { added: attack } } }, papers: [] });
  assert.ok(!p.document.getElementById('activeRunHeader').innerHTML.includes(attack));
  assert.ok(!p.document.getElementById('activeRunContent').innerHTML.includes(attack));
  p.renderRelationshipGraph({ graphData: { nodes: [{ id: 'x', title: attack, shortLabel: attack, paper: item }], edges: [] } });
  assert.ok(!p.document.getElementById('graphSvg').innerHTML.includes(attack));
});

test('unsafe external links and download URLs never reach the host or popup', async () => {
  const p = makePanel();
  await initialize(p);
  for (const url of ['javascript:alert(1)', 'data:text/html,<script>x</script>', 'file:///C:/secret', 'https://user:pass@example.org/x']) {
    await p.openExternalUrl(url);
    assert.equal(p.window.opened, undefined);
  }
  assert.equal(p.posted.filter((msg) => msg.method === 'ui/open-link').length, 0);
  await assert.rejects(p.callTool('resolve_oa_and_pdf', { action: 'download_pdf', pdfUrl: 'http://example.org/a.pdf' }), /HTTPS/);
  assert.equal(tools(p).length, 0);
  const download = p.callTool('resolve_oa_and_pdf', { action: 'download_pdf', pdfUrl: 'https://example.org/a.pdf', fallbackUrls: ['javascript:x', 'https://example.org/b.pdf'] });
  await flush();
  assert.deepEqual(Array.from(tools(p).at(-1).params.arguments.fallbackUrls), ['https://example.org/b.pdf']);
  answer(p); await download;
});

test('only plugin-created Zotero records expose undo; non-paper raw results expose no paper actions', async () => {
  const p = makePanel();
  const item = { ...paper(), zotero_match: { matched: true, itemKey: 'ABCDEFGH', createdByPlugin: false } };
  assert.ok(!p.renderPaperCardHtml(item).includes('zotero-undo-btn'));
  p.inspectPaper(item);
  assert.equal(p.document.getElementById('drwUndoZoteroItem'), null);
  await assert.rejects(p.executeUndoZoteroImport('ABCDEFGH', item.paper_key), /仅可撤销/);
  item.zotero_match.createdByPlugin = true;
  assert.match(p.renderPaperCardHtml(item), /zotero-undo-btn/);
  p.inspectPaper(item);
  assert.ok(p.document.getElementById('drwUndoZoteroItem'));
  const raw = p.renderRawResultCardHtml({ title: 'University', serper_type: 'maps', url: 'https://example.org' }, 0);
  assert.ok(!raw.includes('raw-basket-btn'));
  assert.ok(!raw.includes('raw-enrich-abs-btn'));
  assert.ok(!raw.includes('raw-zotero-save-btn'));
});

test('failed history navigation rolls back its index and successful back/forward preserves stack', async () => {
  const p = makePanel();
  await initialize(p);
  const data = { ...emptyState(), recentRuns: [{ runId: 'a' }, { runId: 'b' }], runNavHistory: [{ runId: 'a' }, { runId: 'b' }] };
  p.updatePanelData({ ...data, activeRun: { runId: 'a' } });
  p.updatePanelData({ ...data, activeRun: { runId: 'b' } });
  await click(p, 'btnNavBackRun');
  p.reply(tools(p).at(-1), { isError: true, content: [{ text: 'load failed' }] });
  await flush();
  assert.equal(p.state.navIndex, 1);
  assert.equal(p.state.navSwitching, false);
  await click(p, 'btnNavBackRun');
  answer(p, { ...data, activeRun: { runId: 'a' } });
  await flush();
  assert.equal(p.state.navIndex, 0);
  assert.deepEqual(Array.from(p.state.navStack), ['a', 'b']);
  await click(p, 'btnNavForwardRun');
  answer(p, { ...data, activeRun: { runId: 'b' } });
  await flush();
  assert.equal(p.state.navIndex, 1);
});

test('failed history deletion preserves selections for retry', async () => {
  const p = makePanel();
  await initialize(p);
  const data = { ...emptyState(), recentRuns: [{ runId: 'a' }, { runId: 'b' }] };
  p.updatePanelData(data);
  p.state.selectedRuns.add('a');
  p.state.selectedRuns.add('b');
  await click(p, 'btnDeleteSelectedRuns');
  p.reply(tools(p).at(-1), { isError: true, content: [{ text: 'delete failed' }] });
  await flush();
  assert.deepEqual(Array.from(p.state.selectedRuns), ['a', 'b']);
  await click(p, 'btnConfirmClearAllRuns');
  p.reply(tools(p).at(-1), { isError: true, content: [{ text: 'clear failed' }] });
  await flush();
  assert.deepEqual(Array.from(p.state.selectedRuns), ['a', 'b']);
});

test('folder and run-file open failures are caught by UI actions', async () => {
  const p = makePanel();
  await initialize(p);
  p.updatePanelData({ ...emptyState(), activeRun: {
    runId: 'a', run: { exported_save_dir: 'C:/exports' },
    deliverableFiles: [{ path: 'report.md', size: 100 }],
    acquisition: { saved_dir: 'C:/pdf', saved_pdf_path: 'C:/pdf/paper.pdf' }
  }, recentRuns: [{ runId: 'a' }] });
  for (const element of [p.document.getElementById('btnOpenExportedRunDir'),
    p.document.querySelector('.run-file-btn'), p.document.getElementById('btnOpenAcqFolder')]) {
    assert.ok(element);
    element.click();
    await flush();
    assert.equal(tools(p).at(-1).params.name, 'read_run_file');
    p.reply(tools(p).at(-1), { isError: true, content: [{ text: 'open failed' }] });
    await flush();
    assert.match(p.document.getElementById('bannerText').textContent, /open failed/);
    assert.equal(p.state.busy, false);
  }
});

test('graph letterboxing, timeline years, hidden tab RAF, clearing maps and pointer cancel cleanup', () => {
  const p = makePanel();
  const svg = p.document.getElementById('graphSvg');
  svg.rect = { left: 0, top: 0, width: 360, height: 430 };
  const center = p.clientToGraphWorld(svg, 180, 215);
  assert.equal(center.x, 360);
  assert.equal(center.y, 215);
  assert.equal(p.clientToGraphWorld(svg, 180, 107.5).y, 0);
  const nodes = [{ year: 1960 }, { year: 2040 }, { year: 2020 }, { year: null }];
  p.computeTimelineTargets(nodes);
  assert.ok(nodes.every((n) => Number.isFinite(n.targetX) && n.targetX >= 60 && n.targetX <= 660));
  p.computeTimelineTargets([{ year: 2024 }, { year: '2024' }]);
  const data = { graphData: { nodes: [{ id: 'n', title: 'Node', shortLabel: 'Node', paper: paper() }], edges: [] } };
  p.renderRelationshipGraph(data);
  assert.equal(p.frames.size, 0);
  p.switchTab('graph');
  assert.equal(p.frames.size, 1);
  svg.onpointerdown({ button: 0, target: svg, clientX: 20, clientY: 20 });
  assert.equal(p.window.listeners.get('pointermove').size, 1);
  p.window.dispatchEvent({ type: 'pointercancel' });
  assert.equal(p.window.listeners.get('pointermove').size, 0);
  p.switchTab('search');
  assert.equal(p.frames.size, 0);
  p.renderRelationshipGraph(emptyState());
  assert.equal(p.graphSim.nodeById.size, 0);
  assert.equal(p.graphSim.nodeElMap.size, 0);
  assert.equal(p.graphSim.viewportEl, null);
});

test('pagehide releases observer, pointer handlers, frames and pending RPC', async () => {
  const p = makePanel();
  await initialize(p);
  const request = p.sendRpcRequest('pending');
  const rejected = assert.rejects(request, /面板已关闭/);
  p.window.dispatchEvent({ type: 'pagehide' });
  await rejected;
  assert.equal(p.observerDisconnected, true);
  assert.equal(p.pendingRpc.size, 0);
  assert.equal(p.timers.size, 0);
  assert.equal(p.frames.size, 0);
});

test('clipboard rejection shows an error instead of a false copied success', async () => {
  const p = makePanel({ clipboard: { writeText: async () => { throw new Error('denied'); } } });
  await click(p, 'btnCopyCitation');
  assert.match(p.document.getElementById('bannerText').textContent, /复制失败/);
  assert.ok(p.document.getElementById('bannerBox').classList.contains('error'));
});
