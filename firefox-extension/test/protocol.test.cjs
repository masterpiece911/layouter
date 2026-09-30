const test = require('node:test');
const { createTabBadges } = require('../badges.js');
const assert = require('node:assert/strict');
const { createProtocol, assertPrivateResponse, FIREFOX_PROTOCOL_VERSION, WINDOW_KEY, TAB_KEY } = require('../protocol.js');

function fixture() {
  let next = 100;
  const storage = {}, windows = [], tabs = [], windowValues = new Map(), tabValues = new Map(), mutations = [];
  const badges = new Map();
  const browser = {
    browserAction: Object.fromEntries(['setBadgeText', 'setBadgeTextColor', 'setBadgeBackgroundColor', 'setTitle'].map(method =>
      [method, async ({ tabId, ...values }) => badges.set(tabId, { ...badges.get(tabId), [method]: values })])),
    storage: { local: {
      get: async key => ({ [key]: structuredClone(storage[key]) }),
      set: async values => Object.assign(storage, structuredClone(values)),
    } },
    sessions: {
      getWindowValue: async (id, key) => windowValues.get(id)?.[key],
      setWindowValue: async (id, key, value) => {
        windowValues.set(id, { ...windowValues.get(id), [key]: value });
      },
      getTabValue: async (id, key) => tabValues.get(id)?.[key],
      setTabValue: async (id, key, value) => tabValues.set(id, { ...tabValues.get(id), [key]: value }),
      removeTabValue: async (id, key) => { mutations.push(['detach', id]); delete tabValues.get(id)[key]; },
    },
    windows: {
      getAll: async () => structuredClone(windows),
      create: async opts => {
        const win = { id: next++, ...opts }; windows.push(win); mutations.push(['window', win.id]);
        if (opts.url) await browser.tabs.create({ windowId: win.id, url: opts.url, pinned: false, active: true });
        return win;
      },
      update: async (id, opts) => { mutations.push(['window-update', id, opts]); Object.assign(windows.find(w => w.id === id), opts); },
    },
    tabs: {
      ...Object.fromEntries(['onCreated', 'onUpdated', 'onAttached', 'onRemoved', 'onActivated'].map(name => {
        const listeners = [];
        return [name, { addListener: fn => listeners.push(fn), emit: (...args) => listeners.forEach(fn => fn(...args)) }];
      })),
      query: async query => structuredClone(tabs.filter(t => query.windowId === undefined || t.windowId === query.windowId)),
      create: async opts => {
        const tab = { id: next++, incognito: false, index: tabs.filter(t => t.windowId === opts.windowId).length, ...opts };
        if (opts.active) for (const t of tabs.filter(t => t.windowId === opts.windowId)) t.active = false;
        tabs.push(tab); mutations.push(['tab', tab.id]); return tab;
      },
      update: async (id, opts) => {
        const tab = tabs.find(t => t.id === id);
        if (opts.active) for (const t of tabs.filter(t => t.windowId === tab.windowId)) t.active = false;
        Object.assign(tab, opts); mutations.push(['update', id, opts]);
      },
      move: async (id, { index }) => {
        const tab = tabs.find(t => t.id === id);
        const siblings = tabs.filter(t => t.windowId === tab.windowId).sort((a, b) => a.index - b.index);
        siblings.splice(siblings.indexOf(tab), 1); siblings.splice(index, 0, tab);
        siblings.forEach((t, index) => { t.index = index; });
        mutations.push(['move', id, index]);
      },
      remove: async id => { mutations.push(['remove', id]); tabs.splice(tabs.findIndex(t => t.id === id), 1); },
    },
  };
  let handle = createProtocol(browser);
  const responses = [];
  async function raw(op, fields = {}) {
    const response = await handle({ id: 'request', version: FIREFOX_PROTOCOL_VERSION, op, windowElementId: 'work', ...fields });
    // Every success and failure in this suite traverses an independent wire-schema guard.
    function check(value) {
      if (value && typeof value === 'object') for (const [key, child] of Object.entries(value)) {
        assert.doesNotMatch(key, /^(url|currentUrl|current_url|previousUrl|title|pageTitle|favIconUrl|pendingUrl|history|domain|origin|content|pageContent|searchQuery|urlMatches)$/);
        check(child);
      }
    }
    check(response);
    responses.push(response);
    return response;
  }
  async function call(op, fields = {}) {
    const response = await raw(op, fields);
    if (!response.ok) throw Object.assign(new Error(response.message), { code: response.code });
    return response.result;
  }
  async function ensure(tab) {
    await call('ensure-tabs', { tabs: [tab] });
    return (await call('inspect')).tabs.find(t => t.identity === tab.id).id;
  }
  return { browser, badges, storage, windows, tabs, windowValues, tabValues, mutations, raw, call, ensure, responses,
    maintain: windowId => handle.maintainTabIdentities(windowId),
    restart: () => { handle = createProtocol(browser); } };
}
const spec = (id, overrides = {}) => ({ id, url: `https://${id}.example`, pinned: false, active: false, ...overrides });
async function setup(f) { await f.call('claim-window'); return f.call('create-window'); }

