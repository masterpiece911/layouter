# Repository guidance

## Project overview

Layouter turns TOML or React/TSX workflows into Linux development workspaces
using i3/Sway and kitty. Python 3.11+ implements the CLI and desktop operations;
Node.js 22+ runs TSX workflows. The Python package has no runtime dependencies
outside the standard library. Full desktop functionality requires kitty 0.40.0+.

Read `docs/design.md` before changing reconciliation, ownership, or identity.
Use `docs/workflows.md` and `docs/react-workflows.md` for public behavior,
`docs/integrations.md` for launcher and API consumers, `docs/validation.md` for
checks, and `docs/packaging.md` for distribution details.

## Code map

- `src/layouter/cli.py`: CLI operations and reporting.
- `src/layouter/metadata.py`: public workflow and argument metadata for integrations.
- `src/layouter/sources.py`, `schema.py`, and `config.py`: workflow discovery,
  frontend evaluation, normalization, argument binding, interpolation, and validation.
- `src/layouter/model.py`: shared desired-state records and stable identity rules.
- `src/layouter/reconcile.py`: creation and explicit synchronization.
- `src/layouter/i3.py` and `kitty.py`: desktop protocols and backend operations.
- `src/layouter/runtime.py`: private runtime files, process launches, logs, and locks.
- `src/layouter/capture.py`: capture and save-layout TOML generation.
- `src/layouter/react_runtime.py`: bundled evaluator verification and execution.
- `react/src/` and `react/runner.mjs`: public React API, renderer, compiler, and evaluator.
- `tests/` and `react/test/`: Python unittest, renderer, and TypeScript checks.
- `scripts/`: build and release tooling; `examples/`: sample workflows.

## Behavioral invariants

- **No autonomous reconciliation.** Companion software may persist identity and expose
  application state, but user-visible mutation occurs only during an explicit
  Layouter invocation. Clearing copied tab identity metadata after duplication is
  passive metadata maintenance; both tabs and their browser state remain intact.
  Passive toolbar indicators may reflect that metadata without reconciling browser state.

- **Browser-derived browsing data never crosses the Firefox companion boundary.**
  Keep tab URL comparison and browser-sensitive synchronization inside the extension.
  Python sends declarations and receives only structural integration metadata.

- **Create, don't correct; preserve, don't prune.** Ordinary reconciliation creates
  missing elements and preserves existing processes and arrangements. Removing a
  declaration must not close anything. Respect declared focus and `--no-focus`.
- Rearrangement of existing managed elements belongs to `--sync`; display
  preferences are reapplied with `--sync-displays`. Neither mode prunes windows
  or restarts processes. Workspaces are destinations and must not be renamed.
- Discover live state from compositor trees, marks, and kitty remote control.
  Do not introduce a daemon or persistent database as a substitute for discovery.
- Keep identity separate from launch commands and presentation. Preserve stable
  session, element, tab, and pane identities across edits and repeated runs.
- TOML and TSX must feed the same model, validator, and reconciliation engine.
  Local workflow files override global files as a whole; do not implicitly merge them.
- Verify ownership and effects using events and fresh snapshots. Ambiguous
  matches are errors; an inaccessible kitty socket does not mean a terminal is
  absent. Arbitrary sleeps are not proof of successful discovery or placement.
- Partial failures leave successful work available for the next invocation to
  rediscover. Do not implement recovery by destroying already running work.
- Commands are argument arrays; shell evaluation requires an explicitly invoked
  shell. `--check` makes no desktop connection, but TSX validation executes code
  with the invoking user's permissions. `--dry-run` inspects without applying changes.
- Save-layout validates and backs up TOML before replacement. TSX source is not
  a target for automatic layout rewriting.

## Integrations

Launchers such as vicinae-layouter should consume the metadata API rather than
parse workflow files or human-readable output. See `docs/integrations.md` for
the complete discovery, argument-form, and launch flow.

- Discover with `layouter -C PROJECT --list --json`; inspect a selection with
  `--describe --json NAME` or `--list-args --json NAME`; launch with
  `layouter -C PROJECT NAME VALUES...`. Options precede the workflow name;
  every token after it is a workflow argument, including tokens starting with `-`.
