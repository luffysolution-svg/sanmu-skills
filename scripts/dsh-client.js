/* Harness owns the sidebar; the packaged MCP App is the full ZCode workbench. */
window.__ModuleLoader__.load({
  id: 'sanmu-skills',
  factory(require) {
    const React = require('react');
    const h = React.createElement;
    const NS = 'sanmuWorkbench';
    const KIND = 'sanmu-paper-search';
    const VERSION = '1.10.3';
    const RESOURCE = '/sanmu-paper-search/workbench';
    const dictionaries = {
      en: { title: 'Sanmu Paper Search', open: 'Open Sanmu Paper Search', loading: 'Loading the complete workbench…', retry: 'Reload workbench', reconnect: 'Reconnect research tools', error: 'Unable to load the workbench.', unavailable: 'The host did not accept this operation.' },
      zh: { title: '三木文献检索', open: '打开三木文献检索', loading: '正在加载完整工作台…', retry: '重新加载工作台', reconnect: '重新连接科研工具', error: '工作台加载失败。', unavailable: '宿主未接收此操作。' },
    };
    const css = `
.sanmu-harness{height:100%;min-height:0;min-width:0;display:flex;flex-direction:column;background:var(--dsw-alias-bg-base);color:var(--dsw-alias-label-primary);font:inherit}
.sanmu-harness iframe{flex:1;width:100%;min-height:0;border:0;display:block;background:var(--dsw-alias-bg-base)}
.sanmu-harness .sanmu-loading,.sanmu-harness .sanmu-error{padding:16px;font-size:14px;line-height:1.6;overflow-wrap:anywhere}.sanmu-harness .sanmu-error{color:var(--dsw-alias-state-error-primary)}
.sanmu-harness .sanmu-recovery{display:flex;align-items:center;justify-content:flex-end;gap:8px;padding:4px 10px;border-bottom:1px solid var(--dsw-alias-border-l1);flex:none}
.sanmu-harness button,.sanmu-launch{font:inherit;color:inherit;border:1px solid var(--dsw-alias-border-l1);background:var(--dsw-alias-bg-base);border-radius:8px;padding:5px 9px;cursor:pointer}
.sanmu-harness button:hover,.sanmu-launch:hover{background:var(--dsw-alias-bg-layer-1)}.sanmu-harness button:disabled{opacity:.5;cursor:wait}.sanmu-harness button:focus-visible,.sanmu-launch:focus-visible{outline:2px solid var(--dsw-alias-brand-primary);outline-offset:3px}
.sanmu-recovery button{font-size:12px;color:var(--dsw-alias-label-secondary);border:0;background:transparent}.sanmu-launch{display:inline-flex;align-items:center;gap:6px;border:0;background:transparent;font-size:13px}
`;
    function Icon({ size = 18 }) {
      return h('svg', { width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor', strokeWidth: 1.6, strokeLinecap: 'round', strokeLinejoin: 'round', 'aria-hidden': true },
        h('path', { d: 'M3 5h5a4 4 0 0 1 4 4v12a4 4 0 0 0-4-3H3V5Zm18 0h-5a4 4 0 0 0-4 4v12a4 4 0 0 1 4-3h5V5Z' }),
        h('circle', { cx: 15.5, cy: 10.5, r: 3 }), h('path', { d: 'm18 13 3 3M6 8v5m-2-2 2-3 2 3' }));
    }
    const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
    function unwrapRemote(reply) {
      if (reply?.ok === false) throw new Error(reply.error?.message || 'Harness operation failed.');
      return reply?.ok === true ? reply.value : reply;
    }
    function preparePanelDocument(html) {
      if (typeof html !== 'string' || !/<head(?:\s[^>]*)?>/i.test(html) || !html.includes('ui/initialize')) throw new Error('The installed Sanmu workbench resource is incomplete.');
      // The original document stays intact. Its only network access is article images;
      // research operations use the authenticated parent bridge, never a launch URL.
      const policy = "default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src https: http: data: blob:; font-src data:; connect-src 'none'; frame-src 'none'; object-src 'none'; base-uri 'none'; form-action 'none'";
      return html.replace(/<head(?:\s[^>]*)?>/i, match => `${match}<meta http-equiv="Content-Security-Policy" content="${policy}">`);
    }
    function createPanelBridge({ frame, command, prompt, context, openLink, onReady = () => {}, onError = () => {} }) {
      let disposed = false;
      let initialized = false;
      let modelContext = [];
      const pending = new Map();
      const post = message => { if (!disposed) frame.postMessage(message, '*'); }; // sandboxed srcDoc has an opaque origin
      const notifyContext = () => { if (initialized) post({ jsonrpc: '2.0', method: 'ui/notifications/host-context-changed', params: { hostContext: context() } }); };
      const textContent = content => {
        if (!Array.isArray(content) || content.some(block => !isObject(block) || block.type !== 'text' || typeof block.text !== 'string')) throw new Error('Only text content is accepted by the workbench conversation bridge.');
        if (content.reduce((total, block) => total + block.text.length, 0) > 262144) throw new Error('The workbench message is too large.');
        return content.filter(block => block.text.trim()).map(block => ({ type: 'text', text: block.text }));
      };
      async function dispatch(method, params, signal) {
        if (method === 'ui/initialize') {
          initialized = true;
          return { protocolVersion: '2026-01-26', hostInfo: { name: 'deepseek-harness-sanmu', version: VERSION }, hostCapabilities: { serverTools: {}, openLinks: {}, updateModelContext: {}, message: {} }, hostContext: context() };
        }
        if (!initialized) throw new Error('Initialize the workbench before requesting an operation.');
        if (method === 'tools/list') return command({ action: 'tools/list' }, signal);
        if (method === 'tools/call') {
          if (typeof params.name !== 'string' || !/^[A-Za-z0-9_-]+$/.test(params.name) || params.name.startsWith('mcp__') || params.arguments !== undefined && !isObject(params.arguments)) throw new Error('Invalid Sanmu tool request.');
          return command({ name: params.name, arguments: params.arguments || {} }, signal);
        }
        if (method === 'ui/open-link') {
          if (typeof params.url !== 'string' || params.url.length > 8192) throw new Error('Invalid link.');
          const url = new URL(params.url);
          if (!['https:', 'http:', 'zotero:'].includes(url.protocol) || url.username || url.password) throw new Error('Only HTTP, HTTPS and Zotero links without credentials are supported.');
          await openLink(url.href);
          return {};
        }
        if (method === 'ui/update-model-context') { modelContext = textContent(params.content); return {}; }
        if (method === 'ui/message') {
          if (params.role !== undefined && params.role !== 'user') throw new Error('The workbench can submit only a user message.');
          const content = textContent(params.content);
          if (!content.length) throw new Error('The workbench message is empty.');
          // Context becomes model-visible only as part of this committed user message.
          const snapshot = modelContext;
          modelContext = [];
          await prompt([...content, ...snapshot], signal);
          return {};
        }
        if (method === 'ui/resource-teardown') return {};
        throw new Error(`Unsupported workbench method: ${method}`);
      }
      async function handleMessage(event) {
        if (disposed || event.source !== frame || event.origin !== 'null') return;
        const request = event.data;
        if (!isObject(request) || request.jsonrpc !== '2.0' || typeof request.method !== 'string') return;
        if (request.id === undefined) {
          if (request.method === 'ui/notifications/initialized' && initialized) onReady();
          return;
        }
        if (!['string', 'number'].includes(typeof request.id)) return;
        if (pending.has(request.id)) { post({ jsonrpc: '2.0', id: request.id, error: { code: -32600, message: 'Duplicate workbench request.' } }); return; }
        const controller = new AbortController();
        pending.set(request.id, controller);
        try {
          if (request.params !== undefined && !isObject(request.params)) throw new Error('Workbench parameters must be an object.');
          if (JSON.stringify(request).length > 2097152) throw new Error('The workbench request is too large.');
          const result = await dispatch(request.method, request.params || {}, controller.signal);
          if (!disposed && !controller.signal.aborted) post({ jsonrpc: '2.0', id: request.id, result });
        } catch (error) {
          if (!disposed && !controller.signal.aborted) {
            post({ jsonrpc: '2.0', id: request.id, error: { code: -32000, message: error.message || String(error) } });
            if (request.method === 'tools/call' && request.params?.name === 'open_panel') onError(error.message || String(error));
          }
        } finally { pending.delete(request.id); }
      }
      return { handleMessage, notifyContext, dispose() { disposed = true; for (const controller of pending.values()) controller.abort(); pending.clear(); modelContext = []; } };
    }
    function getHostContext(ctx) {
      const theme = ctx.theme.getTheme();
      const computed = getComputedStyle(document.body);
      const mapping = {
        '--color-background-primary': '--dsw-alias-bg-base', '--color-background-secondary': '--dsw-alias-bg-layer-1', '--color-background-tertiary': '--dsw-alias-bg-layer-2',
        '--color-text-primary': '--dsw-alias-label-primary', '--color-text-secondary': '--dsw-alias-label-secondary', '--color-text-tertiary': '--dsw-alias-label-secondary',
        '--color-border-primary': '--dsw-alias-border-l2', '--color-border-secondary': '--dsw-alias-border-l1',
      };
      const variables = Object.fromEntries(Object.entries(mapping).map(([key, token]) => [key, computed.getPropertyValue(token).trim()]).filter(([, value]) => value));
      const bodyFont = computed.getPropertyValue('--dsw-font-family').trim() || computed.fontFamily;
      const codeFont = computed.getPropertyValue('--ds-font-family-code').trim();
      if (bodyFont) variables['--font-sans'] = bodyFont;
      if (codeFont) variables['--font-mono'] = codeFont;
      return { theme: theme.active.colorScheme, locale: ctx.locale.getLocale().active, displayMode: 'inline', styles: { variables } };
    }
    function Workbench(props) {
      const { ctx, t, sessionId } = props;
      const [html, setHtml] = React.useState(null);
      const [error, setError] = React.useState('');
      const [loading, setLoading] = React.useState(true);
      const [generation, setGeneration] = React.useState(0);
      const frameRef = React.useRef(null);
      const bridgeRef = React.useRef(null);
      const controllers = React.useRef(new Set());
      const tabInfo = props.useTabInfo();
      const command = async (request, signal) => {
        const execution = unwrapRemote(await ctx.remote.commands.execute(sessionId, '/sanmu ' + JSON.stringify(request), [], signal));
        if (execution?.result?.kind !== 'success') throw new Error(execution?.result?.text || t('unavailable'));
        return JSON.parse(execution.result.text);
      };
      React.useEffect(() => {
        const controller = new AbortController();
        let active = true;
        setHtml(null); setLoading(true); setError('');
        void (async () => {
          let resource;
          if (['http:', 'https:'].includes(location.protocol)) {
            const response = await fetch(RESOURCE, { credentials: 'same-origin', cache: 'no-store', signal: controller.signal });
            if (!response.ok) throw new Error(`${t('error')} HTTP ${response.status}`);
            resource = await response.json();
          } else {
            resource = await command({ action: 'panel/read' }, controller.signal);
          }
          if (active) setHtml(preparePanelDocument(resource.html));
        })().catch(failure => { if (active && !controller.signal.aborted) { setError(failure.message); setLoading(false); } });
        return () => { active = false; controller.abort(); for (const pending of controllers.current) pending.abort(); controllers.current.clear(); };
      }, [sessionId, generation]);
      React.useLayoutEffect(() => {
        const frame = frameRef.current?.contentWindow;
        if (!html || !frame) return;
        const bridge = createPanelBridge({ frame, command, context: () => getHostContext(ctx),
          prompt: async (content, signal) => {
            const accepted = unwrapRemote(await ctx.remote.session.prompt({ requestId: crypto.randomUUID(), sessionId, mode: 'queue', content }, signal));
            if (!accepted?.accepted) throw new Error(t('unavailable'));
          },
          openLink: url => { window.open(url, '_blank', 'noopener,noreferrer'); },
          onReady: () => { setLoading(false); setError(''); }, onError: setError,
        });
        bridgeRef.current = bridge;
        const listener = event => { void bridge.handleMessage(event); };
        window.addEventListener('message', listener);
        let themeFrame;
        const updateTheme = () => { cancelAnimationFrame(themeFrame); themeFrame = requestAnimationFrame(bridge.notifyContext); };
        const offTheme = ctx.on('theme/change', updateTheme);
        const offLocale = ctx.on('locale/change', bridge.notifyContext);
        return () => { window.removeEventListener('message', listener); offTheme(); offLocale(); cancelAnimationFrame(themeFrame); bridge.dispose(); bridgeRef.current = null; };
      }, [html, sessionId, generation]);
      React.useEffect(() => {
        return tabInfo.tab.actions.bindCommands({ refresh: () => setGeneration(value => value + 1) });
      }, [tabInfo.tab.actions]);
      const retry = async () => {
        const controller = new AbortController();
        controllers.current.add(controller); setLoading(true); setError('');
        try { await command({ action: 'retry' }, controller.signal); setGeneration(value => value + 1); }
        catch (failure) { if (!controller.signal.aborted) setError(failure.message); setLoading(false); }
        finally { controllers.current.delete(controller); }
      };
      return h('section', { className: 'sanmu-harness', 'aria-label': t('title'), 'data-sanmu-version': VERSION }, h('style', null, css),
        error ? h('div', { className: 'sanmu-error', role: 'alert' }, error) : null,
        loading && !html ? h('div', { className: 'sanmu-loading', role: 'status' }, t('loading')) : null,
        error ? h('div', { className: 'sanmu-recovery' }, h('button', { onClick: () => setGeneration(value => value + 1), disabled: loading && !error, title: t('retry') }, t('retry')), h('button', { onClick: () => { void retry(); }, disabled: loading && !error }, t('reconnect'))) : null,
        html ? h('iframe', { key: generation, ref: frameRef, title: t('title'), srcDoc: html, sandbox: 'allow-scripts allow-downloads', referrerPolicy: 'no-referrer', allow: 'clipboard-write', 'data-sanmu-workbench': 'full' }) : null);
    }
    return {
      inject: ['slots', 'locale', 'theme', 'remote', 'remote.commands', 'remote.session', 'sidebarRight', 'sidebarRightTabs'],
      createPanelBridge, preparePanelDocument, unwrapRemote,
      apply(ctx) {
        for (const [locale, dictionary] of Object.entries(dictionaries)) ctx.effect(() => ctx.locale.register(NS, locale, dictionary));
        const t = ctx.locale.bind(NS);
        ctx.effect(() => ctx.sidebarRightTabs.register({ id: 'sanmu-skills', kind: KIND, keepMounted: true, title: () => t('title'), guide: [{ id: 'workbench', order: 45, title: () => t('title'), description: () => t('open'), icon: Icon }] }));
        ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({ name: 'sidebar.right.pane.tab', key: 'sanmu-skills', locale: NS }, props => h(Workbench, { ...props, ctx })));
        ctx.slots.inject('conversation.session.header.actions', () => ctx.slots.register({ name: 'conversation.session.header.actions', id: 'sanmu-workbench', order: 35, label: () => t('title'), locale: NS }, props => h(React.Fragment, null, h('style', null, css), h('button', { className: 'sanmu-launch', title: props.t('open'), 'aria-label': props.t('open'), onClick: () => ctx.sidebarRight.openTab(KIND) }, h(Icon), h('span', null, props.t('title'))))));
      },
    };
  },
});