test('ownership, window/tab values survive companion reload; no autonomous mutations', async () => {
  const f = fixture();
  const id = await setup(f);
  const tabId = await f.ensure(spec('https://literal/url', { pinned: true, active: true }));
  assert.equal(f.windowValues.get(id)[WINDOW_KEY], 'work');
  assert.deepEqual(f.tabValues.get(tabId)[TAB_KEY], { window: 'work', tab: 'https://literal/url' });
  const before = structuredClone(f.mutations);
  f.restart();
  assert.deepEqual((await f.call('inspect')).tabs[0].identity, 'https://literal/url');
  assert.deepEqual(f.storage.ownership, ['work']);
  assert.deepEqual(f.mutations, before);
  f.windows.splice(0); f.tabs.splice(0); // User closes the window; no callback recreates it.
  assert.deepEqual(await f.call('inspect'), { owned: true, windowId: null, tabs: [] });
  assert.deepEqual(f.mutations, before);
  await f.call('create-window');
  assert.equal(f.tabs.length, 0);
});

test('moved tab outside parent is ignored; both managed and unmanaged destinations preserved', async () => {
  for (const managed of [false, true]) {
    const f = fixture(); await setup(f);
    const id = await f.ensure(spec('repo'));
    f.windows.push({ id: 5 });
    if (managed) f.windowValues.set(5, { [WINDOW_KEY]: 'other' });
    f.tabs[0].windowId = 5;
    const moved = structuredClone(f.tabs[0]);
    assert.equal((await f.call('inspect')).tabs.length, 0);
    await f.ensure(spec('repo'));
    assert.deepEqual(f.tabs.find(t => t.id === id), moved);
    assert.equal((await f.call('inspect')).tabs.length, 1);
    assert.equal((await f.call('inspect', { windowElementId: 'other' })).tabs.length, 0);
  }
});

test('copied tab identity is detached from the duplicate while both tabs remain unchanged', async () => {
  const f = fixture(); await setup(f);
  const id = await f.ensure(spec('jira'));
  f.tabs.push({ ...f.tabs[0], id: 500 });
  assert.equal((await f.call('inspect')).tabs.length, 1);
  f.tabValues.set(500, structuredClone(f.tabValues.get(id)));
  const before = structuredClone(f.tabs), mutations = f.mutations.length;
  assert.deepEqual((await f.call('inspect')).tabs, [{ id, identity: 'jira' }]);
  assert.deepEqual(f.tabs, before);
  assert.deepEqual(f.mutations.slice(mutations), [['detach', 500]]);
  assert.equal(f.tabValues.get(500)[TAB_KEY], undefined);
  assert.equal(f.tabValues.get(id)[TAB_KEY].tab, 'jira');
});

test('duplicate windows refuse to guess, including manually restored old window', async () => {
  const f = fixture(); const id = await setup(f);
  f.windows.push({ id: 500 }); f.windowValues.set(500, structuredClone(f.windowValues.get(id)));
  const before = structuredClone(f.mutations);
  assert.equal((await f.raw('inspect')).code, 'AmbiguousState');
  assert.equal((await f.raw('create-window')).code, 'AmbiguousState');
  assert.deepEqual(f.mutations, before);
});

test('close/restore with new runtime IDs retains identity, without recently-closed APIs', async () => {
  const f = fixture(); await setup(f);
  const old = await f.ensure(spec('jira'));
  const metadata = structuredClone(f.tabValues.get(old));
  f.tabs[0].id = 901; f.tabValues.set(901, metadata);
  const win = f.windows[0]; f.windowValues.set(902, structuredClone(f.windowValues.get(win.id)));
  win.id = 902; f.tabs[0].windowId = 902;
  const state = await f.call('inspect');
  assert.equal(state.windowId, 902); assert.equal(state.tabs[0].identity, 'jira');
});

test('private state is ignored and cannot be probed or edited', async () => {
  const f = fixture(); const id = await setup(f);
  await f.ensure(spec('jira'));
  f.tabs[0].incognito = true;
  assert.equal((await f.call('inspect')).tabs.length, 0);
  f.windows[0].incognito = true;
  assert.equal((await f.call('inspect')).windowId, null);
  const before = structuredClone(f.mutations);
  assert.equal((await f.raw('probe-window', { windowId: id, nonce: 'a'.repeat(64) })).code, 'MissingWindow');
  assert.deepEqual(f.mutations, before);
});

