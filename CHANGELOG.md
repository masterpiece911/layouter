# Changelog

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
