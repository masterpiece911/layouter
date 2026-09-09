# Layouter v0.1 design

Status: minimal usable implementation, Python 3.11+, Linux, i3/Sway, and kitty.

## Contract

Layouter treats configuration as desired desktop state:

> Create, don't correct; preserve, don't prune.

Every invocation discovers live state and creates missing elements. It does not
restart, relocate, retitle, resize, or remove an existing managed element. Extra
unmanaged windows and panes remain untouched. Layout and `size` affect initial
creation only; requested final focus is the sole intentional update to an
existing desktop. These statements describe ordinary invocation; `--sync` is
the explicit corrective mode described below.

There is no daemon, persistent state database, process supervision, or pruning.
Live i3/Sway marks and kitty remote-control state are authoritative.

## CLI and project context

```text
layouter [options] [workflow [workflow-args...]]
layouter -C <directory> [workflow [workflow-args...]]
```

`-C` is applied before configuration discovery, path expansion, default `cwd`,
and session hashing. It selects a single project directory, with relative paths
resolved from the current directory.
Options precede the workflow because all later positional values are workflow
arguments. The default workflow name is `default`.

`--check` parses and prints desired state without desktop I/O. `--dry-run`
inspects the live desktop but creates nothing. `--no-focus` restores the
original compositor focus after creation. `--list`, `--file`, `--global`, and
`--timeout` are also implemented. `--sync` enables corrective reconciliation;
`--dry-run --sync` reports its planned corrections without applying them.

## Configuration resolution

Layouter selects exactly one workflow file:

| Invocation | First choice | Fallback |
|---|---|---|
| `layouter` | `.dev/default.toml` | `~/.config/layouter/default.toml` |
| `layouter debug` | `.dev/debug.toml` | `~/.config/layouter/debug.toml` |

`XDG_CONFIG_HOME` replaces `~/.config` when set. Selection is file-level: when
the local file exists, no values or elements are read from the global file.
When it is absent, Layouter tries the global file with the same workflow name.
There is no fallback from a named workflow to `default.toml`.

`--global` skips local discovery and selects the global file. `--file FILE`
selects only that explicit file, relative to the project unless absolute. These
options are mutually exclusive. `--list` shows the union of local and global
workflow names, with a local filename shadowing the same global filename.

Every TOML file contains exactly one complete workflow at its root. There are
no grouped workflow tables, partial overrides, declaration merging, or explicit
inheritance directives. `enabled = false` simply omits that declared element;
it never removes a live one.

## Readable TOML model

The public identity field is `name` for workspaces, containers, GUI windows,
kitty OS windows, and tabs. Containers may omit `name`; their stable identity is
then derived from their parent and declared position. A pane may use just
`title`, which serves as both its identity and initial visible title:

```toml
[[workspace.kitty.pane]]
title = "tests"
command = ["npm", "run", "test", "--", "--watch"]
```

For an identity independent of presentation, provide both:

```toml
[[workspace.kitty.pane]]
name = "tests"
title = "Tests — watch mode"
```

A workspace or container may recursively contain `window`, `kitty`, and
`container` arrays. Compositor layout vocabulary is `splith`, `splitv`,
`tabbed`, and `stacking`. `size` is an initial percentage.

A GUI window supports `name`, `command`, `match`, `cwd`, `env`, `adopt`, `size`,
and `enabled`. `match` may use `class`, `app_id`, `instance`, `title`, or
`window_role`. No application names or commands are special-cased. With no
matcher, event discovery accepts only one unambiguous new window from that
launch. `adopt = true` requires an explicit matcher.

A kitty OS window supports `name`, `class`, `cwd`, `env`, `executable`,
`config`, `options`, `size`, and either inline panes/tabs or a raw kitty session.
Direct panes form one implicit tab. Explicit tabs use `[[workspace.kitty.tab]]`
and contain their own panes. `session = ".dev/special.kitty"` is the escape
hatch; it is mutually exclusive with inline `pane` and `tab` arrays.

## Workflow arguments and interpolation

`[args]` maps names to declarations with `position`, `required`, optional
`default`, `choices`, and `help`. Positions are unique and contiguous from zero.
Extra, missing, and invalid-choice values are errors.

All supported string fields can interpolate `{arg}`, `{project}`,
`{project_name}`, `{workflow}`, `{session}`, `{session_id}`, and node-specific
`{element_id}`. `:q` shell-quotes and `:url` percent-encodes. Commands remain
argv arrays and do not invoke a shell unless the configuration explicitly does.

## Stable identity

The session ID is the first 24 hexadecimal characters of SHA-256 over the JSON
encoding of:

```text
[canonical project directory, expanded session string]
```

Compositor elements add their canonical identity path. Kitty panes add the
kitty-window, tab, and pane identities. Commands, visible titles, cwd, layout,
size, and file paths do not affect identity.

Compositor containers receive additive `layouter_*` marks. Kitty panes receive
stable kitty user variables and process environment markers:

```text
LAYOUTER_SESSION
LAYOUTER_ELEMENT
LAYOUTER_PANE_ID
```

Identity therefore survives title changes, layout changes, tab movement, and
pane detachment within the same kitty process.