test('URL replacement detaches identity but never navigates or closes old tab', async () => {
  const f = fixture(); await setup(f);
  const id = await f.ensure(spec('jira'));
  f.tabs[0].url = 'https://jira.example/unsaved';
  const old = structuredClone(f.tabs[0]);
  const response = await f.raw('sync-tabs', { tabs: [spec('jira')] });
  assert.deepEqual(response, { id: 'request', version: FIREFOX_PROTOCOL_VERSION, ok: true, result: null });
  assert.equal(f.tabValues.get(id)[TAB_KEY], undefined);
  assert.equal(f.tabValues.get(f.tabs[1].id)[TAB_KEY].tab, 'jira');
  // The replacement moves left; the preserved old tab shifts right without navigation.
  assert.deepEqual(f.tabs[0], { ...old, index: 1 });
  assert.equal(f.tabs.length, 2);
  assert.equal((await f.call('inspect')).tabs.length, 1);
  assert.equal(f.mutations.some(([op]) => op === 'remove'), false);
});

test('sync accepts loading and redirected replacements without navigating preserved tabs', async () => {
  for (const resultingUrl of ['about:blank', 'https://jira.example/redirected']) {
    const f = fixture(); await setup(f);
    const declaration = spec('jira', { pinned: true, active: true });
    const original = await f.ensure(declaration);
    f.tabs[0].url = 'https://jira.example/unsaved';
    const create = f.browser.tabs.create;
    const requested = [];
    f.browser.tabs.create = async opts => {
      requested.push(structuredClone(opts));
      const tab = await create(opts);
      tab.url = resultingUrl;
      return tab;
    };
    assert.equal(await f.call('sync-tabs', { tabs: [declaration] }), null);
    assert.equal(requested.length, 1);
    assert.equal(requested[0].url, declaration.url);
    assert.equal(f.tabs.length, 2);
    assert.equal(f.tabs.find(t => t.id === original).url, 'https://jira.example/unsaved');
    assert.equal(f.tabValues.get(original)[TAB_KEY], undefined);
    const managed = (await f.call('inspect')).tabs;
    assert.equal(managed.length, 1);
    const replacement = f.tabs.find(t => t.id === managed[0].id);
    assert.notEqual(replacement.id, original);
    assert.equal(replacement.url, resultingUrl);
    assert.equal(replacement.pinned, true);
    assert.equal(replacement.active, true);
    assert.equal(replacement.index, 0);
    assert.equal(f.mutations.some(([op]) => op === 'remove'), false);
    const before = structuredClone(f.mutations);
    await f.call('ensure-tabs', { tabs: [declaration] });
    assert.deepEqual(f.mutations, before);
  }
});

test('sync puts declared tabs leftmost in each partition, preserves unmanaged tabs, then activates', async () => {
  const f = fixture(); const windowId = await setup(f);
  const specs = [spec('pA', { pinned: true }), spec('uA', { active: true }),
    spec('pB', { pinned: true }), spec('uB')];
  const ids = {};
  for (const tab of [specs[2], specs[0], specs[3], specs[1]]) ids[tab.id] = await f.ensure(tab);
  await f.browser.tabs.create({ windowId, url: 'user pinned', pinned: true, active: false });
  await f.browser.tabs.create({ windowId, url: 'user unpinned', pinned: false, active: false });
  // Start with a valid Firefox pinned partition but arbitrary order within it.
  f.tabs.sort((a, b) => Number(b.pinned) - Number(a.pinned)).forEach((t, i) => { t.index = i; });
  const allIds = f.tabs.map(t => t.id).sort();
  await f.call('sync-tabs', { tabs: specs });
  assert.deepEqual(f.tabs.map(t => t.id).sort(), allIds);
  const ordered = f.tabs.toSorted((a, b) => a.index - b.index);
  assert.deepEqual(ordered.map(t => t.url), [specs[0].url, specs[2].url, 'user pinned',
    specs[1].url, specs[3].url, 'user unpinned']);
  assert.equal(f.tabs.find(t => t.active).id, ids.uA);
  assert.deepEqual(f.mutations.at(-1), ['update', ids.uA, { active: true }]);
  const firstOrder = ordered.map(t => t.id);
  await f.call('sync-tabs', { tabs: specs });
  assert.deepEqual(f.tabs.toSorted((a, b) => a.index - b.index).map(t => t.id), firstOrder);
});

test('sync pin correction and no declared active leaves active state alone', async () => {
  const f = fixture(); await setup(f);
  const id = await f.ensure(spec('repo'));
  f.tabs[0].pinned = true;
  await f.call('sync-tabs', { tabs: [spec('repo')] });
  assert.equal(f.tabs[0].pinned, false);
  assert.equal(f.mutations.some(([op, , opts]) => op === 'update' && opts.active !== undefined), false);
  assert.equal(f.tabs[0].id, id);
});

