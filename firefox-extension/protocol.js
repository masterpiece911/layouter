/* Visible browser changes require invocation. Duplicate identity metadata is maintained passively. */
"use strict";
const FIREFOX_PROTOCOL_VERSION = 2;

// Wire results are explicit structural records, never serialized WebExtension objects.
// This recursive guard is defense in depth; even errors must not expose API details.
const FORBIDDEN_RESPONSE_KEYS = new Set([
  "url", "currentUrl", "current_url", "previousUrl", "title", "pageTitle",
  "favIconUrl", "pendingUrl", "history", "domain", "origin", "content",
  "pageContent", "searchQuery", "urlMatches",
]);
function assertPrivateResponse(value) {
  if (value && typeof value === "object") {
    for (const [key, child] of Object.entries(value)) {
      if (FORBIDDEN_RESPONSE_KEYS.has(key)) throw new Error("Unsafe companion response");
      assertPrivateResponse(child);
    }
  }
}
class ProtocolError extends Error {
  constructor(code, message) { super(message); this.code = code; }
}
const WINDOW_KEY = "layouter.window.v1";
const TAB_KEY = "layouter.tab.v1";

/**
 * @typedef {{id: string, url: string, pinned: boolean, active: boolean}} TabDeclaration Inbound only.
 * @typedef {{tab: object, identity: string}} InternalManagedTab Never serialized.
 * @typedef {{id: number, identity: string}} TabIdentityResult Outbound structural metadata only.
 */
