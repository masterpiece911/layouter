# Live desktop smoke check

Run these checks from a source checkout or extracted source archive inside an
i3 or Sway graphical session with Python 3.11+ and kitty on `PATH`.
Build the TOML-only executable used by the fixtures:

```sh
python3 scripts/build_zipapp.py --core
```

For i3, use the nested-layout fixture:

```sh
dist/layouter-core -f examples/smoke.toml --check smoke
dist/layouter-core -f examples/smoke.toml --dry-run smoke
dist/layouter-core -f examples/smoke.toml smoke
```

Expected: workspace number `99` (preserving its existing name, if any), one
kitty OS window, one tab, and two shell panes. The declared single-child
container may be flattened. Initial focus ends in pane `one`.

For Sway, use its nested-layout fixture through the same placement path:

```sh
dist/layouter-core -f examples/sway-smoke.toml --check sway-smoke
dist/layouter-core -f examples/sway-smoke.toml --dry-run sway-smoke
dist/layouter-core -f examples/sway-smoke.toml sway-smoke
```

Expected: workspace number `98` (preserving its existing name, if any), one
native kitty view, one tab, and two panes. The declared single-child container
may be flattened. Initial focus ends in pane `one`.

Then exercise create-only reconciliation:

1. Run the launch command again. No window or pane should be created.
2. Change the tab title and layout manually; type a command in pane `one`. Run it
   again. The renamed tab, chosen layout, and shell process remain intact.
3. Add an unmanaged pane, then close only pane `two`. Run again. Only `two` should be
   recreated; `one` and the unmanaged pane survive.
4. Move kitty to another workspace. Run again. Kitty stays where you moved it.
5. Detach `one` to another kitty OS window in the same process. It remains present.
6. Run with `--no-focus` from another window. Final focus returns there.
7. Inspect the compositor tree and confirm Layouter marks the managed kitty
   view without removing user marks. Workspaces are destinations and are not
   marked or renamed by Layouter.

Then exercise explicit synchronization:

1. Move the managed kitty OS window to another workspace and change its
   compositor layout.
2. Detach pane `two` into a different kitty tab and rename/change the original
   tab layout.
3. Run the fixture with `--dry-run --sync`; it should report compositor and
   kitty corrections without changing the desktop.
4. Run it with `--sync`. The managed window returns to the declared tree and
   the panes return to one declared tab with its declared title/layout.
5. Add an unmanaged window and pane, disturb the managed layout again, and
   repeat `--sync`. The unmanaged elements must remain present.
6. Run `--sync` once more. It should report no further `sync` actions.
7. Close the smoke terminal windows manually when finished.

For the GUI path, edit `examples/debug.toml` for real project commands. Verify
class or app-ID values against the live tree. Close only Firefox and the tests
pane, invoke again, and verify that those two elements alone are recreated. Also
test a slow-starting application; event subscription should discover it without
a sleep.

Diagnostic commands:

```sh
i3 --version               # or: sway --version
i3-msg -t get_tree         # or: swaymsg -t get_tree
kitty --version
```

Runtime log filenames are stable element IDs followed by `.log`. If a launch
times out, inspect those logs and the compositor tree before retrying. Do not
delete a live kitty socket to test failure; use the automated failure fixtures.

## Firefox-family companion

Run this gate under both i3 and Sway with the [host and companion](firefox.md)
installed. Use `examples/firefox.tsx` or `examples/firefox.toml` consistently;
file paths participate in session identity. Both declare `docs` on workspace 3
with a pinned MDN tab and `project` on workspace 4 with an active named repo tab,
using `executable="firefox"` and `args=["-P", "Work"]`. Choose browser CLI arguments
for a real test profile; repeat with the same extension in a compatible fork
such as Zen when available. No executable-name compatibility branches exist.

1. Invoke Layouter. Verify both windows are created and placed, while preexisting
   unrelated browser windows are untouched.
2. Add arbitrary unmanaged tabs to both windows. Navigate the managed tabs away
   from their creation URLs; change pinning, order, activation and placement.
3. Invoke normally. Verify no existing state is corrected, and another invocation
   performs no title probe or placement changes.
4. Quit the browser completely. Restart it using its normal session restoration,
   or invoke Layouter to route its launch. Allow restoration to finish, then
   invoke. Verify window identity recovery, restored compositor marks, preserved
   placement and all Firefox-restored unmanaged tabs.
5. Close one managed tab. Wait without invoking: nothing should recreate it.
   Invoke and verify only that missing declaration is recreated.
6. Move a managed tab to the other managed window, then invoke. Verify the moved
   tab stays there and a replacement appears in its declared parent. Repeat with
   an unmanaged destination window.
7. Run `--sync`. Verify each mismatched URL becomes a preserved old tab plus a new
   declared tab. Verify pinning, declaration order at the leftmost valid positions
   of each pinned/unpinned region, and the declared active tab. Use additional
   declared tabs to exercise both regions. Unmanaged tabs all remain open.
8. Close an entire managed window, wait, then invoke. Verify a fresh window with
   only declared tabs, without
   history lookup or restoration of undeclared historical tabs. Run again and
   verify idempotency.
   Repeat closure followed by `--sync`, including a declared page that redirects
   or loads slowly. Each new window must contain exactly its declared tabs, with
   correct pinning, declaration order and activation. No newly created tab should
   become an unmanaged copy during that same invocation.
9. Add an empty `FirefoxWindow`, create it, add unmanaged tabs, restart normally
   and verify their restoration. Close it and invoke: a fresh default browser
   window appears, without restoring undeclared contents.
10. Restore an old closed managed window after Layouter has replaced it. Verify
    duplicate window identity fails with `AmbiguousState` and preserves both.
    Duplicate a managed tab manually: the original stays tracked and the duplicate
    becomes unmanaged without an invocation; both remain open. Check that no pin,
    order, activation, or URL state is corrected by this metadata maintenance.
    Check close/restore of a tab preserves identity when the
    browser restores its session values.
11. Run `--dry-run` against missing windows, missing tabs, and URL differences.
    Verify no launch, bootstrap, probe, mark, pin, activation or visible browser mutation.
12. With multiple companion profiles connected, change args and then executable.
    Verify a new logical window routes only through the selected command, leaving
    the old managed window and unrelated windows untouched. Return to the old
    launch declaration and verify its original identity is recovered.

Record browser/version, compositor, extension build, signing mode and results.
An automated mock suite alone does not establish live browser compatibility.

For companion protocol 2, also verify the privacy boundary: inspection returns
only ownership, window IDs and scoped tab identities/runtime IDs. Native responses
must never include live URLs, titles, pin/order/active snapshots or URL-match
booleans. Keep browser-state assertions inside the extension when using a test
driver. Check API failures and native disconnect logs as well as successful sync.
Upgrade Layouter and reload the companion together; protocol 1 must fail clearly.

Toolbar indicator: pin the Layouter button if necessary. Select a declared tab
and check its green L badge/identity tooltip, then select an unmanaged tab and
check the badge clears. Navigate a managed tab and verify its badge returns.
Duplicate it, move it out of its parent, and use URL-replacing sync: only the tabs
with valid scoped identity should retain the badge. Restart and verify badges
recover. Conflicting managed window identities should show an amber !.
