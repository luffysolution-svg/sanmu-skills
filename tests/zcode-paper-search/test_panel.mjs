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

test('failed inline note save preserves the stored note and can retry the same edit', async () => {
  const p = makePanel();
  await initialize(p);
  const item = { ...paper('note-retry'), user_tags: ['original'], user_note: 'Stored note' };
  const data = { ...emptyState(), activeRun: { runId: 'notes', papers: [item] } };
  p.updatePanelData(data);
  const bar = p.document.querySelector('.inline-note-tags-bar');
  const note = bar.querySelector('.inline-note-input');
  note.value = 'Revised note';
  note.dispatchEvent({ type: 'change' });
  await flush();
  const failed = tools(p).at(-1);
  p.reply(failed, { isError: true, structuredContent: { error: { message: 'Disk full' } } });
  await flush();
  assert.equal(p.state.panelData.activeRun.papers[0].user_note, 'Stored note');
  assert.equal(note.value, 'Revised note');
  note.dispatchEvent({ type: 'change' });
  await flush();
  assert.equal(tools(p).length, 2);
  assert.equal(tools(p).at(-1).params.arguments.user_note, 'Revised note');
  answer(p, { ...data, activeRun: { runId: 'notes', papers: [{ ...item, user_note: 'Revised note' }] } });
  await flush();
  assert.equal(p.state.panelData.activeRun.papers[0].user_note, 'Revised note');
});

test('failed detail note save does not change the paper used by later actions', async () => {
  const p = makePanel();
  await initialize(p);
  const item = { ...paper('detail-note'), user_tags: ['original'], user_note: 'Stored note' };
  p.updatePanelData({ ...emptyState(), basket: [item] });
  p.inspectPaper(item);
  set(p, 'drwUserNoteInput', 'Unsaved edit');
  await click(p, 'drwSaveNoteTagsBtn');
  p.reply(tools(p).at(-1), { isError: true, structuredContent: { error: { message: 'Disk full' } } });
  await flush();
  assert.equal(item.user_note, 'Stored note');
  assert.equal(p.document.getElementById('drwUserNoteInput').value, 'Unsaved edit');
});

test('inline note revert while a save is pending is queued and persists the latest edit', async () => {
  const p = makePanel();
  await initialize(p);
  const item = { ...paper('note-revert'), user_note: 'Stored note' };
  const data = { ...emptyState(), activeRun: { runId: 'notes', papers: [item] } };
  p.updatePanelData(data);
  const note = p.document.querySelector('.inline-note-input');
  note.value = 'Temporary edit';
  note.dispatchEvent({ type: 'change' });
  await flush();
  note.value = 'Stored note';
  note.dispatchEvent({ type: 'change' });
  await flush();
  answer(p, { ...data, activeRun: { runId: 'notes', papers: [{ ...item, user_note: 'Temporary edit' }] } });
  await flush();
  assert.equal(tools(p).length, 2);
  assert.equal(tools(p).at(-1).params.arguments.user_note, 'Stored note');
  answer(p, { ...data, activeRun: { runId: 'notes', papers: [{ ...item, user_note: 'Stored note' }] } });
  await flush();
  assert.equal(p.document.querySelector('.inline-note-input').value, 'Stored note');
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
  p.document.getElementById('citationPreviewBox').textContent = '[1] Test citation.';
  await click(p, 'btnCopyCitation');
  assert.match(p.document.getElementById('bannerText').textContent, /复制失败/);
  assert.ok(p.document.getElementById('bannerBox').classList.contains('error'));
});

