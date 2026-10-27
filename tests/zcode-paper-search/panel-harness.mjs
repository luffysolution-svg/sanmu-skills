import { readFileSync } from 'node:fs';
import vm from 'node:vm';

export const panelPath = new URL('../../distribution/zcode/sanmu-paper-search/assets/panel.html', import.meta.url);
const html = readFileSync(panelPath, 'utf8');

// Deliberately small DOM fixture, not a layout/browser substitute. It runs the real
// inline script and supports the selectors, form values and events used below.
const decode = (s) => s.replace(/&(?:amp|lt|gt|quot|#39);/g, (m) => ({ '&amp;': '&', '&lt;': '<', '&gt;': '>', '&quot;': '"', '&#39;': "'" })[m]);
class Events {
  listeners = new Map();
  addEventListener(type, fn) {
    if (!this.listeners.has(type)) this.listeners.set(type, new Set());
    this.listeners.get(type).add(fn);
  }
  removeEventListener(type, fn) { this.listeners.get(type)?.delete(fn); }
  dispatchEvent(event) {
    event.target ||= this;
    event.currentTarget = this;
    event.preventDefault ||= () => { event.defaultPrevented = true; };
    event.stopPropagation ||= () => { event.stopped = true; };
    for (const fn of [...(this.listeners.get(event.type) || [])]) fn(event);
    if (this.attributes?.onclick?.includes('stopPropagation')) event.stopPropagation();
    if (event.bubbles && !event.stopped) this.parentElement?.dispatchEvent(event);
    return !event.defaultPrevented;
  }
}
class Element extends Events {
  constructor(tag, attrs = {}) {
    super();
    this.tagName = tag.toUpperCase();
    this.attributes = attrs;
    this.children = [];
    this.dataset = {};
    for (const [key, value] of Object.entries(attrs)) {
      if (key.startsWith('data-')) this.dataset[key.slice(5).replace(/-([a-z])/g, (_, c) => c.toUpperCase())] = value;
    }
    this.style = { setProperty(key, value) { this[key] = value; } };
    this.className = attrs.class || '';
    this.id = attrs.id || '';
    this._value = attrs.value;
    this.checked = Object.hasOwn(attrs, 'checked');
    this.disabled = Object.hasOwn(attrs, 'disabled');
    this.classList = {
      contains: (key) => this.className.split(/\s+/).includes(key),
      add: (...keys) => { this.className = [...new Set([...this.className.split(/\s+/).filter(Boolean), ...keys])].join(' '); },
      remove: (...keys) => { this.className = this.className.split(/\s+/).filter((key) => !keys.includes(key)).join(' '); },
      toggle: (key, force) => {
        const enabled = force ?? !this.classList.contains(key);
        this.classList[enabled ? 'add' : 'remove'](key);
        return enabled;
      }
    };
  }
  get value() {
    if (this._value !== undefined) return this._value;
    if (this.tagName === 'SELECT') {
      const options = this.querySelectorAll('option');
      return (options.find((option) => Object.hasOwn(option.attributes, 'selected')) || options[0])?.value || '';
    }
    return this.tagName === 'TEXTAREA' ? this.textContent : '';
  }
  set value(value) { this._value = String(value); }
  get textContent() { return decode(this._text || '') + this.children.map((node) => node.textContent).join(''); }
  set textContent(value) { this._text = String(value); this.children = []; this._html = String(value); }
  get innerHTML() { return this._html || ''; }
  set innerHTML(value) {
    this._html = String(value);
    this._text = '';
    this.children = [];
    if (this.tagName === 'SELECT') this._value = undefined;
    parse(this._html, this);
  }
  setAttribute(key, value) { this.attributes[key] = String(value); }
  getAttribute(key) { return this.attributes[key] ?? null; }
  append(node) { node.parentElement = this; this.children.push(node); }
  querySelectorAll(selector) {
    const output = [];
    const selectors = selector.split(',').map((item) => item.trim().split(/\s+/));
    const walk = (node) => {
      for (const child of node.children) {
        if (selectors.some((parts) => {
          if (!matches(child, parts.at(-1))) return false;
          let ancestor = child.parentElement;
          for (let i = parts.length - 2; i >= 0; i--) {
            while (ancestor && !matches(ancestor, parts[i])) ancestor = ancestor.parentElement;
            if (!ancestor) return false;
            ancestor = ancestor.parentElement;
          }
          return true;
        })) output.push(child);
        walk(child);
      }
    };
    walk(this);
    return output;
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  closest(selector) { return matches(this, selector) ? this : this.parentElement?.closest(selector); }
  click() { if (!this.disabled) this.dispatchEvent({ type: 'click', bubbles: true }); }
  focus() { this.focused = true; }
  getBoundingClientRect() { return this.rect || { left: 0, top: 0, width: 720, height: 430 }; }
  setPointerCapture() {}
  releasePointerCapture() {}
}
function matches(el, selector) {
  if (selector.endsWith(':checked')) {
    if (!el.checked) return false;
    selector = selector.slice(0, -8);
  }
  const tag = selector.match(/^[a-z][\w-]*/i)?.[0];
  if (tag && el.tagName !== tag.toUpperCase()) return false;
  for (const [, id] of selector.matchAll(/#([\w-]+)/g)) if (el.id !== id) return false;
  for (const [, cls] of selector.matchAll(/\.([\w-]+)/g)) if (!el.classList.contains(cls)) return false;
  for (const [, key, value] of selector.matchAll(/\[([\w-]+)(?:="([^"]*)")?\]/g)) {
    if (!Object.hasOwn(el.attributes, key) || (value !== undefined && el.attributes[key] !== value)) return false;
  }
  return true;
}
function parse(source, root) {
  const stack = [root];
  for (const token of source.match(/<!--[\s\S]*?-->|<[^>]+>|[^<]+/g) || []) {
    if (token.startsWith('<!--') || token.startsWith('<!')) continue;
    if (token.startsWith('</')) {
      const tag = token.slice(2, -1).trim().toUpperCase();
      const idx = stack.findLastIndex((node) => node.tagName === tag);
      if (idx > 0) stack.length = idx;
    } else if (token.startsWith('<')) {
      const tag = token.match(/^<([\w-]+)/)?.[1];
      if (!tag) continue;
      const attrs = {};
      const rest = token.slice(tag.length + 1, -1).replace(/\/$/, '');
      for (const [, key, a, b, c] of rest.matchAll(/([^\s=]+)(?:\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s]+)))?/g)) attrs[key] = decode(a ?? b ?? c ?? '');
      const node = new Element(tag, attrs);
      stack.at(-1).append(node);
      if (!/^(input|meta|link|br|hr|img)$/i.test(tag) && !token.endsWith('/>')) stack.push(node);
    } else {
      const text = new Element('#text');
      text._text = token;
      stack.at(-1).append(text);
    }
  }
}

export function makePanel({ bootstrap = false, clipboard, resizeObserver = true } = {}) {
  const document = new Element('document');
  document.documentElement = new Element('html');
  document.append(document.documentElement);
  document.body = new Element('body');
  document.documentElement.append(document.body);
  parse(html.split('<body>')[1].split('<script>')[0], document.body);
  document.getElementById = (id) => document.querySelector(`#${id}`);
  const window = new Events();
  const posted = [];
  window.parent = { postMessage: (payload, origin) => posted.push({ ...payload, targetOrigin: origin }) };
  window.open = (...args) => { window.opened = args; };
  window.scrollTo = () => {};
  const timers = new Map();
  const frames = new Map();
  let nextTimer = 0;
  let nextFrame = 0;
  let observerDisconnected = false;
  const exports = 'state, graphSim, pendingRpc, ensureInitialized, callTool, sendRpcRequest, normalizeDirectId, isDirectPaperIdentifier, safeWebUrl, openExternalUrl, updatePanelData, renderPaperCardHtml, renderRawResultCardHtml, renderActiveRun, renderSessionNavBar, bindPaperListEvents, inspectPaper, inspectRawResultItem, executeUndoZoteroImport, canUndoZoteroImport, computeTimelineTargets, clientToGraphWorld, renderRelationshipGraph, renderGraphIntelligencePanel, renderBasket, filterAndSortPapers, computePaperRecommendScore, buildDeepReadHandoffPrompt, triggerDeepReadHandoff, prefillDoc2mdFromPaper, prefillDoc2mdFromZoteroItem, renderDoc2mdTab, switchTab, openDetailView, closeDetailView, copyTextToClipboard, sendToChat, sendPromptToChat';
  let script = html.match(/<script>([\s\S]*?)<\/script>/)[1];
  script = script.replace('callTool("open_panel", {}).catch(() => {});', `globalThis.panel = { ${exports} }; ${bootstrap ? 'callTool("open_panel", {}).catch(() => {});' : ''}`);
  const context = vm.createContext({
    window, document, URL, console, navigator: { clipboard },
    setTimeout: (fn, ms) => { const id = ++nextTimer; timers.set(id, { fn, ms }); return id; },
    clearTimeout: (id) => timers.delete(id),
    requestAnimationFrame: (fn) => { const id = ++nextFrame; frames.set(id, fn); return id; },
    cancelAnimationFrame: (id) => frames.delete(id),
    ...(resizeObserver ? { ResizeObserver: class { observe() {} disconnect() { observerDisconnected = true; } } } : {})
  });
  new vm.Script(script, { filename: panelPath.pathname }).runInContext(context);
  const reply = (request, result, { source = window.parent, origin = 'https://host.test', error } = {}) => {
    window.dispatchEvent({ type: 'message', source, origin, data: { jsonrpc: '2.0', id: request.id, ...(error ? { error } : { result }) } });
  };
  return { ...context.panel, document, window, posted, timers, frames, reply,
    get observerDisconnected() { return observerDisconnected; },
    notify: (structuredContent, source = window.parent) => window.dispatchEvent({ type: 'message', source, origin: 'https://host.test', data: { jsonrpc: '2.0', method: 'ui/notifications/tool-result', params: { structuredContent } } })
  };
}
export const flush = async () => { for (let i = 0; i < 5; i++) await new Promise(setImmediate); };
export async function initialize(panel) {
  const ready = panel.ensureInitialized();
  panel.reply(panel.posted.at(-1), { hostContext: { theme: 'dark' } });
  await ready;
}
export const emptyState = () => ({ configStatus: {}, settings: {}, activeRun: null, recentRuns: [], basket: [], graphData: { nodes: [], edges: [] } });
export const paper = (key = 'paper-1') => ({ paper_key: key, title: `Title ${key}`, authors: [{ name: 'Author One' }], identifiers: { doi: [`10.1234/${key}`] }, year: 2024, resolvable_seed_id: `10.1234/${key}`, abstract: 'Abstract '.repeat(30), handoff_stage: 'metadata_checked' });
