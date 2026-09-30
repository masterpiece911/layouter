"use strict";
(async () => {
  let { instanceId } = await browser.storage.local.get("instanceId");
  if (!instanceId) {
    instanceId = [...crypto.getRandomValues(new Uint8Array(16))].map(v => v.toString(16).padStart(2, "0")).join("");
    await browser.storage.local.set({ instanceId });
  }
  const handle = createProtocol(browser);
  const refreshBadges = createTabBadges(browser, WINDOW_KEY, TAB_KEY);
  async function refresh(windowId) {
    try { await refreshBadges(windowId); }
    catch { console.error("Layouter toolbar indicators could not be refreshed"); }
  }
  // Share one queue across connections and metadata events. No lifecycle event
  // creates, moves, navigates, pins, activates or closes a tab/window.
  let queue = Promise.resolve();
  function maintain(windowId) {
    queue = queue.then(async () => {
      try { await handle.maintainTabIdentities(windowId); }
      catch { console.error("Layouter tab identity metadata could not be updated"); }
      await refresh(windowId);
    });
  }
  browser.tabs.onCreated.addListener(tab => maintain(tab.windowId));
  browser.tabs.onUpdated.addListener((id, changes, tab) => {
    if (changes.status !== undefined || changes.discarded !== undefined || changes.url !== undefined) maintain(tab.windowId);
  });
  browser.tabs.onAttached.addListener((id, info) => maintain(info.newWindowId));
  browser.tabs.onRemoved.addListener((id, info) => maintain(info.windowId));
  browser.tabs.onActivated.addListener(info => maintain(info.windowId));
  maintain();
  function connect() {
    const port = browser.runtime.connectNative("org.layouter.firefox");
    // Serialize requests with passive identity maintenance.
    port.onMessage.addListener(request => {
      queue = queue.then(async () => {
        try {
          const response = await handle(request);
          // Identity changes during creation/sync must update both the old and new
          // tab badges before reporting completion. Presentation failure is benign.
          await refresh();
          assertPrivateResponse(response);
          port.postMessage(response);
        } catch {
          console.error("Layouter native connection could not complete a request");
        }
      });
    });
    port.onDisconnect.addListener(() => {
      if (port.error) console.error("Layouter native connection disconnected");
      // Reconnection only exposes state. It never reconciles browser state.
      setTimeout(connect, 3000);
    });
    const registration = { op: "register", version: FIREFOX_PROTOCOL_VERSION, instanceId };
    assertPrivateResponse(registration);
    port.postMessage(registration);
  }
  connect();
})().catch(() => console.error("Layouter companion could not start"));
