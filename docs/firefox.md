# Firefox companion integration

Layouter supports a Firefox-family browser when its companion, native messaging,
`browser.sessions`, `browser.windows`, and `browser.tabs` APIs support protocol 2.
It does not inspect the executable name to decide compatibility. Private browsing
is unsupported. Browser history, containers, user settings and undeclared tabs
belong to the browser. All visible changes require an explicit Layouter invocation.

## Installation

Install Layouter, then register its native messaging host and install the
companion in each browser instance/profile you intend to use. The wheel supplies
`layouter-firefox-host`; the Debian package installs the host and a manifest in
`/usr/lib/mozilla/native-messaging-hosts/org.layouter.firefox.json`.

For a zipapp or a user-local installation, generate a wrapper and manifest in a
persistent location (choose the path to your installed executable):

```sh
python3 scripts/configure_firefox_host.py \
  --layouter "$HOME/.local/bin/layouter" \
  --output "$HOME/.local/lib/layouter/firefox"
mkdir -p "$HOME/.mozilla/native-messaging-hosts"
cp "$HOME/.local/lib/layouter/firefox/org.layouter.firefox.json" \
  "$HOME/.mozilla/native-messaging-hosts/"
```

Use the browser's documented native-host search directory if different. No
profile is read or configured by Layouter. The JSON permits only extension ID
`firefox@layouter.dev`. The wrapper executes the installed Layouter's private
`--firefox-host` entry point; it does not evaluate a workflow. Keep the wrapper
and executable at the absolute paths recorded in the generated files.

For development, load `firefox-extension/manifest.json` through the browser's
`about:debugging` temporary add-on loader. Build an unsigned archive with:

```sh
python3 scripts/build_firefox_extension.py
```