test('graph docked tooltip prevents hover flicker, caches DOM key, and easyScholar / AI4Scholar modes work', async () => {
  const p = makePanel();
  await initialize(p);
  const p1 = { ...paper('p1'), title: 'First Paper Title', venue_rank: { is_top_tier: true, if: 29.1, badges: ['🏆 材料科学1区 TOP', 'IF 29.1'], summary: '中科院1区' } };
  const p2 = { ...paper('p2'), title: 'Second Paper With A Much Longer Title That Previously Wrapped Multiple Lines', venue_rank: { is_top_tier: false, if: 3.5, badges: ['📊 化学4区'] } };
  p.updatePanelData({
    ...emptyState(),
    configStatus: { ai4scholar_api_key: { configured: true, source: 'panel' }, easyscholar_secret_key: { configured: true, source: 'panel' } },
    activeRun: { runId: 'main', papers: [p1, p2] },
    graphData: {
      centerNodeId: 'doi:10.1234/p1',
      nodes: [
        { id: 'doi:10.1234/p1', paperKey: 'doi:10.1234/p1', title: p1.title, shortLabel: 'Lee (2025)', year: 2025, firstAuthor: 'Alice Lee', citationCount: 50, role: 'seed', paper: p1 },
        { id: 'doi:10.1234/p2', paperKey: 'doi:10.1234/p2', title: p2.title, shortLabel: 'Smith (2024)', year: 2024, firstAuthor: 'Bob Smith', citationCount: 10, role: 'citation', paper: p2 }
      ],
      edges: [{ source: 'doi:10.1234/p2', target: 'doi:10.1234/p1', type: 'cites', label: '施引' }]
    }
  });

  // Verify easyScholar badge rendering and top_tier / if_desc filtering & sorting via DOM controls
  assert.match(p.document.getElementById('mainPapersList').innerHTML, /材料科学1区 TOP/);
  p.document.querySelector('.filter-chip[data-flt="top_tier"]').click();
  assert.match(p.document.getElementById('mainPapersList').innerHTML, /First Paper Title/);
  assert.ok(!p.document.getElementById('mainPapersList').innerHTML.includes('Second Paper With A Much Longer Title'));
  p.document.querySelector('.filter-chip[data-flt="all"]').click();
  const sortSel = p.document.getElementById('selResultSort');
  sortSel.value = 'if_desc';
  sortSel.dispatchEvent({ type: 'change', target: sortSel });
  const listHtml = p.document.getElementById('mainPapersList').innerHTML;
  assert.ok(listHtml.indexOf('First Paper Title') < listHtml.indexOf('Second Paper With A Much Longer Title'));

  // Verify graph hover tooltip is docked outside #graphStageContainer and uses solid node-hit circle + debounce timer
  const stage = p.document.getElementById('graphStageContainer');
  const tooltip = p.document.getElementById('graphHoverTooltip');
  assert.equal(stage.querySelector('#graphHoverTooltip'), null);
  assert.match(p.document.getElementById('graphSvg').innerHTML, /class="node-hit"/);
  const firstKey = p.graphSim.lastTooltipKey;
  assert.ok(firstKey.includes('doi:10.1234/p1'));

  // Simulate pointerenter on node 2 -> pointerleave (grace timer active, no immediate snap back) -> pointerenter on node 1
  const n2El = p.graphSim.nodeElMap.get('doi:10.1234/p2');
  const n1El = p.graphSim.nodeElMap.get('doi:10.1234/p1');
  n2El.dispatchEvent({ type: 'pointerenter' });
  assert.equal(p.graphSim.hoveredNodeId, 'doi:10.1234/p2');
  n2El.dispatchEvent({ type: 'pointerleave' });
  assert.equal(p.graphSim.hoveredNodeId, 'doi:10.1234/p2'); // Still p2 during 70ms grace window!
  assert.ok(p.graphSim.hoverLeaveTimer !== null);
  n1El.dispatchEvent({ type: 'pointerenter' });
  assert.equal(p.graphSim.hoveredNodeId, 'doi:10.1234/p1');
  assert.equal(p.graphSim.hoverLeaveTimer, null);
  p.switchTab('search');
  assert.equal(p.timers.size, 0);

  // Verify AI4Scholar search & studio modes dispatch the right MCP tools
  set(p, 'qExtChannel', 'ai4s:patents');
  set(p, 'qExtQuery', 'single atom photocatalyst');
  set(p, 'qExtPatentCountry', 'CN,US');
  await click(p, 'btnExecExtSearch');
  assert.equal(tools(p).at(-1).params.name, 'search_ai4scholar');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'patents');
  assert.equal(tools(p).at(-1).params.arguments.country, 'CN,US');
  answer(p); await flush();

  set(p, 'qStudioToolMode', 'draw:smart');
  set(p, 'qStudioPrompt', 'Single-atom Ni/NiOx photocatalytic H2 evolution schematic');
  await click(p, 'btnExecStudio');
  assert.equal(tools(p).at(-1).params.name, 'ai4scholar_draw_and_cite');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'draw');
  assert.equal(tools(p).at(-1).params.arguments.drawAction, 'smart');
  answer(p); await flush();

  // Verify fallback source badge, gs_citations card, and google-scholar author works routing
  p.renderActiveRun({
    runId: 'gs-cite-run',
    run: {
      source_status: { 'semantic-scholar': { status: 'error', error: 'HTTP 429', fetched: 5, fallback_used: 'openalex' } },
      gs_citations: { citations: [{ title: 'MLA', snippet: 'Hei, X. Catalysts 2026.' }], links: [{ name: 'BibTeX', link: 'https://scholar.google.com/a.bib' }] },
      author_candidates: [{ provider: 'google-scholar', author_id: 'GS_AUTH_1', name: 'Bolong Huang', affiliations: ['HKPolyU'] }]
    },
    papers: []
  });
  assert.match(p.document.getElementById('activeRunHeader').innerHTML, /已由开放引文自动补齐 \(5条\)/);
  assert.match(p.document.getElementById('activeRunContent').innerHTML, /Google Scholar 标准引用格式/);
  p.document.querySelector('.fetch-author-works-btn').click();
  await flush();
  assert.equal(tools(p).at(-1).params.name, 'search_ai4scholar');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'gs-author');
  assert.equal(tools(p).at(-1).params.arguments.id, 'GS_AUTH_1');
  answer(p); await flush();
});

