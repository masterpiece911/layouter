# Changelog

## Unreleased

- Accept Mozilla manifest JSON reserialization during signed-companion verification,
  while rejecting changed values or other payload changes. Allow importing an
  already approved XPI without resubmitting it for signing.

## 1.1.0 — 2026-09-30

- Sign the Firefox companion through Mozilla's unlisted channel on version-tag
  releases, verify the returned payload and publish the signed XPI with release
  checksums. Document AMO credential setup; ordinary CI and local builds remain
  independent of signing credentials.

- Avoid immediately synchronizing Firefox tabs a second time after creating a
  window. Loading pages and redirects no longer cause duplicate tabs during the
  same invocation that creates them.

- Allow Firefox tab synchronization to complete while newly created pages are
  loading or redirecting. URL comparison still decides when an existing managed
  tab needs a preserved old copy plus a fresh declared tab.

- Show a green L toolbar badge for the selected managed Firefox tab, with a
  declared-identity tooltip. Clear it for unmanaged, duplicated and out-of-parent
  tabs; show an amber warning for conflicting windows. No new permissions or
  content scripting are required.

- Keep the original Firefox tab tracked when it is duplicated, clearing only the
  duplicate's Layouter metadata passively. Existing duplicate tab identities use
  the lowest runtime tab ID when there is no prior tracking observation. Both
  tabs remain open; duplicate window identities still fail as ambiguous.

- Place declared Firefox tabs leftmost during `--sync`, in declaration order
  within the pinned and unpinned sections, preserving all unmanaged tabs.

- Keep Firefox browsing information inside the companion: protocol 2 uses local
  `ensure-tabs`/`sync-tabs` operations and structural-only inspection. Native
  responses and diagnostics never carry live URLs or titles. The extension keeps
  the `tabs` permission and declares data collection `none`. Update Layouter and
  the companion together; existing persistent identities remain unchanged.

- Add FirefoxWindow/FirefoxTab declarations in TOML and TSX, a companion extension
  and native messaging bridge, ownership routing, session-identity recovery, and
  additive tab creation. `--sync` preserves old URL state in unmanaged tabs while
  recreating declared URLs and restoring pinning, order and activation. Window
  identity includes executable/args. Companion actions require an invocation.
  Duplicate window identities fail safely as ambiguous.

- Fix unreliable floating-window size and placement during client startup and
  `--sync` by retrying until geometry remains stable within the compositor timeout.
- Validate optional workflow descriptions and expose TSX definition descriptions
  through `--describe`; add explicit `--text` alongside `--json` for metadata helpers.
- Add `--list-args`, `--describe`, and versioned JSON output for workflow
  discovery and metadata, plus matching helpers in `layouter.metadata`.
  Selected TSX argument inspection imports metadata without rendering layouts.

Firefox companion verification: Firefox and Firefox Developer Edition passed
live Sway checks. i3, native Wayland Firefox windows and permanent signed
installation remain unverified. Zen 1.22.3b did not pass the multi-window
scenario; see `docs/firefox.md` for details. Install the signed companion XPI
alongside Layouter 1.1.0; unsigned XPIs are development/signing inputs.

## 1.0.0 — 2026-09-24

First stable release of Layouter, a desired-state workspace launcher for Linux,
i3/Sway, and kitty.

- Declare workspaces, application windows, terminal tabs, and panes in TOML or
  React/TSX, with reusable components and workflow arguments.
- Restore missing tools while preserving existing windows and running processes;
  use `--sync` to restore the declared arrangement explicitly.
- Adapt React layouts to connected displays, discover project-local and shared
  workflows, and validate configurations with `--check`.
- Capture desktop layouts and save arrangements back to TOML workflows.
- Target numbered workspaces, including number-only declarations, while
  preserving configured i3/Regolith workspace names when creating them.
- Install a portable executable, Python wheel, or Debian package with the React
  runtime included. Optional TOML-only and React editor packages are available.
- Local source installations retain the selected Python interpreter, including
  when the system's default Python is older.

Requires Linux and Python 3.11+; launching workspaces requires i3 or Sway and
kitty. React/TSX workflows additionally require Node.js 22+. Release users do
not need npm. TSX workflows are executable configuration and must be trusted.
