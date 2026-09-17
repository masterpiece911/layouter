# Layouter

**Create, don't correct; preserve, don't prune.**

Layouter turns a TOML file into a running Linux development workspace. You
describe the workspaces, applications, terminal tabs, and terminal panes a
project needs; Layouter inspects i3 or Sway and kitty, then creates whatever is
missing.

That last part is the point. Starting a development environment should not mean
tearing down one that is already useful. If the editor and development server
are still running, but the test pane and browser have disappeared, the next
invocation creates only the test pane and browser. It does not restart the
editor, disturb the server, or remove windows you opened yourself.

```sh
layouter
layouter morning garden
layouter evening
layouter -C ~/src/garden
layouter -C ~/src/meadow studio
```

Here, `morning`, `evening`, and `studio` are example workflow filenames, and
`garden` is an argument declared by the `morning` workflow. They are names you
choose, not built-in subcommands.

Normal invocations are deliberately conservative. An explicit `--sync` mode is
available when you do want Layouter to restore the declared arrangement of its
managed elements:

```sh
layouter --sync morning garden
```

## Installation

Layouter requires Linux, Python 3.11 or newer, i3 or Sway, and kitty. It uses no
third-party Python packages at runtime.

The release artifact is a self-contained Python zip application. Put it
somewhere on your `PATH`:

```sh
install -Dm755 layouter ~/.local/bin/layouter
layouter --version
```

To build that executable from a source checkout instead:

```sh
python3 scripts/build_zipapp.py
install -Dm755 dist/layouter ~/.local/bin/layouter
```

## A first workflow

A project workflow lives in `.dev/<workflow>.toml`. For example, save the
following as `.dev/morning.toml`:

```toml
session = "morning-{microfrontend}"
focus = "code"

[args]
microfrontend = { position = 0, required = true }

[[workspace]]
number = 2
name = "code"
output = ["DP-1", "eDP-1"]

  [[workspace.window]]
  name = "zed"
  command = ["zed", "-n", "."]

[[workspace]]
number = 3
name = "dev"
output = ["DP-1", "eDP-1"]
layout = "splith"

  [[workspace.kitty]]
  name = "frontend"
  class = "dev-frontend"
  layout = "tall"
  size = 65

    [[workspace.kitty.pane]]
    title = "vite"
    command = ["npm", "run", "dev", "--", "--mf", "{microfrontend}"]

    [[workspace.kitty.pane]]
    title = "tests"
    command = ["npm", "run", "test", "--", "--watch"]

    [[workspace.kitty.pane]]
    title = "types"
    command = ["npm", "run", "typecheck", "--", "--watch"]

  [[workspace.kitty]]
  name = "backend"
  class = "dev-backend"
  layout = "fat"
  size = 35

    [[workspace.kitty.pane]]
    title = "server"
    command = ["npm", "run", "backend"]

    [[workspace.kitty.pane]]
    title = "logs"
    command = ["npm", "run", "logs"]

[[workspace]]
number = 4
name = "browser"
output = ["HDMI-1", "eDP-1"]

  [[workspace.window]]
  name = "browser"
  command = [
    "firefox",
    "--new-window",
    "http://{microfrontend}.localhost:5173"
  ]
```

Replace the display names with your preferred connectors, listed by
`i3-msg -t get_outputs` or `swaymsg -t get_outputs`. Remove the `output` lines to
let the compositor choose. The first connected display in each array wins.
This example prefers `DP-1` for new `code` and `dev` workspaces and `HDMI-1` for
`browser`, with `eDP-1` as a fallback for each. If none are connected, the
compositor chooses. Existing workspaces keep their display
unless you enable display synchronization.

Now run:

```sh
layouter morning garden
```

`garden` becomes the value of `microfrontend`. Layouter expands the session
name to `morning-garden`, opens the three compositor workspaces, starts the two
kitty OS windows and their panes, opens Zed and Firefox, and finally focuses the
`code` workspace. Running the same command again creates nothing if everything
is still present.

Use `--check` while writing a workflow. It parses and expands the file without
connecting to the desktop or launching anything:

```sh
layouter --check morning garden
```

## Finding workflows

Unless `-C` is present, the project is the current directory. `-C DIR`
selects the project directory before Layouter resolves the
workflow, relative paths, and working directories. It affects session identity
only when the `session` template explicitly includes the project.

For `layouter morning`, Layouter first looks for `.dev/morning.toml` in the selected
project. If that file is absent, it looks for
`~/.config/layouter/morning.toml`. `XDG_CONFIG_HOME` replaces `~/.config` when it
is set. The same rule applies to the implicit `default` workflow used by a bare
`layouter` invocation.