test('bootstrap matches exact nonce URL, never removes unrelated or changed tabs', async () => {
  const f = fixture();
  const url = `http://127.0.0.1:5555/layouter/${'a'.repeat(64)}`;
  f.tabs.push({ id: 1, url }, { id: 2, url: `${url}different` }, { id: 3, url, incognito: true });
  assert.deepEqual(await f.call('find-bootstrap', { url }), [1]);
  await f.call('remove-bootstrap', { url, tabId: 1 });
  assert.deepEqual(f.tabs.map(t => t.id), [2, 3]);
  assert.equal((await f.raw('remove-bootstrap', { url, tabId: 2 })).code, 'ChangedState');
});

test('probe setup/clear and exact version mismatch', async () => {
  const f = fixture(); const windowId = await setup(f);
  const nonce = 'f'.repeat(64);
  await f.call('probe-window', { windowId, nonce });
  assert.equal(f.windows[0].titlePreface, `__layouter_probe_${nonce}__`);
  await f.call('clear-probe', { windowId });
  assert.equal(f.windows[0].titlePreface, '');
  assert.equal((await f.raw('create-window', { version: 1 })).code, 'ProtocolMismatch');
});


test('new window uses the first declaration without an extra blank tab', async () => {
  const f = fixture(); await f.call('claim-window');
  await f.call('create-window', { tab: spec('first', { pinned: true }) });
  const state = await f.call('inspect');
  assert.equal(f.tabs.length, 1);
  assert.equal(state.tabs[0].identity, 'first');
  assert.deepEqual(state.tabs[0], { id: f.tabs[0].id, identity: 'first' });
  assert.equal(f.tabs[0].url, 'https://first.example');
  assert.equal(f.tabs[0].pinned, true);
});

test('extra browser-created startup tabs fail safely without selecting or removing one', async () => {
  const f = fixture();
  const create = f.browser.windows.create;
  f.browser.windows.create = async opts => {
    const win = await create(opts);
    // Firefox-family browsers may copy pinned tabs or mirror another window.
    await f.browser.tabs.create({ windowId: win.id, url: 'https://existing.example',
                                 pinned: true, active: false });
    return win;
  };
  await f.call('claim-window');
  const result = await f.raw('create-window', { tab: spec('declared') });
  assert.equal(result.ok, false);
  assert.match(result.message, /initial tab is ambiguous/);
  assert.equal(f.windows.length, 1);
  assert.equal(f.tabs.length, 2);
  assert.equal(f.tabValues.size, 0);
  assert.equal(f.mutations.some(([op]) => op === 'remove' || op === 'detach'), false);
  assert.equal(f.windowValues.get(f.windows[0].id)[WINDOW_KEY], 'work');
});

const SECRET = 'https://secret.invalid/do-not-leak-938475';

test('manifest declares no transmission while retaining local Tabs API access', () => {
  const manifest = require('../manifest.json');
  assert.deepEqual(manifest.browser_specific_settings.gecko.data_collection_permissions, { required: ['none'] });
  assert.deepEqual(manifest.permissions.toSorted(), ['sessions', 'nativeMessaging', 'storage', 'tabs'].toSorted());
});

test('ordinary ensure retains divergent navigation, pinning and activation without exposing them', async () => {
  const f = fixture(); await setup(f);
  await f.ensure(spec('jira'));
  Object.assign(f.tabs[0], { url: SECRET, title: 'Private page', favIconUrl: SECRET + '/icon',
                             pinned: true, active: true });
  const before = structuredClone(f.tabs), mutations = structuredClone(f.mutations);
  assert.deepEqual(await f.call('ensure-tabs', { tabs: [spec('jira')] }), null);
  assert.deepEqual(await f.call('inspect'), { owned: true, windowId: f.windows[0].id,
    tabs: [{ id: f.tabs[0].id, identity: 'jira' }] });
  assert.deepEqual(f.tabs, before);
  assert.deepEqual(f.mutations, mutations);
  assert.ok(!JSON.stringify(f.responses).includes(SECRET));
});

test('sync handles missing tabs and URL replacement internally, returning only success', async () => {
  const f = fixture(); await setup(f);
  await f.ensure(spec('jira'));
  f.tabs[0].url = SECRET;
  const old = structuredClone(f.tabs[0]);
  const result = await f.raw('sync-tabs', { tabs: [spec('jira'), spec('new', { pinned: true, active: true })] });
  assert.deepEqual(result, { id: 'request', version: FIREFOX_PROTOCOL_VERSION, ok: true, result: null });
  assert.equal(f.tabs.find(t => t.id === old.id).url, SECRET);
  assert.equal(f.tabValues.get(old.id)[TAB_KEY], undefined);
  const managed = (await f.call('inspect')).tabs;
  assert.deepEqual(managed.map(t => t.identity).sort(), ['jira', 'new']);
  assert.equal(f.tabs.find(t => t.id === managed.find(t => t.identity === 'jira').id).url, spec('jira').url);
  assert.equal(f.mutations.some(([op, , opts]) => op === 'remove' || (op === 'update' && 'url' in opts)), false);
  assert.ok(!JSON.stringify(f.responses).includes(SECRET));
});