function createProtocol(browser) {
  // Ephemeral observations select the original while connected; session values
  // remain the source of truth. No browsing information is retained here.
  const trackedTabs = new Map();
  function fail(code, message) { throw new ProtocolError(code, message); }
  function string(value, name) {
    if (typeof value !== "string" || !value) fail("InvalidRequest", `${name} must be nonempty`);
    return value;
  }
  function declaration(tab) {
    string(tab.id, "tab.id"); string(tab.url, "tab.url");
    if (typeof tab.pinned !== "boolean" || typeof tab.active !== "boolean") {
      fail("InvalidRequest", "pinned and active must be booleans");
    }
    return tab;
  }
  async function windows(key) {
    const result = [];
    for (const win of await browser.windows.getAll({ windowTypes: ["normal"] })) {
      if (!win.incognito && await browser.sessions.getWindowValue(win.id, WINDOW_KEY) === key) result.push(win);
    }
    if (result.length > 1) fail("AmbiguousState", `Multiple live browser windows carry FirefoxWindow identity ${key}; refusing to guess`);
    return result;
  }
  async function window(key) {
    const found = await windows(key);
    if (!found.length) fail("MissingWindow", "FirefoxWindow closed during invocation; invoke Layouter again");
    return found[0];
  }
  async function tabs(key) {
    const win = await window(key);
    const groups = new Map();
    for (const tab of await browser.tabs.query({ windowId: win.id })) {
      if (tab.incognito) continue;
      const identity = await browser.sessions.getTabValue(tab.id, TAB_KEY);
      if (identity?.window !== key || typeof identity.tab !== "string") continue;
      if (!groups.has(identity.tab)) groups.set(identity.tab, []);
      groups.get(identity.tab).push(tab);
    }
    const previous = trackedTabs.get(key) || new Map(), current = new Map(), result = [];
    for (const [identity, copies] of groups) {
      const original = copies.find(tab => tab.id === previous.get(identity)) ||
        copies.reduce((first, tab) => tab.id < first.id ? tab : first);
      for (const copy of copies) {
        if (copy.id === original.id) continue;
        // Recheck live containment and metadata before touching only our tab value.
        const fresh = await browser.tabs.query({ windowId: win.id });
        if (!fresh.some(tab => tab.id === original.id && !tab.incognito) ||
            !fresh.some(tab => tab.id === copy.id && !tab.incognito)) {
          fail("ChangedState", "Tab parent changed during identity maintenance; invoke again");
        }
        const originalValue = await browser.sessions.getTabValue(original.id, TAB_KEY);
        const copyValue = await browser.sessions.getTabValue(copy.id, TAB_KEY);
        if (originalValue?.window !== key || originalValue.tab !== identity ||
            copyValue?.window !== key || copyValue.tab !== identity) {
          fail("ChangedState", "Tab identity changed during maintenance; invoke again");
        }
        await browser.sessions.removeTabValue(copy.id, TAB_KEY);
      }
      current.set(identity, original.id);
      // Internal only: browser-derived fields never enter a wire result.
      result.push({ tab: original, identity });
    }
    trackedTabs.set(key, current);
    return result;
  }
  async function owned(key) {
    return ((await browser.storage.local.get("ownership")).ownership || []).includes(key);
  }
  async function target(request) {
    const key = string(request.windowElementId, "windowElementId");
    const current = (await tabs(key)).find(t => t.identity === request.tabIdentity);
    if (!current || current.tab.id !== request.tabId) fail("ChangedState", "Tab identity or parent changed; invoke again");
    return current;
  }
  async function bootstrap(url) {
    const parsed = new URL(string(url, "url"));
    if (parsed.protocol !== "http:" || parsed.hostname !== "127.0.0.1" ||
        !/^\/layouter\/[a-f0-9]{64}$/.test(parsed.pathname) || parsed.search || parsed.hash) {
      fail("InvalidRequest", "Invalid local bootstrap URL");
    }
    return (await browser.tabs.query({})).filter(t => !t.incognito && t.url === url);
  }
  async function dispatch(request) {
    if (request.version !== FIREFOX_PROTOCOL_VERSION) {
      fail("ProtocolMismatch", `Companion protocol version ${FIREFOX_PROTOCOL_VERSION} does not match Layouter protocol version ${request.version}`);
    }
    if (request.op === "hello") return { instanceId: (await browser.storage.local.get("instanceId")).instanceId };
    if (request.op === "find-bootstrap") return (await bootstrap(request.url)).map(t => t.id);
    if (request.op === "remove-bootstrap") {
      const found = await bootstrap(request.url);
      if (found.length !== 1 || found[0].id !== request.tabId) fail("ChangedState", "Bootstrap tab changed; preserving it");
      await browser.tabs.remove(request.tabId);
      return null;
    }
    const key = string(request.windowElementId, "windowElementId");
    if (request.op === "inspect") {
      const found = await windows(key);
      return { owned: await owned(key), windowId: found[0]?.id ?? null,
               tabs: found.length ? (await tabs(key)).map(({ tab, identity }) => ({ id: tab.id, identity })) : [] };
    }
    if (request.op === "claim-window") {
      const ownership = (await browser.storage.local.get("ownership")).ownership || [];
      if (!ownership.includes(key)) await browser.storage.local.set({ ownership: [...ownership, key] });
      return null;
    }
    if (request.op === "create-window") {
      if (!await owned(key)) fail("NotOwned", "Claim ownership before creating a FirefoxWindow");
      const found = await windows(key);
      if (found.length) return found[0].id;
      const first = request.tab === undefined ? null : declaration(request.tab);
      // Use the first declaration as the initial tab; empty declarations get Firefox's default.
      const win = await browser.windows.create({ incognito: false, focused: false, type: "normal",
                                                 ...(first ? { url: first.url } : {}) });
      await browser.sessions.setWindowValue(win.id, WINDOW_KEY, key);
      if (first) {
        const initial = await browser.tabs.query({ windowId: win.id });
        if (initial.length !== 1 || initial[0].incognito) {
          fail("ChangedState", "New FirefoxWindow initial tab is ambiguous; preserving it");
        }
        await browser.sessions.setTabValue(initial[0].id, TAB_KEY, { window: key, tab: first.id });
        if (first.pinned) await browser.tabs.update(initial[0].id, { pinned: true });
      }
      return win.id;
    }
    if (request.op === "ensure-tabs" || request.op === "sync-tabs") {
      if (!Array.isArray(request.tabs)) fail("InvalidRequest", "tabs must be an array");
      const specs = request.tabs.map(declaration);
      if (new Set(specs.map(spec => spec.id)).size !== specs.length ||
          specs.filter(spec => spec.active).length > 1) {
        fail("InvalidRequest", "Duplicate tab declarations or multiple active tabs");
      }
      // Maintain copied tab identities before invoking visible reconciliation.
      await tabs(key);
      const sync = request.op === "sync-tabs";
      for (const spec of specs) {
        let current = (await tabs(key)).find(t => t.identity === spec.id);
        if (sync && current && current.tab.url !== spec.url) {
          // Recheck parentage immediately before removing only our metadata.
          current = await target({ ...request, tabId: current.tab.id, tabIdentity: spec.id });
          if (current.tab.url !== spec.url) {
            await browser.sessions.removeTabValue(current.tab.id, TAB_KEY);
            current = null;
          }
        }
        if (!current) {
          const win = await window(key);
          const tab = await browser.tabs.create({ windowId: win.id, url: spec.url,
                                                  pinned: spec.pinned, active: spec.active });
          await browser.sessions.setTabValue(tab.id, TAB_KEY, { window: key, tab: spec.id });
          await target({ ...request, tabId: tab.id, tabIdentity: spec.id });
        }
      }
      if (sync) {
        const selected = [];
        for (const spec of specs) {
          const current = (await tabs(key)).find(t => t.identity === spec.id);
          if (!current) fail("ChangedState", "Tab parent or identity changed; invoke again");
          if (current.tab.pinned !== spec.pinned) await browser.tabs.update(current.tab.id, { pinned: spec.pinned });
          selected.push({ spec, id: current.tab.id });
        }
        // Put declared tabs first within each valid pinned/unpinned partition.
        for (const pinned of [true, false]) {
          let offset = 0;
          for (const { spec, id } of selected.filter(t => t.spec.pinned === pinned)) {
            await target({ ...request, tabId: id, tabIdentity: spec.id });
            const all = await browser.tabs.query({ windowId: (await window(key)).id });
            const index = (pinned ? 0 : all.filter(t => t.pinned).length) + offset;
            await browser.tabs.move(id, { index });
            offset += 1;
          }
        }
        const active = selected.find(t => t.spec.active);
        if (active) {
          await target({ ...request, tabId: active.id, tabIdentity: active.spec.id });
          await browser.tabs.update(active.id, { active: true });
        }
        // Verify synchronous effects here, never by returning them to Python.
        // tabs.create resolves before navigation finishes. A loading tab or a
        // server redirect does not invalidate successful creation at spec.url.
        // Compare URLs only when deciding whether to replace an existing tab.
        for (const { spec, id } of selected) {
          const { tab } = await target({ ...request, tabId: id, tabIdentity: spec.id });
          if (tab.pinned !== spec.pinned || (spec.active && !tab.active)) {
            fail("ChangedState", "Firefox tabs changed during synchronization; invoke again");
          }
        }
      }
      return null;
    }
    if (request.op === "probe-window" || request.op === "clear-probe") {
      const win = await window(key);
      if (win.id !== request.windowId) fail("ChangedState", "Browser window changed during correlation");
      if (request.op === "probe-window" && !/^[a-f0-9]{64}$/.test(request.nonce)) fail("InvalidRequest", "Invalid probe nonce");
      await browser.windows.update(win.id, { titlePreface: request.op === "clear-probe" ? "" : `__layouter_probe_${request.nonce}__` });
      return null;
    }
    fail("InvalidRequest", `Unknown operation ${request.op}`);
  }
  const handle = async request => {
    let response;
    try {
      response = { id: request.id, version: FIREFOX_PROTOCOL_VERSION, ok: true, result: await dispatch(request) };
      assertPrivateResponse(response);
    } catch (error) {
      // WebExtension exceptions may embed URLs/titles. Only our own fixed diagnostics
      // and Layouter-authored identities may cross the native boundary.
      response = { id: request.id, version: FIREFOX_PROTOCOL_VERSION, ok: false,
                   code: error instanceof ProtocolError ? error.code : "BrowserError",
                   message: error instanceof ProtocolError ? error.message : "Firefox operation failed; check the companion and invoke again" };
    }
    assertPrivateResponse(response);
    return response;
  };
  handle.maintainTabIdentities = async windowId => {
    const candidates = new Map();
    for (const win of await browser.windows.getAll({ windowTypes: ["normal"] })) {
      if (win.incognito) continue;
      const key = await browser.sessions.getWindowValue(win.id, WINDOW_KEY);
      if (typeof key !== "string" || !key) continue;
      if (!candidates.has(key)) candidates.set(key, []);
      candidates.get(key).push(win.id);
    }
    for (const [key, ids] of candidates) {
      // Never resolve duplicate logical windows, including manual window restore.
      if (ids.length === 1 && (windowId === undefined || ids[0] === windowId)) await tabs(key);
    }
    for (const key of trackedTabs.keys()) if (!candidates.has(key)) trackedTabs.delete(key);
  };
  return handle;
}
// Node's built-in test runner loads the same implementation, without a browser dependency.
if (typeof module !== "undefined") module.exports = { createProtocol, assertPrivateResponse, FIREFOX_PROTOCOL_VERSION, WINDOW_KEY, TAB_KEY };