Each workflow is one complete file. Local and global declarations are never
merged: a local file wins in full, and the global file is only a fallback.
`--global` selects the global file explicitly, while `--file FILE` selects one
exact file.

This makes global workflows useful for common tools without quietly injecting
pieces of global configuration into a project's own desktop declaration.

## Arguments, paths, and identity

Everything after the workflow name is a workflow argument. Arguments bind by
position, and positions must be unique and contiguous from zero. A default
makes an argument optional:

```toml
[args]
microfrontend = { position = 0, required = true }
environment = { position = 1, default = "local" }
```

Layouter interpolates strings after binding the arguments. Along with declared
arguments, `{project}`, `{project_name}`, `{workflow}`, `{session}`,
`{session_id}`, and the node-specific `{element_id}` are available. A URL value
can be encoded with `{value:url}`. Commands are argv arrays and do not invoke a
shell, which keeps interpolated values from accidentally becoming shell syntax.
When a pipeline or another shell feature is intentional, invoke a shell
explicitly and use `{value:q}`:

```toml
command = ["sh", "-c", "tool {microfrontend:q} | formatter"]
```

`cwd` and `env` flow from the workflow to a window or kitty declaration, then
to its tabs and panes. Relative paths always start at the canonical project
directory, including when the project was selected with `-C`.

The canonical workflow-file path and expanded `session` string form the stable
session identity. Declaration names extend that identity for individual elements.
The same global TOML and session refer to the same running layout whether launched
from a terminal, a launcher such as Vicinae, or a different directory. Symlinks to
the same file share identity; separate files do not. Editing a file in place does
not change its identity unless its expanded `session` changes. Moving or copying
the file to another canonical path creates a different identity.

To deliberately create a separate instance per project, express that in TOML:

```toml
session = "dev-{project}"
```

Use `{project}` for the full canonical directory; `{project_name}` can collide
between directories with the same basename. `layouter -C /path/to/project a`
and running `layouter a` inside that directory are equivalent. Project paths
still determine cwd, relative paths, and placeholder values for newly created
elements. Existing processes keep their original cwd and environment. For a
shared global workflow that should always launch missing elements in one place,
set its `cwd` explicitly as well.

Workspaces, GUI windows, kitty OS windows, containers, and tabs use `name` as
their declaration identity. A pane can use its `title` as both identity and
presentation, as in the first example. Give it a `name` as well when the title
is expected to change without changing identity:

```toml
[[workspace.kitty.pane]]
name = "tests"
title = "Checkout tests"
command = ["npm", "test", "--", "--watch"]
```

## Describing the compositor tree

The TOML follows the compositor's own tree. A workspace can contain windows,
kitty OS windows, and recursively nested containers. Layout names map directly
to i3/Sway: `splith`, `splitv`, `tabbed`, and `stacking`.

```toml
[[workspace]]
number = 3
name = "dev"
layout = "splith"

  [[workspace.window]]
  name = "frontend"
  size = 65
  command = ["frontend"]

  [[workspace.container]]
  layout = "splitv"
  size = 35

    [[workspace.container.window]]
    name = "backend"
    command = ["backend"]

    [[workspace.container.window]]
    name = "logs"
    command = ["logs"]
```

Layouter builds new groups from real application windows using compositor IPC.
It subscribes to window events before launching each application, waits for the
actual window, and verifies every structural operation against a fresh tree.
There are no startup sleeps and no race-prone sequence of workspace-switching
commands.

`size` is a percentage. During ordinary reconciliation it is applied only when
Layouter constructs an entirely new group, because resizing an established
group would also disturb existing siblings. `--sync` may reapply it explicitly.

Most applications can be discovered as the one new window created after their
command starts. An application that creates several windows, or one launched on
a particularly busy desktop, can provide an explicit matcher:

```toml
# X11/XWayland
match = { class = "^org.example.App$" }

# Native Wayland under Sway
match = { app_id = "^org.example.App$" }
```

Matchers support `class`, `app_id`, `instance`, `title`, and `window_role`, with
values interpreted as Python regular expressions. `adopt = true` lets Layouter
attach identity to exactly one matching unmanaged window. Adoption never moves
the window and requires an explicit matcher.

### The singleton-container limitation

i3 and Sway may flatten a container with only one child because that container
has no visible layout effect. Consider this tree:

```toml
[[workspace]]
name = "dev"
layout = "splith"

  [[workspace.container]]
  name = "tools"
  layout = "splitv"

    [[workspace.container.window]]
    name = "terminal"
    command = ["terminal"]

  [[workspace.window]]
  name = "browser"
  command = ["browser"]
```

