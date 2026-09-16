# Validation record

Date: 2026-09-16. Runtime: Python 3.14.4 on Linux.

## Reproduce the run

From the source checkout, use Python 3.11 or newer. No third-party Python test
packages or running desktop are required; the socket fixtures need permission
to bind local Unix sockets.

```sh
make test
make check
```

Select a different interpreter with `make test PYTHON=python3.12`. To run one
test module directly:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_backends.py' -v
```

The packaging test rebuilds `dist/layouter` as part of the suite.

## Executed

`make test` (`PYTHONPATH=src python3 -m unittest discover -s tests -v`).

**81 tests run: 81 passed, 0 skipped, 0 failures, 0 errors.**

The initial sandboxed run passed 76 tests and reported 5 errors because Unix
socket binding was prohibited (`PermissionError: Operation not permitted`).
These are errors, not automatic skips. The complete suite then passed outside
the sandbox with local Unix sockets permitted, including all five socket
fixtures: fragmented framing, subscription acknowledgement ordering, malformed
framing/EOF, dead-socket discovery, and invalid kitty protocol state.

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
- In-memory transport checks for fragmented Unicode payloads, EOF, oversized
  frames, mismatched replies, protocol wire values, and subscription ordering.
- Numbered workspace lookup preserves existing names and user renames, rejects
  ambiguous numbers, activates missing destinations, and never marks workspaces.
- A shared i3/Sway live-window assembler: nested split construction, recursive
  layout, initial percentages, recovery of a marked empty workspace, and the
  deliberately virtual single-child-container case.
- The mutation boundary: entirely new groups are assembled, a new child can be
  attached to an existing marked parent, and an existing descendant is never
  reparented to reconstruct missing nesting. The preserved difference is
  reported to the user.
- Explicit sync on the shared i3/Sway path: moved existing windows are staged
  and rebuilt into declared nested structure, workspace destinations, layouts,
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
- Bounded kitty startup waits after the compositor window maps, protocol errors
  without retries, and preservation of existing tab layouts during pane creation.
- Private runtime permissions, advisory locks, and standalone executable exit
  behavior.

Also executed: `make check`, which validated `examples/debug.toml` with the
`debug checkout` arguments. The packaging test built the standalone zipapp and
verified its `--version` output and missing-file configuration exit code.

## Not executed

The live desktop smoke check was not run. Sway and kitty executables are
available in this environment; i3 was not found on `PATH`. Actual application
launch, live nested-tree assembly, Sway native-view placement, live kitty
pane/tab creation or movement, compositor matching, sync staging, sizing, and
focus behavior have not been validated against a real desktop. Simulated tests
establish internal behavior, not real-desktop compatibility.

Follow [the smoke check](smoke-test.md) on each target compositor before relying on the launcher
for a daily workspace. Both backends use the same nested construction and
initial-sizing path. A declared single-child container may be flattened, and
ordinary reconciliation does not move existing elements to reconstruct it;
`--sync` can rebuild it once it has multiple children.
