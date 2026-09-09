# Validation record

Date: 2026-09-09. Runtime: Python 3.12.13 on Linux.

## Executed

`PYTHONPATH=src python3 -m unittest discover -s tests -v`

**70 tests collected: 65 passed, 5 skipped, 0 failures.**

The skip reason is this execution environment's prohibition on creating Unix
sockets (`PermissionError: Operation not permitted`). Those fixtures remain in
the suite and run automatically where Unix sockets are permitted.

Coverage includes:

- The authoritative complete workflow parses verbatim, including `focus =
  "code"`, title-only kitty panes, two kitty OS windows, argument interpolation,
  workspace numbers, `class`, and `size`.
- Exclusive file selection: local first, same-named global fallback,
  global-only and explicit-file modes, plus `.dev/<workflow>.toml` discovery,
  disabling, cwd/env cascading, unknown fields, argument errors, escaping, and
  stable hashes.
- Local and global declarations are never combined; tests verify local
  shadowing, global fallback after local removal, and `--file`/`--global`
  mutual exclusion.
- CLI checking/listing without desktop access and workflow argument parsing.
- The requested partial-state case: only a missing tests pane and Firefox are
  created; a second invocation creates nothing and changes no live state.
- Generic event-based GUI discovery without application-specific rules,
  moved/floating windows, renamed tabs, user-changed layouts, detached panes,
  unmanaged panes, adoption ambiguity, opaque kitty sessions, failed inspection,
  and focus suppression.
- i3-compatible binary framing, event acknowledgement ordering, timeout
  boundaries, and Sway-native `app_id` matching.
- A shared i3/Sway live-window assembler: nested split construction, recursive
  layout, initial percentages, recovery of a marked empty workspace, and the
  deliberately virtual single-child-container case.
- The mutation boundary: entirely new groups are assembled, a new child can be
  attached to an existing marked parent, and an existing descendant is never
  reparented to reconstruct missing nesting. The preserved difference is
  reported to the user.
- Explicit sync on the shared i3/Sway path: moved existing windows are staged
  and rebuilt into declared nested structure, managed workspace names, layouts,
  order, tiled state, and ratios are corrected, unmanaged windows survive, and
  a converged second sync performs no compositor mutation.
- Kitty sync planning and correction: detached declared panes are reunited in
  order, tab title/layout are restored, unmanaged panes survive, raw sessions
  remain opaque, and converged kitty state is a no-op.
- `--dry-run --sync` correction reporting without mutation, end-to-end
  reconciler dispatch to the corrective backends, and CLI forwarding of
  `--sync` into reconciliation.
- Kitty socket targeting, duplicate identity detection, individual pane creation,
  `LAYOUTER_*` pane metadata, the `session` escape hatch, new-tab-only layout
  application, safe startup encoding, and no forced XWayland option.
- Private runtime permissions, advisory locks, and standalone executable exit
  behavior.

Also executed: Python byte-compilation, the standalone zipapp build, `--version`,
all shipped example `--check` commands, and source/built-executable checks of the
readable `debug checkout` workflow.

## Not executed

Five real Unix-socket fixtures: fragmented framing, subscription acknowledgement
ordering, malformed framing/EOF, dead-socket discovery, and invalid kitty
protocol state.

Neither i3, Sway, nor kitty is installed or running here. Actual application
launch, live nested-tree assembly, Sway native-view placement, live kitty
pane/tab creation or movement, compositor matching, sync staging, sizing, and
focus behavior have not been validated against a real desktop. Simulated tests
establish internal behavior, not real-desktop compatibility.

Follow `smoke-test.md` on each target compositor before relying on the launcher
for a daily workspace. Both backends use the same nested construction and
initial-sizing path. A declared single-child container may be flattened, and
ordinary reconciliation does not move existing elements to reconstruct it;
`--sync` can rebuild it once it has multiple children.