The live tree may contain `terminal` and `browser` directly under the workspace
instead of retaining the one-child `tools` node. If another child is later
declared under `tools`, ordinary reconciliation creates it at the nearest
surviving managed ancestor rather than moving `terminal`. `--sync` can rebuild
`tools` once it has multiple children. A container that still has only one child
may remain flattened even after synchronization.

## Making kitty controllable by Layouter

Layouter controls only the kitty OS windows it launches. It gives every such
window a deterministic Unix socket, then uses kitty's remote-control API to
inspect tabs and panes on every invocation. This is what makes it possible to
add one missing test pane without recreating the terminal.

There is no required change to your global `kitty.conf`. To prepare kitty:

1. Install kitty and make sure the `kitty` executable is available to the same
   graphical session that runs Layouter:

   ```sh
   command -v kitty
   kitty --version
   ```

2. Declare kitty inside the workflow, as shown above. Do not start that kitty OS
   window yourself; let the first Layouter invocation create it:

   ```sh
   layouter --check morning meadow
   layouter morning meadow
   ```

3. Layouter starts it with the equivalent of these remote-control options:

   ```sh
   kitty \
     --override allow_remote_control=socket-only \
     --listen-on unix:/private/layouter/runtime/<element-hash>.sock \
     ...
   ```

   `socket-only` accepts control requests through the selected socket while
   denying requests sent through terminal escape sequences. Layouter creates
   the socket inside an owned directory with mode `0700`, normally
   `$XDG_RUNTIME_DIR/layouter`. If `XDG_RUNTIME_DIR` is unavailable, it uses
   `/tmp/layouter-<uid>`.

4. If you want to verify the connection, locate the socket and ask kitty for
   its live tree:

   ```sh
   if [ -n "${XDG_RUNTIME_DIR:-}" ]; then
     layouter_runtime="$XDG_RUNTIME_DIR/layouter"
   else
     layouter_runtime="/tmp/layouter-$(id -u)"
   fi

   find "$layouter_runtime" -maxdepth 1 -type s -name '*.sock' -print
   kitty @ --to "unix:/path/from/the-command.sock" --use-password never ls
   ```

The socket name is intentionally hashed, so several projects and expanded
session names can run independently. Layouter also records stable hashes in
kitty pane user variables and sets `LAYOUTER_SESSION`, `LAYOUTER_ELEMENT`, and
`LAYOUTER_PANE_ID` in pane environments.

An arbitrary kitty that was launched by hand has neither Layouter's socket nor
its identity metadata and is therefore left alone. Enabling remote control in
global `kitty.conf` does not make such a window adoptable. Likewise, do not put
`allow_remote_control` or `listen_on` in a workflow's kitty `options`; Layouter
owns those values so it can guarantee a private, deterministic endpoint.

You can still use a custom kitty configuration for appearance and ordinary
kitty behavior:

```toml
[[workspace.kitty]]
name = "terminal"
config = "~/.config/kitty/kitty.conf"
layout = "tall"

  [[workspace.kitty.pane]]
  title = "shell"
```

Inline kitty declarations can contain direct panes or explicit tabs. Supported
layouts include `tall`, `fat`, `grid`, `horizontal`, `vertical`, `stack`, and
`splits`. Panes support `cwd`, `env`, `title`, `hold`, `after`, and `location`.
When `command` is omitted, the pane starts `$SHELL`, falling back to `/bin/sh`.

For unusual setups, a handcrafted kitty session remains available as a
startup-only escape hatch:

```toml
[[workspace.kitty]]
name = "weird-one"
session = ".dev/special.kitty"
```

`session` is mutually exclusive with inline pane and tab declarations. Layouter
can start and identify that kitty instance, but the contents of the session file
are opaque: missing internal panes are not reconciled or synchronized.

