import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';
import vm from 'node:vm';
import test from 'node:test';

const root = resolve(dirname(fileURLToPath(import.meta.url)), '../..');
let factory;
vm.runInNewContext(await readFile(resolve(root, 'scripts/dsh-client.js'), 'utf8'), {
  window: { __ModuleLoader__: { load(definition) { factory = definition.factory; } } },
  AbortController, URL,
});
const plugin = factory(name => { assert.equal(name, 'react'); return {}; }); // Protocol tests do not render or emulate React/DOM.
const plain = value => JSON.parse(JSON.stringify(value));
function fixture(overrides = {}) {
  const posted = [], commands = [], prompts = [], links = [];
  const frame = { postMessage(message, origin) { posted.push({ message: plain(message), origin }); } };
  const bridge = plugin.createPanelBridge({
    frame,
    command: async (request, signal) => { commands.push({ request: plain(request), signal }); return { content: [{ type: 'text', text: 'ok' }], structuredContent: { papers: [] } }; },
    prompt: async (content, signal) => { prompts.push({ content: plain(content), signal }); },
    openLink: async url => { links.push(url); },
    context: () => ({ theme: 'dark', locale: 'zh', displayMode: 'inline' }),
    ...overrides,
  });
  let id = 1;
  const request = (method, params = {}, options = {}) => bridge.handleMessage({ source: frame, origin: 'null', data: { jsonrpc: '2.0', id: id++, method, params }, ...options });
  return { bridge, frame, posted, commands, prompts, links, request };
}