test('ambiguity and browser exceptions cannot transmit browsing data in messages or codes', async () => {
  const f = fixture(); await setup(f); const id = await f.ensure(spec('jira'));
  f.tabs[0].url = SECRET;
  f.tabs.push({ ...f.tabs[0], id: 500, title: SECRET });
  f.tabValues.set(500, structuredClone(f.tabValues.get(id)));
  await f.call('inspect'); // Duplicate tab becomes unmanaged without leaking its state.
  assert.equal(f.tabValues.get(500)[TAB_KEY], undefined);
  f.windows.push({ id: 600 });
  f.windowValues.set(600, { [WINDOW_KEY]: 'work' });
  for (const op of ['inspect', 'ensure-tabs', 'sync-tabs']) {
    assert.equal((await f.raw(op, { tabs: [spec('jira')] })).code, 'AmbiguousState');
  }
  f.windows.pop();
  // Error.code is not trusted either: API errors are not our ProtocolError class.
  f.browser.tabs.query = async () => { throw Object.assign(new Error(SECRET), { code: SECRET }); };
  for (const op of ['inspect', 'ensure-tabs', 'sync-tabs', 'find-bootstrap']) {
    assert.equal((await f.raw(op, { tabs: [spec('jira')], url: 'http://127.0.0.1:1234/layouter/' + 'a'.repeat(64) })).code, 'BrowserError');
  }
  assert.equal((await f.raw('hello', { version: 1 })).code, 'ProtocolMismatch');
  assert.ok(!JSON.stringify(f.responses).includes(SECRET));
});

test('outgoing guard rejects nested browser fields, including navigation-derived booleans', () => {
  for (const key of ['url', 'currentUrl', 'current_url', 'title', 'favIconUrl', 'pendingUrl',
                     'history', 'domain', 'origin', 'urlMatches']) {
    assert.throws(() => assertPrivateResponse({ result: [{ nested: { [key]: SECRET } }] }), /Unsafe companion response/);
  }
  assert.doesNotThrow(() => assertPrivateResponse({ result: { tabs: [{ id: 5, identity: 'https://authored.example/' }] } }));
});

test('background logging sanitizes API failures and native disconnects', async () => {
  const vm = require('node:vm');
  const fs = require('node:fs');
  const f = fixture(), messages = [], logs = [];
  f.storage.instanceId = 'a'.repeat(32);
  let onMessage, onDisconnect;
  const port = {
    onMessage: { addListener: fn => { onMessage = fn; } },
    onDisconnect: { addListener: fn => { onDisconnect = fn; } },
    postMessage: msg => { assertPrivateResponse(msg); messages.push(msg); },
    error: { message: SECRET },
  };
  f.browser.runtime = { connectNative: () => port };
  vm.runInNewContext(fs.readFileSync(require.resolve('../background.js'), 'utf8'), {
    browser: f.browser, createProtocol, createTabBadges, WINDOW_KEY, TAB_KEY, FIREFOX_PROTOCOL_VERSION, assertPrivateResponse,
    console: { error: (...args) => logs.push(args) }, setTimeout: () => {},
  });
  await new Promise(resolve => setImmediate(resolve));
  f.browser.windows.getAll = async () => { throw new Error(SECRET); };
  onMessage({ id: 'error', version: FIREFOX_PROTOCOL_VERSION, op: 'inspect', windowElementId: 'work' });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(messages.at(-1).code, 'BrowserError');
  port.postMessage = () => { throw new Error(SECRET); };
  onMessage({ id: 'send-error', version: FIREFOX_PROTOCOL_VERSION, op: 'hello' });
  await new Promise(resolve => setImmediate(resolve));
  onDisconnect();
  assert.ok(logs.length >= 2);
  assert.ok(!JSON.stringify({ messages, logs }).includes(SECRET));
});