See kitty's documentation for the underlying
[socket transport](https://sw.kovidgoyal.net/kitty/remote-control/#remote-control-via-a-socket)
and [`allow_remote_control`](https://sw.kovidgoyal.net/kitty/conf/#opt-kitty.allow_remote_control)
behavior.

## Workspace destinations

A numbered workspace targets the user's workspace with that number. For example,
`name = "1"` or `number = 1` with a name uses an existing workspace such as
`1: terminal`, preserving its name. If it is inactive, Layouter activates the
ordinary workspace with `workspace number 1`, just like a numbered shortcut.
Names without a leading number are matched exactly.

Workspaces are destinations, not marked Layouter objects. Neither ordinary runs
nor `--sync` rename them. Existing windows stay where they are during ordinary
runs; `--sync` returns managed windows to their declared destinations.

Set `output` to an ordered array of exact display connector names to choose
where a new workspace opens. Layouter uses the first connected display:

```toml
[[workspace]]
number = 2
name = "code"
output = ["DP-1", "eDP-1"]
```

List connector names with `i3-msg -t get_outputs` or `swaymsg -t get_outputs`.
A single name, such as `output = "DP-1"`, is also accepted. Each workspace can
have its own preferences. With no matching connected display, an empty array,
or no `output` setting, Layouter leaves display placement to the compositor.

By default, ordinary runs apply these preferences only when activating a missing
workspace and preserve the display of existing workspaces. To reapply display
preferences on every invocation, put this at the top of the workflow file,
before any table headers:

```toml
sync_displays = true
```

For a single invocation, use `--sync-displays`:

```sh
layouter --dry-run --sync-displays morning garden
layouter --sync-displays morning garden
```

Both options fill gaps as usual and move existing destination workspaces to their
first connected preferred display, without correcting existing window placement,
layouts, sizes, order, or kitty tabs and panes. Full `--sync` includes display
synchronization along with its other corrections. `sync_displays` defaults to
`false`; the flag enables it regardless of the workflow setting.

A display move carries the entire workspace, including unmanaged windows on it.
If none of the preferred displays are connected, the workspace stays on its
current display. A workspace already on its preferred display is left alone.
`--dry-run` respects both options and reports planned display moves without
changing the desktop. `--no-focus` restores the original focus afterward.
`--check` validates the setting without requiring a connected display.

## Reconciliation and synchronization

Layouter does not keep a persistent registry or daemon. The source of truth is
the live desktop: i3/Sway marks identify managed containers and windows, while the
kitty socket and pane user variables identify the terminal hierarchy.

An ordinary invocation only fills gaps. Existing applications and panes are
not restarted. Existing windows are not moved or resized. Changed titles and
layouts are accepted as user choices, and additional unmanaged windows and
panes remain untouched. Creating a new tiled window necessarily causes the
compositor to allocate space for it, but Layouter performs no further
correction.

`--sync` is the explicit exception. It first creates anything missing, then
restores the declared arrangement of managed elements:

```sh
layouter --dry-run --sync morning meadow
layouter --sync morning meadow
```

For i3/Sway, synchronization restores managed workspace assignments,
tiled state, nested parentage, relative managed-child order, container layouts,
and declared percentages. When nesting differs, Layouter moves the managed
windows through a temporary workspace and rebuilds their tree from the bottom
up. Unmanaged windows are not moved or closed, although changing a layout or
ratio on a shared parent naturally changes the space available to them.

For inline kitty declarations, synchronization brings declared panes back into
their declared tabs, restores their relative order, and reapplies tab titles
and layouts. Unmanaged panes remain where they are. The `splits` layout owns an
arbitrary internal geometry that cannot be reconstructed from pane order alone,
so Layouter restores membership and order but not every manually edited split.

Even in sync mode, Layouter does not restart processes, replace commands,
change a running process's working directory or environment, retitle individual
panes, close extra elements, or inspect the internals of a handcrafted kitty
session. A second sync against an already matching desktop is a no-op.

## Saving a desktop arrangement

Use `--capture` to create a new workflow draft from the current i3/Sway desktop:

```sh
layouter --capture studio
layouter --file .dev/captured.toml --capture
```

The first command writes `.dev/studio.toml` in the selected project. `-C`,
`--file`, and `--global` also select capture destinations. Capture refuses to
replace an existing file and accepts no workflow arguments. It captures all
regular workspaces, their displays, tiled containers, layouts, and percentages.
It only reads desktop state; it does not move, launch, focus, or adopt windows.

GUI application declarations include matchers and best-effort command/cwd hints
from an accessible process. They start with `enabled = false`: review the argv,
supply missing launch information, then enable them. A live process command is
not always a reusable launch command, especially for applications sharing a
process across windows. Environments and application-internal state such as
browser tabs or unsaved documents are not captured.

Kitty instances with a discoverable private Layouter socket are captured as
inline tabs and panes, including available titles, layouts, working directories,
and command hints. Review those commands too: a foreground process can be
transient. An inaccessible terminal is exported as a disabled application draft.
Floating windows and scratchpad contents are omitted. Warnings appear both in
the generated file and on stderr.

Use `--save-layout` after rearranging an existing workflow:

```sh
layouter --save-layout morning garden
```

This updates the selected source file using the session identified by the
supplied workflow arguments. It saves managed tiled windows' workspace placement,
container nesting, relative order, sizes, and workspace outputs/layouts. New
structural containers are added as needed; unmanaged applications are not added.
Missing or disabled declarations are retained, including closed Kitty panes; a
later normal run recreates them. If no declared application windows match,
save-layout fails without rewriting the source or making a backup. Launch and
save using the same canonical workflow file and expanded session. The working
directory only affects identity if the session explicitly includes it. Commands, working directories,
environments, session templates, and argument declarations remain intact.
Changes to a parameterized workflow apply to that workflow file for all argument
values, with layout captured from the selected session.

For controlled Kitty windows, save-layout records tab titles/layouts and pane
order within each declared tab. Cross-tab pane moves retain their declared
membership and produce a warning because tab membership participates in pane
identity. Exact Kitty `splits` geometry and floating/scratchpad placement are not
representable. Moves that would change a location-derived element identity are
rejected; use simple names containing letters, digits, underscores, or hyphens
for elements you intend to move between containers.

Save-layout validates the result before replacing the source and keeps the
original bytes in `<file>.bak` (then `.bak.1`, `.bak.2`, etc.). It rewrites TOML
formatting with two spaces per nested table level; original comments remain in
the backup. Neither saving mode changes
the running desktop or combines with `--sync`, `--sync-displays`, `--no-focus`,
or another CLI mode.

An optional nonnegative `order` on windows, Kitty windows, and containers records
sibling order across different child types. Lower values come first; ties retain
declaration order. Without it, existing declaration ordering is unchanged.
This lets captures represent a window, a container, and another window in that
sequence despite TOML grouping arrays by field name.

## Command-line reference

Options must appear before the workflow name because every token after the
workflow belongs to it, including tokens beginning with `-`.

| Option | Meaning |
| --- | --- |
| `-C DIR` | Select the project directory |
| `-f FILE`, `--file FILE` | Use exactly this TOML file |
| `--global` | Force the global workflow file |
| `--list` | List discoverable workflows and argument signatures |
| `--check` | Parse, expand, and validate without desktop access |
| `--dry-run` | Inspect live state and print the plan; combines with `--sync` or `--sync-displays` |
| `--capture` | Create a new workflow draft from the live desktop |
| `--save-layout` | Save the live arrangement to the selected workflow, with backup |
| `--sync` | Correct declared layout and placement of managed elements |
| `--sync-displays` | Reapply workspace display preferences without other synchronization |
| `--no-focus` | Restore the original compositor focus |
| `--timeout SECONDS` | Set the IPC and discovery timeout |
| `--version` | Print the version |

Some representative invocations, assuming you have defined the `morning`,
`evening`, and `studio` workflows (`morning` takes one argument):

```sh
layouter -C ~/src/garden --list
layouter -C ~/src/garden --check morning garden
layouter -C ~/src/garden --global evening
layouter -C ~/src/garden --dry-run studio
layouter -C ~/src/meadow --dry-run --sync morning meadow
layouter -C ~/src/meadow --sync morning meadow
layouter -C ~/src/garden evening
```

Exit code `0` means success, `1` is a runtime or backend error, `2` is a
configuration or command-line error, and `130` means the invocation was
interrupted.

## Development

For a release, run `make release` on Linux with Python 3.11 or newer. This
runs the tests and validates the example before building `dist/layouter` and
`dist/layouter-<version>-source.zip`. The version comes from `pyproject.toml`;
keep it aligned with `src/layouter/__init__.py` when preparing a new version.
The target builds local artifacts; publishing and tagging are separate steps.

Use `make build` to build artifacts alone, or `make test` and `make check` to
run individual checks. Select an interpreter with `make release PYTHON=python3.12`.
Run `make help` for the available targets.

The automated suite needs no running desktop or third-party Python test
packages. Its socket fixtures require permission to bind local Unix sockets;
restricted environments report errors rather than skipping those tests. See
the [validation record](docs/validation.md) for the latest results and commands
for running individual test modules.

Run the test suite, validate the complete example, and build the distributable
artifacts with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m layouter -f examples/morning.toml --check morning garden
python3 scripts/build_zipapp.py
python3 scripts/build_source_archive.py
```

The implementation is separated into configuration discovery and parsing,
argument interpolation, desired-state objects, compositor and kitty backends,
runtime files, and reconciliation orchestration. For the deeper contract and
test status, see [the design](docs/design.md),
[validation record](docs/validation.md), and
[live desktop smoke check](docs/smoke-test.md).

## License

Layouter is available under the [MIT License](LICENSE).
