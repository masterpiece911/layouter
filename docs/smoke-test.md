# Live desktop smoke check

Run this from the extracted source directory with Python 3.11+ and kitty. This is
a manual integration check, not a claimed test result.

For i3, use the nested-layout fixture:

```sh
python3 scripts/build_zipapp.py
dist/layouter -f examples/smoke.toml --check smoke
dist/layouter -f examples/smoke.toml --dry-run smoke
dist/layouter -f examples/smoke.toml smoke
```

Expected: workspace `99: Layouter smoke`, one nested container, one kitty OS
window, one tab, and two shell panes. Initial focus ends in pane One.

For Sway, use its nested-layout fixture through the same placement path:

```sh
python3 scripts/build_zipapp.py
dist/layouter -f examples/sway-smoke.toml --check sway-smoke
dist/layouter -f examples/sway-smoke.toml --dry-run sway-smoke
dist/layouter -f examples/sway-smoke.toml sway-smoke
```

Expected: workspace `98: Layouter Sway smoke`, one nested container, one native
kitty view, one tab, and two panes. Initial focus ends in pane One.

Then exercise create-only reconciliation:

1. Run the launch command again. No window or pane should be created.
2. Change the tab title and layout manually; type a command in pane One. Run it
   again. The renamed tab, chosen layout, and shell process remain intact.
3. Add an unmanaged pane, then close only pane Two. Run again. Only Two should be
   recreated; One and the unmanaged pane survive.
4. Move kitty to another workspace. Run again. Kitty stays where you moved it.
5. Detach One to another kitty OS window in the same process. It remains present.
6. Run with `--no-focus` from another window. Final focus returns there.
7. Inspect the compositor tree and confirm Layouter marks the managed workspace
   and kitty view without removing user marks.

Then exercise explicit synchronization:

1. Move the managed kitty OS window to another workspace and change its
   compositor layout.
2. Detach pane Two into a different kitty tab and rename/change the original
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