test('the full packaged document is preserved with only a restrictive embedding policy added', async () => {
  const html = await readFile(resolve(root, 'assets/panel.html'), 'utf8');
  const result = plugin.preparePanelDocument(html);
  assert.equal(result.replace(/<meta http-equiv="Content-Security-Policy" content="[^"]+">/, ''), html);
  assert.match(result, /connect-src 'none'/);
  assert.match(result, /form-action 'none'/);
  assert.match(result, /stepGraphPhysics/);
  assert.match(result, /renderTimelineChronicle/);
  assert.match(result, /id="qSearch"/);
  assert.throws(() => plugin.preparePanelDocument('<html>broken</html>'), /incomplete/);
  for (const required of ['remote', 'remote.commands', 'remote.session', 'theme']) assert.ok(plugin.inject.includes(required));
});

test('the bridge accepts only the exact sandboxed workbench and requires initialization', async () => {
  const f = fixture();
  await f.request('tools/call', { name: 'open_panel' }, { source: {} });
  await f.request('tools/call', { name: 'open_panel' }, { origin: 'https://elsewhere.example' });
  assert.equal(f.posted.length, 0);
  assert.equal(f.commands.length, 0);
  await f.request('tools/call', { name: 'open_panel' });
  assert.match(f.posted.at(-1).message.error.message, /Initialize/);
  await f.request('ui/initialize');
  assert.equal(f.posted.at(-1).message.result.hostContext.theme, 'dark');
  await f.request('tools/call', { name: 'open_panel', arguments: { runId: 'r1' } });
  assert.deepEqual(f.commands[0].request, { name: 'open_panel', arguments: { runId: 'r1' } });
  assert.deepEqual(f.posted.at(-1).message.result, { content: [{ type: 'text', text: 'ok' }], structuredContent: { papers: [] } });
  f.bridge.dispose();
});

test('canonical MCP errors stay intact and invalid method or tool names never dispatch', async () => {
  const canonical = { isError: true, content: [{ type: 'text', text: 'source unavailable' }], structuredContent: { error: { code: 'fixture' } }, _meta: { fixture: true } };
  let calls = 0;
  const f = fixture({ command: async () => { calls++; return canonical; } });
  await f.request('ui/initialize');
  await f.request('tools/call', { name: 'search_papers', arguments: { query: 'Ni' } });
  assert.deepEqual(f.posted.at(-1).message.result, canonical);
  await f.request('tools/call', { name: 'mcp__other__write' });
  await f.request('tools/call', { name: 'search_papers', arguments: [] });
  await f.request('ui/arbitrary-host-operation');
  assert.equal(calls, 1);
  assert.match(f.posted.at(-1).message.error.message, /Unsupported/);
  f.bridge.dispose();
});

test('conversation context enters only the committed current-session user message', async () => {
  const f = fixture();
  await f.request('ui/initialize');
  await f.request('ui/update-model-context', { content: [{ type: 'text', text: 'paper metadata' }] });
  assert.equal(f.prompts.length, 0);
  await f.request('ui/message', { role: 'user', content: [{ type: 'text', text: 'read this paper' }] });
  assert.deepEqual(f.prompts[0].content, [{ type: 'text', text: 'read this paper' }, { type: 'text', text: 'paper metadata' }]);
  await f.request('ui/message', { role: 'system', content: [{ type: 'text', text: 'untrusted' }] });
  assert.equal(f.prompts.length, 1);
  await f.request('ui/message', { content: [{ type: 'text', text: 'next request' }] });
  assert.deepEqual(f.prompts[1].content, [{ type: 'text', text: 'next request' }]);
  f.bridge.dispose();
});

test('a pending handoff cannot clear a later paper context', async () => {
  let admitFirst;
  const firstAdmission = new Promise(resolve => { admitFirst = resolve; });
  const received = [];
  const f = fixture({ prompt: async content => { received.push(plain(content)); if (received.length === 1) await firstAdmission; } });
  await f.request('ui/initialize');
  await f.request('ui/update-model-context', { content: [{ type: 'text', text: 'metadata A' }] });
  const first = f.request('ui/message', { content: [{ type: 'text', text: 'read A' }] });
  await f.request('ui/update-model-context', { content: [{ type: 'text', text: 'metadata B' }] });
  admitFirst();
  await first;
  await f.request('ui/message', { content: [{ type: 'text', text: 'read B' }] });
  assert.deepEqual(received, [
    [{ type: 'text', text: 'read A' }, { type: 'text', text: 'metadata A' }],
    [{ type: 'text', text: 'read B' }, { type: 'text', text: 'metadata B' }],
  ]);
  f.bridge.dispose();
});

test('a rejected handoff cannot leak its paper into the next message', async () => {
  const received = [];
  const f = fixture({ prompt: async content => { received.push(plain(content)); if (received.length === 1) throw new Error('admission rejected'); } });
  await f.request('ui/initialize');
  await f.request('ui/update-model-context', { content: [{ type: 'text', text: 'metadata A' }] });
  await f.request('ui/message', { content: [{ type: 'text', text: 'read A' }] });
  assert.match(f.posted.at(-1).message.error.message, /admission rejected/);
  await f.request('ui/message', { content: [{ type: 'text', text: 'unrelated next message' }] });
  assert.deepEqual(received[1], [{ type: 'text', text: 'unrelated next message' }]);
  f.bridge.dispose();
});

test('external navigation validates URLs and rejects executable or credential-bearing destinations', async () => {
  const f = fixture();
  await f.request('ui/initialize');
  for (const url of ['javascript:alert(1)', 'file:///C:/secret', 'data:text/html,wrong', 'https://user:pass@example.org/']) await f.request('ui/open-link', { url });
  assert.equal(f.links.length, 0);
  await f.request('ui/open-link', { url: 'https://doi.org/10.1000/test' });
  assert.deepEqual(f.links, ['https://doi.org/10.1000/test']);
  f.bridge.dispose();
});

test('unloading aborts pending host work and drops replies to a closed frame', async () => {
  let entered, signal;
  const started = new Promise(done => { entered = done; });
  const f = fixture({ command: async (_request, currentSignal) => {
    signal = currentSignal;
    entered();
    return new Promise((_done, reject) => currentSignal.addEventListener('abort', () => reject(new Error('aborted')), { once: true }));
  } });
  await f.request('ui/initialize');
  const active = f.request('tools/call', { name: 'open_panel' });
  await started;
  const before = f.posted.length;
  f.bridge.dispose();
  await active;
  assert.equal(signal.aborted, true);
  assert.equal(f.posted.length, before);
  await f.request('ui/initialize');
  assert.equal(f.posted.length, before);
});

test('Remote response envelopes preserve structured successes and report real failures', () => {
  assert.deepEqual(plugin.unwrapRemote({ ok: true, value: { accepted: true } }), { accepted: true });
  assert.throws(() => plugin.unwrapRemote({ ok: false, error: { message: 'not allowed' } }), /not allowed/);
});