test('v1.7.0 panel features: topic/author sort & topic filter, comprehensive recommendation sort, claim browser PDF, deep-read handoff, basket tag filter, and graph intelligence', async () => {
  const p = makePanel();
  await initialize(p);

  // Req 1: Topic search sort dropdown (#qTopicSort) & Comprehensive Recommendation Sort (#paperSortSelect / #selResultSort)
  set(p, 'qSearch', 'single atom Ni photocatalytic hydrogen');
  set(p, 'qTopicSort', 'cited');
  await click(p, 'btnExecSearch');
  assert.equal(tools(p).at(-1).params.name, 'search_papers');
  assert.equal(tools(p).at(-1).params.arguments.sort, 'cited');
  answer(p); await flush();

  const pOrdinary = {
    ...paper('ord'),
    index: 1,
    title: 'Ordinary Hit First In Raw List',
    year: 2019,
    citation_count: 3,
    venue_rank: { is_top_tier: false, if: 2.1, badges: ['📊 化学4区'] }
  };
  const pTopCore = {
    ...paper('top'),
    index: 4,
    title: 'Advanced Materials Core Single Atom Ni Paper',
    year: 2025,
    citation_count: 185,
    local_pdf_path: 'F:/个人知识库/00-收件箱-待整理/文献检索/2025-Advanced Materials Core.pdf',
    epmc_saved_dir: 'F:/个人知识库/00-收件箱-待整理/文献检索/2025-Advanced_EPMC/supplementary',
    epmc_suppl_files: ['F:/个人知识库/00-收件箱-待整理/文献检索/2025-Advanced_EPMC/supplementary/si.pdf'],
    user_tags: ['单原子Ni', '光催化析氢'],
    user_note: '含 XPS Ni 2p 分峰与 AQY 表',
    venue_rank: { is_top_tier: true, if: 27.4, cas_up: '材料科学1区', jcr: 'Q1', badges: ['🏆 材料科学1区 TOP', 'JCR Q1', 'IF 27.4'], summary: '中科院1区 TOP' }
  };
  assert.ok(p.computePaperRecommendScore(pTopCore) > p.computePaperRecommendScore(pOrdinary));

  // Req 2: Author topic filter (#qAuthorTopic) & sort (#qAuthorSort) + candidate works button carry-over
  set(p, 'qAuthorName', 'Zhi Yang');
  set(p, 'qAuthorTopic', 'photocatalytic');
  set(p, 'qAuthorSort', 'cited');
  await click(p, 'btnExecAuthor');
  assert.equal(tools(p).at(-1).params.name, 'search_author');
  assert.equal(tools(p).at(-1).params.arguments.topic, 'photocatalytic');
  assert.equal(tools(p).at(-1).params.arguments.sort, 'cited');
  answer(p, {
    ...emptyState(),
    activeRun: {
      runId: 'auth-cand-run',
      run: {
        query: { text: 'Zhi Yang', topic: 'photocatalytic', sort: 'cited' },
        author_candidates: [{ provider: 'openalex', author_id: 'A5047449616', name: 'Zhi Yang', affiliations: ['ZJU'] }]
      },
      papers: []
    }
  });
  await flush();
  p.document.querySelector('.fetch-author-works-btn').click();
  await flush();
  assert.equal(tools(p).at(-1).params.name, 'search_author');
  assert.equal(tools(p).at(-1).params.arguments.authorId, 'A5047449616');
  assert.equal(tools(p).at(-1).params.arguments.topic, 'photocatalytic');
  assert.equal(tools(p).at(-1).params.arguments.sort, 'cited');
  answer(p); await flush();

  // Populate main run & basket with pOrdinary and pTopCore
  const pBasket2 = {
    ...paper('b2'),
    title: 'DFT Machine Learning Benchmark Paper',
    user_tags: ['材料AI'],
    user_note: 'CGCNN 基准对比'
  };
  const richState = {
    ...emptyState(),
    activeRun: { runId: 'main-170', papers: [pOrdinary, pTopCore] },
    basket: [pTopCore, pBasket2],
    graphData: {
      centerNodeId: 'doi:10.1234/seed1',
      nodes: [
        { id: 'doi:10.1234/seed1', paperKey: 'doi:10.1234/seed1', title: 'Seed 1', shortLabel: 'Seed1 (2025)', year: 2025, firstAuthor: 'A', citationCount: 20, role: 'seed', paper: paper('seed1') },
        { id: 'doi:10.1234/seed2', paperKey: 'doi:10.1234/seed2', title: 'Seed 2', shortLabel: 'Seed2 (2024)', year: 2024, firstAuthor: 'B', citationCount: 30, role: 'seed', paper: paper('seed2') },
        { id: 'doi:10.1234/top', paperKey: 'doi:10.1234/top', title: pTopCore.title, shortLabel: 'Core (2025)', year: 2025, firstAuthor: 'C', citationCount: 185, role: 'reference', paper: pTopCore }
      ],
      edges: [
        { source: 'doi:10.1234/seed1', target: 'doi:10.1234/top', type: 'cites', label: '引用参考文献' },
        { source: 'doi:10.1234/seed2', target: 'doi:10.1234/top', type: 'cites', label: '引用参考文献' }
      ]
    }
  };
  p.updatePanelData(richState);

  // Check #paperSortSelect alias & 'recommended' sort
  const sortSelect = p.document.getElementById('paperSortSelect');
  assert.ok(sortSelect);
  sortSelect.value = 'recommended';
  sortSelect.dispatchEvent({ type: 'change', target: sortSelect });
  const mainHtml = p.document.getElementById('mainPapersList').innerHTML;
  assert.ok(mainHtml.indexOf('Advanced Materials Core Single Atom Ni Paper') < mainHtml.indexOf('Ordinary Hit First In Raw List'));

  // Req 3: Claim browser PDF button on card without local_pdf_path
  const claimBtn = p.document.querySelector('.claim-browser-pdf-btn');
  assert.ok(claimBtn);
  claimBtn.click();
  await flush();
  assert.equal(tools(p).at(-1).params.name, 'resolve_oa_and_pdf');
  assert.equal(tools(p).at(-1).params.arguments.action, 'claim_browser_pdf');
  answer(p, richState); await flush();

  // Req 4: Deep-read handoff button (.deep-read-handoff-btn) on card with local_pdf_path / epmc_suppl_files
  const handoffBtn = p.document.querySelector('.deep-read-handoff-btn');
  assert.ok(handoffBtn);
  const promptText = p.buildDeepReadHandoffPrompt(pTopCore);
  assert.match(promptText, /\/sanmu-paper-reader/);
  assert.match(promptText, /si_probe\.py/);
  assert.match(promptText, /extract_si_figures\.py/);
  assert.match(promptText, /2025-Advanced Materials Core\.pdf/);
  assert.match(promptText, /supplementary\/si\.pdf/);
  assert.match(promptText, /材料科学1区 TOP/);

  // Req 5: Basket tag filter capsules (#basketTagFilterBar) & inline note/tag editing
  const tagBar = p.document.getElementById('basketTagFilterBar');
  assert.ok(!tagBar.classList.contains('hidden'));
  assert.match(tagBar.innerHTML, /单原子Ni/);
  assert.match(tagBar.innerHTML, /材料AI/);
  const aiChip = tagBar.querySelector('.basket-tag-chip[data-tag="材料AI"]');
  assert.ok(aiChip);
  aiChip.click();
  assert.match(p.document.getElementById('basketListContainer').innerHTML, /DFT Machine Learning Benchmark Paper/);
  assert.ok(!p.document.getElementById('basketListContainer').innerHTML.includes('Advanced Materials Core Single Atom Ni Paper'));

  // Req 6: Graph intelligence (Bridge & Co-cited Hub halos + Top 5 intelligence panel)
  const coreNode = p.graphSim.nodeById.get('doi:10.1234/top');
  assert.ok(coreNode);
  assert.equal(coreNode.inDegree, 2);
  assert.equal(coreNode.isCoCitedHub, true);
  assert.equal(coreNode.isBridgeNode, true);
  assert.match(p.document.getElementById('graphSvg').innerHTML, /桥梁/);
  assert.match(p.document.getElementById('graphSvg').innerHTML, /共被引×2/);
  const intelPanel = p.document.getElementById('graphIntelligencePanel');
  assert.ok(!intelPanel.classList.contains('hidden'));
  assert.match(intelPanel.innerHTML, /图谱核心情报：共被引基石文献 &amp; 桥梁文献 Top 5/);
  assert.match(intelPanel.innerHTML, /Advanced Materials Core Single Atom Ni Paper/);

  // v1.8.0: SciVerse channel in Mode 2 + paper card badges + detail view inline content & materials
  p.document.querySelector('[data-mode="serper"]').click();
  set(p, 'qExtChannel', 'sciverse:semantic-search');
  p.document.getElementById('qExtChannel').dispatchEvent({ type: 'change', target: p.document.getElementById('qExtChannel') });
  set(p, 'qExtQuery', 'How do single-atom Ni cocatalysts enhance H2 evolution?');
  set(p, 'qExtSciverseSemMode', 'quality');
  await click(p, 'btnExecExtSearch');
  assert.equal(tools(p).at(-1).params.name, 'search_sciverse');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'semantic-search');
  assert.equal(tools(p).at(-1).params.arguments.semanticMode, 'quality');

  const svPaper = {
    ...paper('sv1'),
    title: 'SciVerse Fulltext and Schema Paper',
    year: 2025,
    fwci: 3.85,
    sciverse_doc_id: 'b'.repeat(64),
    sciverse_schema_id: '2501.00001v1-abcdef123456',
    sciverse_saved_dir: 'F:/个人知识库/00-收件箱-待整理/文献检索/2025-SciVerse_SciVerse',
    sciverse_excerpt_path: 'F:/个人知识库/00-收件箱-待整理/文献检索/2025-SciVerse_SciVerse/fulltext_excerpt.md',
    sciverse_figure_files: ['F:/个人知识库/00-收件箱-待整理/文献检索/2025-SciVerse_SciVerse/figures/01_fig1.jpg'],
    sciverse_chunks: [{ chunk_id: 'ck1', doc_id: 'b'.repeat(64), offset: 1500, page_no: 4, score: 0.92, chunk: 'EXAFS confirms Ni-N4 coordination.' }],
    sciverse_schema: { schema_id: '2501.00001v1-abcdef123456', research_problem: 'Slow H2 evolution kinetics', central_contribution: 'Single-atom Ni/NiOx cocatalyst', headline_result: 'AQY reaches 28.5%', has_code: true }
  };
  answer(p, {
    ...emptyState(),
    configStatus: { ...emptyState().configStatus, sciverse_api_key: { configured: true, source: 'panel' } },
    activeRun: { runId: 'sv-run-1', papers: [svPaper] }
  });
  await flush();
  const cardHtml = p.document.getElementById('mainPapersList').innerHTML;
  assert.match(cardHtml, /含SciVerse全文/);
  assert.match(cardHtml, /Paper Schema/);
  assert.match(cardHtml, /FWCI 3\.85/);
  assert.match(cardHtml, /原文片段×1/);

  // Open detail view and test inline SciVerse content fetch
  p.inspectPaper(svPaper);
  assert.match(p.document.getElementById('detailBody').innerHTML, /Slow H2 evolution kinetics/);
  assert.match(p.document.getElementById('detailBody').innerHTML, /EXAFS confirms Ni-N4 coordination/);
  await click(p, 'drwFetchSciverseContent');
  assert.equal(tools(p).at(-1).params.name, 'search_sciverse');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'content');
  assert.equal(tools(p).at(-1).params.arguments.offset, 1200);
  answer(p, {
    ...emptyState(),
    exportedSaveDir: 'F:/个人知识库/00-收件箱-待整理/文献检索/2025-SciVerse_SciVerse',
    sciverseContent: {
      doc_id: 'b'.repeat(64),
      offset: 1200,
      limit: 6000,
      chars_returned: 512,
      next_offset: 1712,
      more: true,
      text: '## Fulltext Markdown Slice\nDetailed experimental procedure...',
      downloaded_figures: [{ index: 1, alt: 'Fig 1', saved_path: 'F:/个人知识库/fig1.jpg', bytes: 24576 }]
    }
  });
  await flush();
  assert.match(p.document.getElementById('drwInlineSciverseMount').innerHTML, /Fulltext Markdown Slice/);
  assert.match(p.document.getElementById('drwInlineSciverseMount').innerHTML, /打开原图 #1/);
  const svPrompt = p.buildDeepReadHandoffPrompt(svPaper);
  assert.match(svPrompt, /SciVerse 原文 Markdown 切片/);
  assert.match(svPrompt, /01_fig1\.jpg/);

  // 8. v1.9.0 Document Parsing (sanmu-doc2md) workbench, paper card jump, Zotero picker & folder selection
  const docPaper = {
    ...paper('doc2md-1'),
    title: 'Single Atom Ni Photocatalysis',
    year: 2025,
    authors: [{ name: 'Alice Lee' }],
    local_pdf_path: 'F:\\个人知识库\\00-收件箱-待整理\\2025-Ni.pdf',
    epmc_suppl_files: ['F:\\个人知识库\\00-收件箱-待整理\\2025-Ni-SI.pdf']
  };
  p.prefillDoc2mdFromPaper(docPaper);
  assert.equal(p.state.activeTab, 'doc2md');
  assert.equal(p.document.getElementById('doc2mdMainFileInput').value, 'F:\\个人知识库\\00-收件箱-待整理\\2025-Ni.pdf');
  assert.equal(p.document.getElementById('doc2mdSiFileInput').value, 'F:\\个人知识库\\00-收件箱-待整理\\2025-Ni-SI.pdf');
  assert.match(p.document.getElementById('doc2mdOutputPreviewText').textContent, /Lee - 2025 - Single Atom Ni Photocatalysis/);

  // Pick custom save directory via system dialog
  await click(p, 'btnPickDoc2mdSaveDir');
  assert.equal(tools(p).at(-1).params.name, 'pick_system_path');
  assert.equal(tools(p).at(-1).params.arguments.mode, 'folder');
  p.reply(tools(p).at(-1), {
    structuredContent: {
      canceled: false,
      mode: 'folder',
      path: 'F:\\个人知识库\\01-科研基石\\光催化'
    }
  });
  await flush();
  assert.equal(p.document.getElementById('doc2mdSaveDirInput').value, 'F:\\个人知识库\\01-科研基石\\光催化');

  // Execute doc2md job
  await click(p, 'btnExecDoc2md');
  const docCall = tools(p).at(-1);
  assert.equal(docCall.params.name, 'run_doc2md');
  assert.equal(docCall.params.arguments.mode, 'research');
  assert.equal(docCall.params.arguments.pdfPath, 'F:\\个人知识库\\00-收件箱-待整理\\2025-Ni.pdf');
  assert.equal(docCall.params.arguments.siPdfPath, 'F:\\个人知识库\\00-收件箱-待整理\\2025-Ni-SI.pdf');
  assert.equal(docCall.params.arguments.saveDir, 'F:\\个人知识库\\01-科研基石\\光催化');
  p.reply(docCall, {
    structuredContent: {
      ...emptyState(),
      doc2mdJobs: [
        {
          jobId: 'doc2md-research-1',
          mode: 'research',
          ocrEngine: 'local_fast',
          title: 'Single Atom Ni Photocatalysis',
          status: 'completed',
          phaseLabel: '✅ 解析完成',
          outDir: 'F:\\个人知识库\\01-科研基石\\光催化\\Lee - 2025 - Single Atom Ni Photocatalysis',
          siOutDir: 'F:\\个人知识库\\01-科研基石\\光催化\\Lee - 2025 - Single Atom Ni Photocatalysis - SI',
          stats: {
            markdownCount: 2,
            totalImageCount: 6,
            formulaCount: 15,
            mathPassed: true,
            warnings: [],
            primaryMarkdownPath: 'F:\\个人知识库\\01-科研基石\\光催化\\Lee - 2025 - Single Atom Ni Photocatalysis\\Lee - 2025 - Single Atom Ni Photocatalysis.md'
          }
        }
      ]
    }
  });
  await flush();
  assert.equal(p.document.getElementById('tabCountDoc2md').textContent, '1');
  assert.match(p.document.getElementById('doc2mdJobsListContainer').innerHTML, /KaTeX浏览器渲染预览/);
});