test('native host relays privacy-safe ensure/sync/errors and emits no sensitive logs on disconnect', { timeout: 10000 }, async () => {
  const { spawn } = require('node:child_process');
  const fs = require('node:fs/promises'), path = require('node:path'), os = require('node:os');
  const net = require('node:net');
  const { once } = require('node:events');
  const runtime = await fs.mkdtemp(path.join(os.tmpdir(), 'layouter-privacy-'));
  const root = path.resolve(__dirname, '../..'), instance = 'a'.repeat(32);
  const f = fixture();
  const host = spawn(process.env.PYTHON || 'python3', ['-m', 'layouter.firefox_host'], {
    env: { ...process.env, PYTHONPATH: path.join(root, 'src'), XDG_RUNTIME_DIR: runtime },
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const exited = once(host, 'exit');
  let stderr = '', wire = '', buffered = Buffer.alloc(0), count = 0, chain = Promise.resolve();
  host.stderr.on('data', chunk => { stderr += chunk; });
  function write(value) {
    const payload = Buffer.from(JSON.stringify(value)), header = Buffer.alloc(4);
    if (os.endianness() === 'LE') header.writeUInt32LE(payload.length); else header.writeUInt32BE(payload.length);
    wire += payload;
    host.stdin.write(Buffer.concat([header, payload]));
  }
  host.stdout.on('data', chunk => {
    buffered = Buffer.concat([buffered, chunk]);
    while (buffered.length >= 4) {
      const size = os.endianness() === 'LE' ? buffered.readUInt32LE() : buffered.readUInt32BE();
      if (buffered.length < size + 4) break;
      const payload = buffered.subarray(4, size + 4).toString();
      buffered = buffered.subarray(size + 4); wire += payload;
      const req = JSON.parse(payload);
      chain = chain.then(async () => write(await f.raw(req.op, req)));
    }
  });
  try {
    write({ op: 'register', version: FIREFOX_PROTOCOL_VERSION, instanceId: instance });
    const socketPath = path.join(runtime, 'layouter/firefox', instance + '.sock');
    const deadline = Date.now() + 3000;
    while (true) {
      try { await fs.stat(socketPath); break; }
      catch (error) {
        if (Date.now() >= deadline) throw new Error('Native host unavailable: ' + stderr);
        await new Promise(resolve => setTimeout(resolve, 10));
      }
    }
    async function rpc(op, fields = {}) {
      const conn = net.createConnection(socketPath);
      conn.setTimeout(2000, () => conn.destroy(new Error('Native host timed out')));
      await once(conn, 'connect');
      conn.write(JSON.stringify({ id: String(++count), version: FIREFOX_PROTOCOL_VERSION, windowElementId: 'work', op, ...fields }) + '\n');
      let text = '';
      for await (const chunk of conn) text += chunk;
      wire += text;
      return JSON.parse(text);
    }
    assert.equal((await rpc('claim-window')).ok, true);
    assert.equal((await rpc('create-window')).ok, true);
    assert.equal((await rpc('ensure-tabs', { tabs: [spec('jira')] })).ok, true);
    f.tabs[0].url = SECRET; f.tabs[0].title = SECRET;
    assert.equal((await rpc('ensure-tabs', { tabs: [spec('jira')] })).ok, true);
    assert.equal((await rpc('sync-tabs', { tabs: [spec('jira')] })).ok, true);
    assert.equal(f.tabs[0].url, SECRET);
    const managed = f.tabs.at(-1);
    f.tabs.push({ ...managed, id: 999, url: SECRET });
    f.tabValues.set(999, structuredClone(f.tabValues.get(managed.id)));
    assert.equal((await rpc('inspect')).ok, true);
    assert.equal(f.tabValues.get(999)[TAB_KEY], undefined);
    f.windows.push({ id: 888 }); f.windowValues.set(888, { [WINDOW_KEY]: 'work' });
    assert.equal((await rpc('sync-tabs', { tabs: [spec('jira')] })).code, 'AmbiguousState');
    f.windows.pop();
    assert.equal((await rpc('hello', { version: 1 })).code, 'ProtocolMismatch');
    f.browser.tabs.query = async () => { throw new Error(SECRET); };
    assert.equal((await rpc('inspect')).code, 'BrowserError');
    await chain;
    host.stdin.end();
    assert.equal((await exited)[0], 0);
    assert.ok(!wire.includes(SECRET));
    assert.ok(!stderr.includes(SECRET));
    await assert.rejects(fs.stat(socketPath), { code: 'ENOENT' });
  } finally {
    if (host.exitCode === null) { host.kill(); await exited; }
    await fs.rm(runtime, { recursive: true, force: true });
  }
});

test('ordinary ensure never reads the current URL of an existing managed tab', async () => {
  const f = fixture(); await setup(f); await f.ensure(spec('jira'));
  const query = f.browser.tabs.query;
  f.browser.tabs.query = async fields => (await query(fields)).map(tab => {
    Object.defineProperty(tab, 'url', { get() { throw new Error('Existing URL was read'); } });
    return tab;
  });
  assert.equal((await f.raw('ensure-tabs', { tabs: [spec('jira')] })).ok, true);
  assert.equal((await f.raw('inspect')).ok, true);
});

test('sync puts unpinned declarations after all pinned tabs and before unmanaged unpinned tabs', async () => {
  const f = fixture(); const windowId = await setup(f);
  await f.browser.tabs.create({ windowId, url: 'user pinned', pinned: true, active: false });
  await f.browser.tabs.create({ windowId, url: 'user unpinned', pinned: false, active: false });
  await f.ensure(spec('B')); await f.ensure(spec('A'));
  const before = f.tabs.map(t => t.id).sort();
  await f.call('ensure-tabs', { tabs: [spec('A'), spec('B')] });
  assert.deepEqual(f.tabs.toSorted((a,b) => a.index-b.index).map(t => t.url),
    ['user pinned', 'user unpinned', spec('B').url, spec('A').url]);
  await f.call('sync-tabs', { tabs: [spec('A'), spec('B')] });
  assert.deepEqual(f.tabs.toSorted((a,b) => a.index-b.index).map(t => t.url),
    ['user pinned', spec('A').url, spec('B').url, 'user unpinned']);
  assert.deepEqual(f.tabs.map(t => t.id).sort(), before);
});

test('metadata maintenance keeps a previously tracked original even with a lower-ID duplicate', async () => {
  const f = fixture(); await setup(f); const id = await f.ensure(spec('jira'));
  const duplicate = { ...f.tabs[0], id: 1, url: SECRET, active: true };
  f.tabs.push(duplicate); f.tabValues.set(1, structuredClone(f.tabValues.get(id)));
  const before = structuredClone(f.tabs), count = f.mutations.length;
  await f.maintain();
  assert.deepEqual(f.tabs, before);
  assert.deepEqual(f.mutations.slice(count), [['detach', 1]]);
  assert.equal(f.tabValues.get(id)[TAB_KEY].tab, 'jira');
  assert.equal(f.tabValues.get(1)[TAB_KEY], undefined);
});

test('startup duplicates keep lowest runtime ID independent of query ordering', async () => {
  const f = fixture(); await setup(f); const id = await f.ensure(spec('jira'));
  f.tabs.unshift({ ...f.tabs[0], id: 999 });
  f.tabValues.set(999, structuredClone(f.tabValues.get(id)));
  f.restart();
  await f.maintain();
  assert.equal(f.tabValues.get(id)[TAB_KEY].tab, 'jira');
  assert.equal(f.tabValues.get(999)[TAB_KEY], undefined);
  const count = f.mutations.length;
  await f.maintain();
  assert.equal(f.mutations.length, count);
});

test('passive maintenance does not choose between duplicate window identities', async () => {
  const f = fixture(); const win = await setup(f); const id = await f.ensure(spec('jira'));
  f.tabs.push({ ...f.tabs[0], id: 999 }); f.tabValues.set(999, structuredClone(f.tabValues.get(id)));
  f.windows.push({ id: 888 }); f.windowValues.set(888, { [WINDOW_KEY]: 'work' });
  const count = f.mutations.length;
  await f.maintain(win);
  assert.equal(f.mutations.length, count);
  assert.equal((await f.raw('inspect')).code, 'AmbiguousState');
});

test('background duplicate lifecycle clears copied metadata without an invocation or visible mutation', async () => {
  const vm = require('node:vm'), fs = require('node:fs');
  const f = fixture(); const win = await setup(f); const id = await f.ensure(spec('jira'));
  f.storage.instanceId = 'a'.repeat(32);
  f.browser.runtime = { connectNative: () => ({
    onMessage: { addListener() {} }, onDisconnect: { addListener() {} }, postMessage: assertPrivateResponse,
  }) };
  const logs = [];
  vm.runInNewContext(fs.readFileSync(require.resolve('../background.js'), 'utf8'), {
    browser: f.browser, createProtocol, createTabBadges, WINDOW_KEY, TAB_KEY, FIREFOX_PROTOCOL_VERSION, assertPrivateResponse,
    console: { error: (...args) => logs.push(args) }, setTimeout() {},
  });
  await new Promise(resolve => setImmediate(resolve));
  f.tabs.push({ ...f.tabs[0], id: 999, url: SECRET });
  f.browser.tabs.onCreated.emit(f.tabs.at(-1));
  await new Promise(resolve => setImmediate(resolve));
  // Firefox may copy session values after onCreated; onUpdated covers completion.
  f.tabValues.set(999, structuredClone(f.tabValues.get(id)));
  const before = structuredClone(f.tabs), count = f.mutations.length;
  f.browser.tabs.onUpdated.emit(999, { status: 'complete' }, f.tabs.at(-1));
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.tabValues.get(999)[TAB_KEY], undefined);
  assert.deepEqual(f.tabs, before);
  assert.deepEqual(f.mutations.slice(count), [['detach', 999]]);
  // Closing the original does not recreate it or adopt its now-unmanaged duplicate.
  f.tabs.splice(f.tabs.findIndex(t => t.id === id), 1);
  f.browser.tabs.onRemoved.emit(id, { windowId: win });
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(f.tabs.length, 1);
  assert.equal(f.tabValues.get(999)[TAB_KEY], undefined);
  assert.deepEqual(logs, []);
});

test('toolbar badge uses scoped identity, never page URL or title', async () => {
  const f = fixture(); const windowId = await setup(f); const id = await f.ensure(spec('jira'));
  const other = await f.browser.tabs.create({ windowId, url: SECRET, active: false, pinned: false });
  const query = f.browser.tabs.query;
  f.browser.tabs.query = async args => (await query(args)).map(tab => {
    for (const key of ['url', 'title', 'favIconUrl']) Object.defineProperty(tab, key, {
      get() { throw new Error('Browsing field accessed by badge'); },
    });
    return tab;
  });
  const before = structuredClone(f.mutations);
  await createTabBadges(f.browser, WINDOW_KEY, TAB_KEY)();
  assert.equal(f.badges.get(id).setBadgeText.text, 'L');
  assert.equal(f.badges.get(id).setTitle.title, 'Managed by Layouter — jira');
  assert.equal(f.badges.get(other.id).setBadgeText.text, '');
  assert.equal(f.badges.get(other.id).setTitle.title, 'Layouter — unmanaged tab');
  assert.deepEqual(f.mutations, before);
  assert.ok(!JSON.stringify([...f.badges]).includes(SECRET));
});

test('toolbar clears moved-tab and duplicate badges, and marks ambiguous windows', async () => {
  const f = fixture(); const windowId = await setup(f); const id = await f.ensure(spec('jira'));
  const refresh = createTabBadges(f.browser, WINDOW_KEY, TAB_KEY);
  await refresh();
  f.tabs.push({ ...f.tabs[0], id: 999 }); f.tabValues.set(999, structuredClone(f.tabValues.get(id)));
  await f.maintain(); await refresh();
  assert.equal(f.badges.get(id).setBadgeText.text, 'L');
  assert.equal(f.badges.get(999).setBadgeText.text, '');
  f.windows.push({ id: 888 }); f.windowValues.set(888, { [WINDOW_KEY]: 'work' });
  await refresh();
  assert.equal(f.badges.get(id).setBadgeText.text, '!');
  f.windowValues.set(888, { [WINDOW_KEY]: 'other' });
  f.tabs[0].windowId = 888;
  await refresh(888);
  assert.equal(f.badges.get(id).setBadgeText.text, '');
  assert.equal(f.tabValues.get(id)[TAB_KEY].window, 'work');
});

test('background refreshes toolbar after creation, navigation, activation and sync replacement', async () => {
  const vm = require('node:vm'), fs = require('node:fs');
  const f = fixture(); const windowId = await setup(f); f.storage.instanceId = 'a'.repeat(32);
  let onMessage;
  const responses = [];
  f.browser.runtime = { connectNative: () => ({
    onMessage: { addListener: fn => { onMessage = fn; } }, onDisconnect: { addListener() {} },
    postMessage: response => { assertPrivateResponse(response); responses.push(response); },
  }) };
  vm.runInNewContext(fs.readFileSync(require.resolve('../background.js'), 'utf8'), {
    browser: f.browser, createProtocol, createTabBadges, WINDOW_KEY, TAB_KEY, FIREFOX_PROTOCOL_VERSION, assertPrivateResponse,
    console: { error: assert.fail }, setTimeout() {},
  });
  const flush = () => new Promise(resolve => setImmediate(resolve));
  await flush();
  onMessage({ id: 'ensure', version: FIREFOX_PROTOCOL_VERSION, op: 'ensure-tabs', windowElementId: 'work', tabs: [spec('jira')] });
  await flush();
  const id = f.tabs[0].id;
  assert.equal(f.badges.get(id).setBadgeText.text, 'L');
  // Firefox clears tab-specific action state on navigation, including history changes.
  f.badges.delete(id); f.tabs[0].url = SECRET;
  f.browser.tabs.onUpdated.emit(id, { url: SECRET }, f.tabs[0]);
  await flush();
  assert.equal(f.badges.get(id).setBadgeText.text, 'L');
  f.badges.delete(id);
  f.browser.tabs.onActivated.emit({ tabId: id, windowId });
  await flush();
  assert.equal(f.badges.get(id).setBadgeText.text, 'L');
  onMessage({ id: 'sync', version: FIREFOX_PROTOCOL_VERSION, op: 'sync-tabs', windowElementId: 'work', tabs: [spec('jira')] });
  await flush();
  assert.equal(f.badges.get(id).setBadgeText.text, '');
  assert.equal(f.badges.get(f.tabs.at(-1).id).setBadgeText.text, 'L');
  assert.equal(f.tabs[0].url, SECRET);
  assert.ok(!JSON.stringify(responses).includes(SECRET));
});

test('toolbar permission surface stays unchanged and private tabs are ignored', async () => {
  const manifest = require('../manifest.json');
  assert.equal(manifest.browser_action.default_area, 'navbar');
  assert.equal(manifest.browser_action.default_icon, 'icon.svg');
  assert.equal(manifest.content_scripts, undefined);
  assert.equal(manifest.host_permissions, undefined);
  const f = fixture(); await setup(f); await f.ensure(spec('jira'));
  f.tabs[0].incognito = true;
  await createTabBadges(f.browser, WINDOW_KEY, TAB_KEY)();
  assert.equal(f.badges.size, 0);
});