## i3/Sway backend

The backend speaks the i3 binary IPC protocol directly and finds the socket via
`I3SOCK`, `SWAYSOCK`, or the relevant compositor executable. Tree and mark
inspection is shared between i3 and Sway, including native Sway `app_id` views.

For each missing GUI leaf, the backend:

1. reuses any marked ancestors already present;
2. focuses the nearest surviving managed ancestor;
3. subscribes to window events and waits for acknowledgement;
4. snapshots the baseline tree and launches the process;
5. discovers one matching new managed view and marks it;
6. moves only that newly created view when attachment is still necessary.

This ordering avoids sleeps and workspace-switch races. Existing real windows
are never moved. Ambiguous discovery is an error rather than a guess.

After leaf creation, one compositor-neutral planner assembles wholly new
structural groups from the bottom up. A first new child seeds the split; the
second is moved beside it; the resulting common parent is discovered from a new
tree snapshot and marked. Additional new children are attached in declaration
order, then the declared layout and initial proportions are applied. The same
path supports recursive `splith`, `splitv`, `stacking`, and `tabbed` groups on
i3 and Sway.

The mutation boundary is strict. Nodes created during the current invocation
may be arranged. A missing child may be attached to a surviving marked parent.
Existing descendants are not reparented to recreate a missing parent, and an
existing group's layout or proportions are not reapplied. Layouter reports the
preserved mismatch when exact nesting is skipped.

A redundant container with one child may be flattened by the compositor. For a
declared `tools: splitv` containing only `terminal`, alongside a workspace-level
`browser`, the live tree may contain `terminal` and `browser` as direct siblings.
If another `tools` child is declared later, create-only reconciliation will not
move the existing terminal merely to reconstruct that otherwise invisible node.

## Kitty backend

Each managed kitty OS window has a deterministic Unix remote-control socket
under `$XDG_RUNTIME_DIR/layouter/<session-id>/` (with a secure `/tmp` fallback).
Layouter starts kitty with remote control enabled, uses `kitty @ ls` to inspect
tabs and panes, and launches only absent panes. During ordinary reconciliation,
existing tabs do not receive a layout command; the declared layout is applied
only when Layouter creates that tab.

If the compositor window exists but its socket cannot be inspected, the
operation fails rather than treating the terminal as absent. A dead socket is
stale only when no corresponding marked compositor window exists. Raw kitty
sessions are opaque and support OS-window reconciliation only, not pane-level
reconciliation.

## Reconciliation and failures

The reconciler walks the desired tree and classifies each element from live
state. Existing elements are skipped without correction; missing leaves are
created through their backend. A second invocation against unchanged live state
therefore performs no creation.

A desktop-wide operation cannot be atomic. Successful creations remain after a
later failure, and the next invocation rediscovers them. Runtime sockets,
bootstrap files, logs, and advisory locks exist only for live communication and
diagnostics; they are not desired-state storage.

## Explicit synchronization

`--sync` runs after missing elements have been created. It corrects only
Layouter-managed state and never prunes:

- i3/Sway workspace identity and name, tiled state, nested parentage, relative
  managed-child order, declared layout, and `size` percentages;
- inline kitty pane-to-tab membership, relative declared-pane order, tab title,
  and tab layout.

When compositor structure differs, all declared managed leaves are moved to a
temporary uniquely named workspace. Structural marks are cleared, leaves are
returned to their declared workspaces in declaration order, and containers are
rebuilt bottom-up. The staging workspace contains no unmanaged windows and
normally disappears when emptied. Marks make an interrupted operation
recoverable on the next invocation.

Unmanaged windows and panes are not moved or closed. They may still share a
parent whose layout or ratios are explicitly changed. Kitty tab reconstruction
moves only declared panes; arbitrary internal geometry of the `splits` layout
is not reconstructed. Raw kitty sessions remain opaque. Sync also does not
restart processes, replace commands, change cwd/environment, or retitle
individual panes.

## Modules

- `schema.py`: readable TOML normalization.
- `config.py`: file selection, validation, and interpolation.
- `model.py`: desired-state objects and deterministic identities.
- `i3.py`: i3/Sway IPC, discovery, event ordering, and placement.
- `kitty.py`: remote control and pane-level reconciliation.
- `reconcile.py`: create-only and explicit corrective orchestration.
- `runtime.py`: private runtime files, process launch, logs, and locking.
- `cli.py`: command parsing, reporting, and exit codes.

## References checked 2026-09-09

- [i3 IPC](https://i3wm.org/docs/ipc.html)
- [i3 user guide](https://i3wm.org/docs/userguide.html)
- [Sway IPC](https://man.archlinux.org/man/sway-ipc.7.en)
- [Sway commands](https://man.archlinux.org/man/sway.5.en)
- [kitty remote control](https://sw.kovidgoyal.net/kitty/remote-control/)
- [kitty session files](https://sw.kovidgoyal.net/kitty/sessions/)
- [Python `tomllib`](https://docs.python.org/3/library/tomllib.html)
- [Python `argparse`](https://docs.python.org/3/library/argparse.html)