- Keep the project directory and source selection consistent across these calls.
  Use a discovered absolute `source` with `--file` to retain that source for
  inspection and launch. `--file` and `--global` are mutually exclusive.
- Preserve the `schema_version: 1` JSON contract. Successful metadata stdout is
  one object: listing returns `workflows`, description returns `workflow`, and
  argument inspection returns a workflow name in `workflow` plus `args`.
  Consumers should check the version and ignore unknown fields within it.
- Workflow records contain `name`, `source`, `format`, `description`, and `args`.
  Descriptions are literal string-or-null summaries; display them as plain text.
  Argument records are in positional order. Keep values as strings and preserve
  empty strings; only trailing optional arguments can be omitted for defaults.
- Distinguish `args: null` (not inspected) from `args: []` (no arguments).
  Listing never imports TSX and leaves TSX arguments and descriptions null.
  Explicit inspection imports the selected module to read `defineWorkflow`
  metadata, but does not render or access the desktop. Do not execute all TSX
  workflows for background discovery or shell completion.
- Invoke subprocesses with argument arrays. Parse JSON only on success; stderr
  can contain diagnostics even on success. Metadata failures emit no JSON.
  Exit codes are `0` for success, `1` for runtime/backend errors, `2` for
  configuration/CLI errors, and `130` for interruption. No discovered workflows
  is currently a configuration error, not a successful empty list.
- `--json` applies only to metadata helpers; launch output is human-readable.
  Metadata inspection does not replace full validation with `--check`.
- Python consumers can import `list_workflows`, `describe_workflow`, and
  `list_args` from `layouter.metadata`. They return lists/dictionaries without
  the CLI version envelope, accept `selected` or `force_global` for source
  selection, and raise `ConfigError` for configuration failures.
- Keep the public metadata schema distinct from the internal Python/Node
  evaluator protocol (`protocol: 1`, newline-delimited JSON). Evaluator stdout
  is reserved for protocol responses; diagnostics go to stderr. Coordinate
  protocol changes across Python, `react/runner.mjs`, and the bundled runtime.

When changing integration behavior, update `tests/test_metadata.py` and relevant
source/evaluator tests, and keep `docs/integrations.md` and the metadata reference
in `docs/workflows.md` consistent.

## Development and validation

Run commands from the repository root. Match nearby code conventions; keep
Python changes compatible with 3.11 and TypeScript compatible with the strict
NodeNext configuration in `react/tsconfig.json`. Use the existing unittest and
Node test infrastructure rather than adding a test framework.

For Python changes:

```sh
make test
make check
```

Select Python with `make test PYTHON=/path/to/python3`. For a focused test run:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_reconcile.py' -v
```

For React changes, source discovery, argument binding, or evaluator protocol changes:

```sh
make deps
npm test --prefix react
python3 scripts/build_react_runtime.py
make test
```

Building the runtime enables Python TSX integration tests, which otherwise may
skip when prerequisites are missing. Report skipped or blocked checks explicitly.
Python backend fixtures need local Unix sockets and subprocess execution; they
do not require a running desktop. Packaging tests rebuild `dist/layouter-core`.

Add regression coverage for behavioral fixes, especially preservation, repeated
runs, identity, ambiguity, and TOML/TSX parity. Desktop-operation changes also
need the live checks in `docs/smoke-test.md` under i3 and Sway when available;
report when live verification was not possible.

For packaging changes, run `make release` with Python venv support, Node.js 22+,
npm, and `dpkg-deb` available. It installs build dependencies, runs checks, builds
artifacts, and verifies them outside the checkout. It does not publish them.
Do not use `make install` as a routine test; it installs into the user's prefix.

## Generated files and documentation

- Edit sources, not generated `dist/`, `react/dist/`, or
  `src/layouter/_react_runtime.zip`. Rebuild those outputs with the existing scripts.
- Use `npm ci --prefix react` for locked dependencies. Keep `react/package-lock.json`
  consistent with intentional dependency changes.
- Preserve the bundled portable React runtime: release users should not need npm,
  and TOML execution must not require a Node process. Packaging hooks must not
  download npm dependencies or execute workflow modules.
- Update the relevant reference docs and examples when changing public CLI or
  workflow behavior. Record user-visible changes in `CHANGELOG.md` using its
  existing format.
