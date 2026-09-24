# Layouter design

Layouter describes a desktop as desired state and reconciles that description
with i3 or Sway and kitty. A workflow brings a project's tools together while
preserving the work already happening in them.

## Repository maxims

### Create, don't correct; preserve, don't prune

An ordinary invocation creates missing elements. Existing applications, tabs,
and panes keep their processes and arrangement. Undeclared windows remain part
of the user's desktop, and removing or disabling a declaration does not close
anything. Declared focus is applied at the end; `--no-focus` restores the
original compositor focus.

Correction requires explicit intent. `--sync` restores declared arrangements
of managed elements, and `--sync-displays` reapplies workspace display
preferences. Neither mode prunes applications or restarts their processes.

### Discover reality; do not maintain a second desktop

The compositor tree, Layouter marks, and kitty remote-control state are the
source of truth about what exists. Each invocation discovers them anew. There
is no daemon, process supervisor, or persistent state database. Runtime files
support communication, locking, and diagnostics; they do not stand in for the
live desktop.

### Identity is separate from presentation

A declaration identifies an element independently of its launch command or
current appearance. Changing a command does not replace a running process;
changing a layout does not create a second window. Explicit names express
continuity across edits. Presentation fields provide identity only where the
configuration deliberately uses them as a shorthand, such as a pane's title
when its name is omitted.

### One workflow, one meaning

A selected workflow is a complete declaration. Project-local files take
precedence over global files as a whole, without implicit merging. TOML and
React/TSX feed the same desired-state model, validator, and reconciliation
engine. A frontend changes how a workflow is authored, not how its desktop
operations behave.

### Verify effects; do not guess

Window discovery uses compositor events and fresh tree snapshots. Ambiguous
matches and failed structural verification are errors. An inaccessible kitty
socket does not make an existing terminal absent. Timing sleeps and
application-specific assumptions cannot establish ownership or prove that an
operation succeeded.

### Recover by rediscovering

Desktop operations are not a transaction. If a later operation fails,
successfully created elements remain available. Their live identities allow
the next invocation to continue from the desktop it actually finds. Recovery
does not destroy useful work to reconstruct an imagined clean slate.

## From workflow to desired state

The project directory supplies relative paths, default working directories,
and interpolation context. `-C` selects it before workflow resolution.
`layouter morning garden` selects the `morning` workflow and binds `garden` as
its first declared argument. Options precede the workflow name.

Discovery selects `.dev/morning.toml` or `.dev/morning.tsx`, then falls back to
the same name under the user's Layouter configuration directory. Two formats
with the same name in the selected scope are ambiguous. `--file` selects an
exact file; `--global` selects the global scope. A bare invocation selects
`default`. Listing discovers names without executing TSX modules.

TOML is declarative data. React/TSX is executable configuration that renders a
single desired-state document. Function components, arguments, and a snapshot
of connected displays allow composition and conditional layouts. The renderer
owns no compositor connection and performs no desktop reconciliation. Python
binds arguments, validates the resulting document, expands placeholders, and
constructs the shared `Workflow` model.

TSX runs with the invoking user's permissions, including during validation.
Selecting it authorizes execution; the evaluator is not a sandbox. Rendering
uses one display snapshot per invocation rather than a subscription or an
interactive update loop. `--check` uses an empty output snapshot and makes no
desktop connection. `--dry-run` inspects live state and reports intended
operations without applying them.

Commands are argument arrays. Shell evaluation happens only when a workflow
explicitly invokes a shell. Working directories and environment values inherit
through the declaration tree; interpolation and quoting are resolved before
launching applications.

## Identity and ownership

A file-backed session is scoped by the canonical workflow-file path and its
expanded session string. The project directory participates only when the
session template includes it. This lets the same global workflow address the
same running session from different launch locations, while a template such as
`morning-{project}` expresses a separate session for each project.

The identity components are hashed into stable keys. Compositor elements add
their declaration identities to the session scope. Kitty pane identities also
include their window and tab declarations. Explicit names keep identity
independent of structural positions where supported; unnamed TOML containers
use a position-derived identity. TSX containers require names.

Additive compositor marks and kitty user variables carry ownership in live
state. Pane environments also expose `LAYOUTER_SESSION`, `LAYOUTER_ELEMENT`,
and `LAYOUTER_PANE_ID`. Arbitrary matching windows are not owned implicitly:
GUI adoption requires an explicit matcher and an unambiguous match.

Workspaces are destinations, not owned objects. Numbered destinations reuse the
user's workspace with that number, preserving its name. Neither ordinary
reconciliation nor synchronization renames workspaces.

## Desktop reconciliation

The desired tree contains workspaces, nested tiling containers, application
windows, and kitty windows with tabs and panes. Floating applications belong
directly to workspaces and can declare geometry or a named position. The model
uses the compositor's layout vocabulary rather than inventing a parallel
layout language.

The i3/Sway backend subscribes to events before launching an application,
compares against a baseline tree, and identifies the resulting window. New
structural groups are assembled from real windows and verified against fresh
tree snapshots. Ordinary reconciliation may arrange newly created elements
and attach missing children to surviving managed parents. It does not move
existing descendants to rebuild a missing container or reapply an existing
group's proportions.

Each managed kitty window has a deterministic socket in a private runtime
directory. Layouter enables socket-only remote control for windows it launches,
inspects their tabs and panes, and creates only missing declarations. A raw
kitty session file delegates terminal contents to kitty; Layouter treats that
window's internals as opaque.

Explicit synchronization extends the mutation boundary to existing managed
elements. It restores workspace placement, tiling structure, managed order,
layouts, declared sizes, floating geometry, and inline kitty tab arrangement.
Display synchronization reapplies declared output preferences. Unmanaged
siblings remain present, although a shared parent's corrected layout can affect
their visible arrangement. Process commands, working directories, and
environments remain those of the running processes.

## Capturing intent from the desktop

Capture produces a TOML draft from live state for the user to review.
Save-layout updates an existing TOML workflow's arrangement while preserving
launch declarations and retaining missing elements. It validates the resulting
document and backs up the source before replacement. Neither operation mutates
the running desktop. TSX remains authored program source rather than a target
for automatic layout rewriting.

## Architectural boundaries

`sources.py` handles workflow discovery and frontend evaluation; `schema.py`
normalizes readable declarations; `config.py` binds arguments, interpolates,
and validates; `model.py` holds desired-state records and identity rules.
The React renderer and compiler live under `react/`, with the reconciler host
adapter isolated from the public workflow API.

`i3.py` and `kitty.py` implement desktop protocols and backend operations.
`reconcile.py` coordinates creation and explicit synchronization. `runtime.py`
owns private files, launch logs, process creation, and invocation locks.
`capture.py` translates live arrangements into TOML, while `cli.py` selects
operations and reports results.

The release bundles the React evaluator with the Python implementation.
`react_runtime.py` verifies and extracts that resource for TSX invocations;
TOML execution needs no Node process. The evaluator exchanges versioned JSON
messages with Python, keeping authoring machinery separate from desktop
ownership and mutation.

Configuration details belong in the [workflow reference](workflows.md) and
[React guide](react-workflows.md). Build and distribution mechanics belong in
[packaging](packaging.md); test procedures belong in [validation](validation.md).
