const KEY = /^[23456789ABCDEFGHIJKLMNPQRSTUVWXYZ]{8}$/;
const FIELD_NAMES = new Set(["title", "publicationTitle", "volume", "issue", "pages", "date", "DOI", "abstractNote", "url", "language", "extra", "note"]);
export const description = "通过本机 Zotero 8+ API 查询分类与条目，或在授权后更新元数据/标签、添加笔记与分类。支持文档同步计划，更新需提供当前 expectedVersion，写后回读核验。";
export const inputSchema = {
  type: "object", additionalProperties: false,
  properties: {
    action: { type: "string", enum: ["list_collections", "search", "get_item", "update_item", "add_note", "create_collection"] },
    library: { type: "string", pattern: "^(user|g[0-9]+)$" },
    key: { type: "string", pattern: KEY.source }, query: { type: "string" },
    fields: { type: "object", additionalProperties: { type: "string" } },
    addTags: { type: "array", items: { type: "string" } }, removeTags: { type: "array", items: { type: "string" } },
    parentKey: { type: "string", pattern: KEY.source }, markdown: { type: "string" }, html: { type: "string" },
    marker: { type: "string" }, name: { type: "string" }, parentCollection: { type: "string", pattern: KEY.source },
    expectedVersion: { type: "integer", minimum: 0 }, expectedServerId: { type: "string" }
  }, required: ["action"]
};
const escapeHtml = text => String(text).replace(/[&<>"']/g, ch => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]));
export async function executeZoteroLibrary(input, { baseUrl = "http://127.0.0.1:23119", authorize, fetchImpl = globalThis.fetch } = {}) {
  const endpoint = new URL(baseUrl);
  if (endpoint.protocol !== "http:" || !["127.0.0.1", "localhost", "[::1]"].includes(endpoint.hostname) || endpoint.username || endpoint.password || endpoint.pathname !== "/") throw new Error("Zotero must use a loopback HTTP URL.");
  const library = input.library || "user";
  if (!/^(user|g[0-9]+)$/.test(library)) throw new Error("Invalid Zotero library.");
  const route = `${endpoint.origin}/api/${library === "user" ? "users/0" : `groups/${library.slice(1)}`}`;
  const headers = { "Zotero-Allowed-Request": "1" };
  const request = async (path, options = {}) => {
    const response = await fetchImpl(path.startsWith("http:") ? path : route + path, { ...options, headers: { ...headers, ...options.headers }, signal: AbortSignal.timeout(60000) });
    if (!response.ok) throw new Error(`Zotero HTTP ${response.status}; operation stopped.`);
    return response;
  };
  const checkKey = key => { if (!KEY.test(key || "")) throw new Error("Invalid Zotero key."); return key; };
  const verifyItem = (item, key) => {
    if (item?.key !== key || !item.data || !Number.isInteger(item.version ?? item.data.version)) throw new Error("Zotero returned a different item or invalid version.");
    if (item.library && (item.library.type !== (library === "user" ? "user" : "group") || library !== "user" && Number(item.library.id) !== Number(library.slice(1)))) throw new Error("Zotero library identity mismatch.");
    return item;
  };
  const getItem = async key => verifyItem(await (await request(`/items/${checkKey(key)}`)).json(), key);
  const list = async path => {
    const all = [];
    for (let start = 0; start < 10000; start += 100) {
      const separator = path.includes("?") ? "&" : "?";
      const page = await (await request(`${path}${separator}start=${start}&limit=100`)).json();
      if (!Array.isArray(page)) throw new Error("Zotero did not return a list.");
      all.push(...page);
      if (page.length < 100) return all;
    }
    throw new Error("Zotero pagination exceeded its limit; no write was performed.");
  };
  const enableWrites = async () => {
    const ping = await request(`${endpoint.origin}/api/`);
    const serverId = ping.headers.get("zotero-server-id");
    if (!serverId || input.expectedServerId && input.expectedServerId !== serverId) throw new Error("Zotero server identity mismatch.");
    if (typeof authorize !== "function") throw new Error("Zotero write authorization is unavailable.");
    const authorized = await authorize(endpoint.origin, serverId);
    if (authorized.serverId !== serverId || !authorized.key) throw new Error("Zotero write authorization identity mismatch.");
    Object.assign(headers, { "Zotero-Server-ID": serverId, "Zotero-API-Key": authorized.key });
  };
  const answer = (text, data) => ({ text, structured: { library, ...data } });
  if (input.action === "list_collections") return answer("Zotero 分类读取完成。", { collections: await list("/collections") });
  if (input.action === "search") return answer("Zotero 条目检索完成。", { items: await list(`/items?itemType=-attachment%20%7C%7C%20note&qmode=everything&q=${encodeURIComponent(input.query || "")}`) });
  if (input.action === "get_item") return answer("Zotero 条目及子条目读取完成。", { item: await getItem(input.key), children: await list(`/items/${checkKey(input.key)}/children`) });
  if (input.action === "update_item") {
    const key = checkKey(input.key);
    if (!Number.isInteger(input.expectedVersion)) throw new Error("update_item requires expectedVersion from a fresh read.");
    const fields = input.fields || {};
    if (!Object.keys(fields).every(key => FIELD_NAMES.has(key) && typeof fields[key] === "string")) throw new Error("Unsupported Zotero field update.");
    await enableWrites();
    const before = await getItem(key);
    if ((before.version ?? before.data.version) !== input.expectedVersion) throw new Error("Zotero item changed; read it again before writing.");
    const patch = { ...fields };
    if (input.addTags || input.removeTags) {
      const tags = before.data.tags || [];
      if (!Array.isArray(tags)) throw new Error("Invalid Zotero tag data.");
      const removals = new Set(input.removeTags || []);
      if (tags.some(tag => tag.type === 1 && removals.has(tag.tag))) throw new Error("Automatic Zotero tags cannot be removed.");
      patch.tags = tags.filter(tag => !removals.has(tag.tag));
      for (const tag of new Set(input.addTags || [])) if (tag.trim() && !patch.tags.some(saved => saved.tag === tag)) patch.tags.push({ tag });
    }
    if (!Object.keys(patch).length) return answer("Zotero 无需更新。", { item: before });
    await request(`/items/${key}`, { method: "PATCH", headers: { "Content-Type": "application/json", "If-Unmodified-Since-Version": String(input.expectedVersion) }, body: JSON.stringify(patch) });
    const after = await getItem(key);
    for (const [field, expected] of Object.entries(fields)) if (after.data[field] !== expected) throw new Error(`Zotero write could not be verified for ${field}.`);
    if (patch.tags && (input.addTags || []).some(tag => !after.data.tags?.some(saved => saved.tag === tag)) || (input.removeTags || []).some(tag => after.data.tags?.some(saved => saved.tag === tag))) throw new Error("Zotero tag write could not be verified.");
    return answer("Zotero 更新已回读核验。", { item: after });
  }
  if (input.action === "add_note") {
    const parentKey = checkKey(input.parentKey);
    if (typeof input.markdown !== "string" || !input.markdown.trim()) throw new Error("add_note requires Markdown text.");
    const html = input.html || `<div data-schema-version="9"><p style="white-space:pre-wrap">${escapeHtml(input.markdown)}</p></div>`;
    if (typeof html !== "string" || /<\s*(?:script|iframe|object|embed)\b|\bon\w+\s*=|javascript:/i.test(html)) throw new Error("Note HTML contains unsupported active content.");
    await enableWrites();
    const parent = await getItem(parentKey);
    if (Number.isInteger(input.expectedVersion) && (parent.version ?? parent.data.version) !== input.expectedVersion) throw new Error("Zotero parent changed; no note was written.");
    const children = await list(`/items/${parentKey}/children`);
    const previous = children.find(child => child.data?.itemType === "note" && (child.data.note === html || input.marker && child.data.note?.includes(input.marker)));
    if (previous) {
      if (previous.data.note !== html) throw new Error("A note with this marker already exists; update its verified version instead.");
      return answer("Zotero 笔记已存在。", { item: previous, alreadyExists: true });
    }
    const result = await (await request("/items", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify([{ itemType: "note", parentItem: parentKey, note: html, tags: [] }]) })).json();
    const createdKey = result.successful?.["0"]?.key || result.success?.["0"];
    if (!createdKey || Object.keys(result.failed || {}).length) throw new Error("Zotero note creation failed.");
    const note = await getItem(createdKey);
    if (note.data.parentItem !== parentKey || note.data.itemType !== "note" || note.data.note !== html) throw new Error("Zotero note readback did not match.");
    return answer("Zotero 笔记已创建并回读核验。", { item: note });
  }
  if (input.action === "create_collection") {
    if (typeof input.name !== "string" || !input.name.trim()) throw new Error("Collection name is required.");
    if (input.parentCollection) checkKey(input.parentCollection);
    await enableWrites();
    const parent = input.parentCollection || false;
    const existing = (await list("/collections")).find(collection => collection.data?.name === input.name && (collection.data.parentCollection || false) === parent);
    if (existing) return answer("Zotero 分类已存在。", { collection: existing, alreadyExists: true });
    const result = await (await request("/collections", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify([{ name: input.name, parentCollection: parent }]) })).json();
    const key = checkKey(result.successful?.["0"]?.key || result.success?.["0"]);
    const collection = await (await request(`/collections/${key}`)).json();
    if (collection.key !== key || collection.data?.name !== input.name || (collection.data.parentCollection || false) !== parent) throw new Error("Zotero collection readback did not match.");
    return answer("Zotero 分类已创建并回读核验。", { collection });
  }
  throw new Error("Unsupported Zotero action.");
}
