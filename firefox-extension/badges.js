"use strict";

// Local presentation of integration metadata. No page data is read or transmitted,
// and badge refresh never changes tabs, window identities or tab identities.
function createTabBadges(browser, windowKey, tabKey) {
  return async function refresh(windowId) {
    const keys = new Map(), counts = new Map();
    for (const win of await browser.windows.getAll({ windowTypes: ["normal"] })) {
      if (win.incognito) continue;
      const key = await browser.sessions.getWindowValue(win.id, windowKey);
      if (typeof key !== "string" || !key) continue;
      keys.set(win.id, key);
      counts.set(key, (counts.get(key) || 0) + 1);
    }
    for (const tab of await browser.tabs.query(windowId === undefined ? {} : { windowId })) {
      if (tab.incognito) continue;
      try {
        const key = keys.get(tab.windowId);
        const identity = await browser.sessions.getTabValue(tab.id, tabKey);
        const managed = key && identity?.window === key && typeof identity.tab === "string";
        const ambiguous = managed && counts.get(key) > 1;
        const text = ambiguous ? "!" : managed ? "L" : "";
        const title = ambiguous ? "Layouter — conflicting window identities" :
          managed ? `Managed by Layouter — ${identity.tab}` : "Layouter — unmanaged tab";
        await browser.browserAction.setBadgeBackgroundColor({ tabId: tab.id, color: ambiguous ? "#92400e" : "#166534" });
        await browser.browserAction.setBadgeTextColor({ tabId: tab.id, color: "#ffffff" });
        await browser.browserAction.setTitle({ tabId: tab.id, title });
        await browser.browserAction.setBadgeText({ tabId: tab.id, text });
      } catch {
        // A tab may close between the snapshot and the action update. Do not log
        // browser exception text, which can contain browsing information.
        console.error("Layouter toolbar indicator could not be updated");
      }
    }
  };
}
if (typeof module !== "undefined") module.exports = { createTabBadges };