`dist/layouter-firefox-1.0.0-unsigned.xpi` is an unsigned development/signing
input, **not a production-installable signed release**. Production Firefox
installation requires Mozilla signing with the maintainer's add-on account.
No signing credentials or signed artifact are supplied by this repository's
build. Tagged CI releases can produce `layouter-firefox-VERSION.xpi` through
Mozilla signing after the maintainer configures the
[AMO Actions secrets](packaging.md#mozilla-signing-for-tagged-releases). Install
that signed XPI through **about:addons → gear → Install Add-on From File**.
Submit the archive for signing without changing its extension ID; changing
that ID loses access to existing extension session values. Follow the browser's
extension distribution rules for compatible forks. Installation compatibility
and the live smoke scenario must be verified before claiming support for a fork.

## Managed-tab toolbar badge

The companion adds a Layouter button to the browser toolbar. A green **L** badge
means the selected tab carries a Layouter identity in its corresponding managed
window. Hover over the button to see the declared tab identity. Unmanaged tabs
have no badge; an amber **!** reports conflicting window identities.

The indicator follows tab selection, navigation, duplication, moves and browser
restoration. A duplicate that becomes unmanaged loses its badge. Moving a managed
tab outside its parent window clears its badge without altering the moved tab's
metadata or contents. Sync replacement clears the old tab's badge and marks the
new managed tab. These are passive status updates, not desktop reconciliation.

The badge reflects persistent identity metadata; it does not evaluate workflow
files or claim that an old declaration still appears in your current source.
No page titles/favicons are modified, no content scripts or site permissions are
added, and no browsing data is transmitted. If Firefox puts the button in the
Extensions menu after an update, use **Pin to Toolbar** to keep it visible.

## Privacy boundary

**Browser-derived browsing data never crosses the Firefox companion boundary.**
The companion inspects browser tabs locally to reconcile user-authored declarations.
Current URLs, titles, domains, favicons, page contents and browsing history are not
transmitted to the Layouter native host, including in errors or logs. Neither URL
hashes nor URL-match booleans are exposed. Layouter sends declared URLs into the
extension; it does not receive the URLs of pages you are browsing.

The `tabs` API permission allows the companion to inspect `Tab.url` locally for
`--sync`. API access is separate from transmission: the manifest intentionally
sets `data_collection_permissions.required` to `["none"]`. This is an architectural
property, not just a signing setting. See Mozilla's
[data consent documentation](https://extensionworkshop.com/documentation/develop/firefox-builtin-data-consent/).
The manifest retains its Gecko 140+ minimum.

Structural inspection exposes only ownership, runtime window IDs, and scoped
Layouter tab identities/runtime IDs. A URL used as an unnamed tab's identity is
workflow-authored metadata, never the live URL. The companion keeps internal
WebExtension tab objects separate from explicit wire records. A recursive outgoing
message guard rejects browsing-data fields; browser API exceptions and transport
logs use generic diagnostics rather than forwarding exception text.

For add-on review: the extension uses the Tabs API to implement Layouter declarations
locally. Native Messaging exchanges declarations and integration metadata with the
local application; it does not transmit current browser URLs, titles, page contents,
history or other browsing activity through that channel.

## Runtime and protocol

The extension persists a random 128-bit instance ID and a set of owned window
keys in `storage.local`. It stores a window key in `sessions.setWindowValue`
and `{window, tab}` identity records in `sessions.setTabValue`. It never stores
URLs, history or session snapshots in its ownership registry. A cloned profile
with copied ownership can produce an ambiguity, which fails without mutation.

A browser-launched native host exposes a mode-600 Unix socket in the owned,
mode-700 `$XDG_RUNTIME_DIR/layouter/firefox/` directory. It relays bounded JSON
requests and responses, and exits/removes its socket when the browser connection
ends. An instance lock prevents competing hosts from replacing a live socket.
The extension may reconnect the transport; reconnection never reconciles state.
A stale/inaccessible endpoint is an error, not proof that a browser is absent.
Restart the companion to replace a stale endpoint under its instance lock.

Native messaging uses its standard native-endian 32-bit byte length prefix.
Local sockets use one newline-delimited JSON request/response per connection.
Every envelope has `id` and `version: 2`; responses carry `ok` and `result` or
`code` and `message`. Any version mismatch fails, without negotiation. Update
Layouter and reload the companion together: version 1's URL-bearing protocol is
not accepted. Stored window/tab keys and ownership are unchanged by the upgrade.
Read operations are `hello`, `inspect`, and `find-bootstrap`. Invocation operations
are `claim-window`, `create-window`, `ensure-tabs`, `sync-tabs`, `probe-window`,
`clear-probe`, and `remove-bootstrap`. Bootstrap responses contain only matching
runtime tab IDs, never surrounding browser state.

`ensure-tabs` and `sync-tabs` take the full desired declarations and return a null
result on success. Ordinary ensure checks identity and creates only missing tabs.
Sync compares URLs locally, removes only the old tab's identity on divergence,
creates a replacement, then restores pinned state, ordering and activation. Declared
tabs are placed first in declaration order within each pinned/unpinned section;
unmanaged tabs remain present after them in each section. The
old tab stays open. Sensitive effect verification remains inside the companion.
Creation succeeds when Firefox accepts the declared URL; sync does not wait for
page loading or require the final destination to equal it. Sites may redirect.
A later `--sync` still compares the then-current URL literally, so use a site's
final URL in declarations when you want to avoid repeated replacements.
New windows already receive the declared tabs with creation-time pinning, order
and activation. They do not receive a second tab synchronization pass in the
same invocation, even with `--sync`.
Tab mutation validates current parent identity before acting. Errors preserve
partial progress. Python retains invocation policy, ownership selection, launch
routing, window recovery, compositor placement and marks.

Unowned identities route through the declaration's executable and argv plus an
unguessable loopback HTTP URL. The one companion seeing that exact URL claims
the identity. The invocation closes only that bootstrap tab. Ownership remains
separate from presence: presence always requires a live window carrying the
key. If a browser was stopped, bootstrap can start it and let its own restoration
recover the window. When recovery is incomplete or ambiguous, preserve the
browser state and invoke again after its normal restoration completes.

An unmarked managed window is correlated with a temporary random titlePreface.
Layouter subscribes to compositor events first, reads a fresh tree, requires one
match, then clears the probe in a finally block. Recovery adds the mark without
moving the window. A marked window still requires a connected companion and live
browser identity, but needs no probe. Some platforms do not expose titlePreface
for untitled/about:blank windows; correlation then fails safely. Test empty-window
recovery with the selected browser's default new-tab page.

## Duplicated tabs and passive metadata maintenance

When Firefox copies a managed tab's session identity during duplication, the
companion retains the original tracked tab and removes only the duplicate's
Layouter tab value. Both tabs stay open; neither tab is moved, navigated, pinned,
or activated by this maintenance. The duplicate becomes unmanaged immediately,
without needing a Layouter invocation. Closing a tab still never recreates it.

The companion uses its in-memory observation of the tracked runtime tab ID. If
multiple copies are already present on startup and no prior observation exists,
the lowest runtime tab ID wins. This deterministic fallback does not depend on
URLs, titles, positions, or browser brand. Tab creation/restoration events refresh
metadata, and structural inspection also maintains copied identities, including
copies that predate the extension update. Only identities inside their declared
parent window participate; moved tabs outside that parent remain untouched.

This is passive maintenance of Layouter-owned metadata, permitted by the
no-autonomous-reconciliation rule. User-visible reconciliation still requires an
invocation. Dry-run does not cause visible changes, but does not suspend passive
metadata maintenance. A duplicate **window** identity remains `AmbiguousState`:
no window is chosen and no tab metadata in conflicting windows is stripped.

## Verification

Run `make test-firefox` for mock WebExtension tests and `make test` for Python
model, recovery, routing, compositor and native transport tests. The live
[i3/Sway procedure](smoke-test.md#firefox-family-companion) is a separate required
gate, including normal browser restoration, empty windows, and a compatible fork.

Platform references:
[sessions persistence](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/sessions),
[titlePreface](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/windows/update),
[tab metadata and permissions](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/API/tabs),
[native messaging](https://developer.mozilla.org/en-US/docs/Mozilla/Add-ons/WebExtensions/Native_messaging),
[session-value duplication test discussion](https://bugzilla.mozilla.org/show_bug.cgi?id=1322060).

## Recorded development verification (2026-09-30)

The final release checks passed 207 Python tests, 31 companion tests, and 6 React
tests plus TypeScript checks, without skips. Relocated zipapps, an offline wheel
installation, the Debian payload and the unsigned extension archive passed
artifact verification.

Firefox Developer Edition 158.0b1 on Sway 1.11 also passed the four-window
close/recreate regression using the manual workflow's real declarations. A fresh
`--sync` produced exactly 3, 3, 2 and 2 tabs, with declared pinning, ordering and
activation. An ordinary repeat created no additional tabs. Separate live checks
covered delayed loading and redirects without treating them as creation failures.
These checks complement the earlier lifecycle and privacy checks below.

Firefox 156.0.1 on Sway 1.11 (XWayland windows), in a disposable profile with
private runtime/native-host directories and a temporarily loaded companion:

- Passed bootstrap routing through opaque executable/argv, two-window creation,
  compositor placement, mark recovery through title probes and ordinary idempotency.
- Passed preservation of unmanaged tabs and existing navigation/pinning, moved-tab
  replacement in the declared parent, and non-destructive URL synchronization with
  pinning, highest valid ordering and declared activation.
- Passed owned closed-window recreation with only declared tabs and empty-window
  title correlation.
- Passed Firefox's normal Quit followed by startup session restoration and
  temporary companion reloading: both live identities and restored unmanaged tabs
  survived, and Layouter recovered compositor marks without replacing windows.
- The initial implementation confirmed copied duplicate-tab metadata and rejected
  ambiguity. This behavior has since been replaced by passive metadata maintenance
  as described above.

The test driver used a test-only browser-API hook in a temporary extension copy
to simulate user actions. The distributed extension has no evaluation hook or
unsafe-eval CSP. A process-termination experiment did not preserve the latest
unflushed session; the successful restart check used Firefox's normal Quit.
The automated suites also cover private-window rejection, protocol mismatch,
multiple owners, scoped duplicates, socket framing and capture preservation.

Not verified live: i3, native Wayland Firefox windows, and a signed permanent
extension installation. Zen verification is recorded below; it did not pass. The production signing gate
remains open. The manual smoke checklist includes those remaining platform checks.


## Protocol 2 privacy verification (2026-09-30)

After the privacy refactor, the isolated Firefox 156.0.1/Sway 1.11 XWayland
scenario passed again: two-window bootstrap and placement, ordinary preservation,
mark recovery, closed-tab/window recreation, moved-tab containment, local URL
replacement, pinned/unpinned ordering, active state, normal browser restart with
unmanaged-tab preservation, duplicate ambiguity, and empty windows.

The temporary test driver kept browsing-state assertions and restart snapshots
inside the extension. Only structural IDs and generic results crossed native
messaging; the production outgoing-response guard remained active. No test hook
is included in the distributed extension. The automated suite passed 206 Python,
6 React and 21 companion tests without skips, including the sentinel native-host
transport/log test. Release artifact verification also passed. The unverified
platform/signing gates listed above remain open; this run did not retest Zen.

The later leftmost-ordering change was verified with 22 companion tests and
206 Python tests, plus another isolated Firefox/Sway smoke run. Declared tabs
occupied the first positions of each pinned/unpinned section; unmanaged tabs
survived and normal restart recovery still passed.

The passive duplicate-tab metadata change passed 26 companion tests, 206 Python
tests and the Firefox 156.0.1 and Developer Edition 158.0b1/Sway smoke scenarios.
A real duplicate lost only its
session identity without a Layouter invocation; the original remained tracked
and both tabs stayed open. Normal session recovery and synchronization still passed.

The toolbar badge passed 30 companion tests and the Developer Edition
158.0b1/Sway smoke scenario, including navigation, moves, URL replacement,
duplication and restart. Release validation passed 206 Python and 6 React tests,
plus companion asset checks. Native messages remained free of browsing data.

## Zen verification (2026-09-30)

Zen 1.22.3b (Gecko 156.0.1), installed from Flathub, was tested on Sway 1.11
using disposable profiles, isolated native-host files and the temporary companion.
**This version did not pass the multi-window scenario.** No Zen-specific behavior
was added to Layouter, and the user's normal profile/settings were not changed.

Running the installed Zen executable directly, outside Flatpak's sandbox:

- Companion installation, native messaging and bootstrap routing succeeded.
- With default settings, creating the first managed window failed with
  `New FirefoxWindow initial tab is ambiguous; preserving it`. The new browser
  window exposed multiple initial tabs because Zen mirrors windows.
- With `zen.window-sync.enabled=false` only in a second disposable profile, the
  first managed window and its declared tabs were created. The second window
  still included copies of pinned tabs from the first and failed the same guard.
- The guard left created windows/tabs available; it did not choose a tab, strip
  its metadata or close browser state. A companion regression test covers this
  multiple-initial-tab failure. Restart recovery, moved-tab handling and `--sync`
  could not be validated end to end after this creation failure.

Zen documents its [window mirroring and blank-window behavior](https://docs.zen-browser.app/user-manual/window-sync).
Blank windows are documented as temporary and not restored across restart, so
that is not a verified workaround for Layouter's persistent-window contract.

The actual Flatpak launch was checked separately with access granted only to the
temporary test directory. Zen launched and loaded the companion, but no native
host connection appeared before the test deadline. Native-host access through
that sandbox remains unverified; the successful direct-executable connection
does not establish Flatpak integration support. No persistent Flatpak overrides
were installed. These results establish a current compatibility limitation,
not a passing Zen support claim.
