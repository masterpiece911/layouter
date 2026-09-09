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
layouter debug checkout
layouter -C ~/src/foo
layouter -C ~/src/foo debug checkout
```

Normal invocations are deliberately conservative. An explicit `--sync` mode is
available when you do want Layouter to restore the declared arrangement of its
managed elements:

```sh
layouter --sync debug checkout
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
following as `.dev/debug.toml`:

```toml
session = "debug-{microfrontend}"
focus = "code"

[args]
microfrontend = { position = 0, required = true }

[[workspace]]
number = 2
name = "code"

  [[workspace.window]]
  name = "zed"
  command = ["zed", "-n", "."]

[[workspace]]
number = 3
name = "dev"
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

  [[workspace.window]]
  name = "browser"
  command = [
    "firefox",
    "--new-window",
    "http://{microfrontend}.localhost:5173"
  ]
```

Now run:

```sh
layouter debug checkout
```

`checkout` becomes the value of `microfrontend`. Layouter expands the session
name to `debug-checkout`, opens the three compositor workspaces, starts the two
kitty OS windows and their panes, opens Zed and Firefox, and finally focuses the
`code` workspace. Running the same command again creates nothing if everything
is still present.

Use `--check` while writing a workflow. It parses and expands the file without
connecting to the desktop or launching anything:

```sh
layouter --check debug checkout
```

## Finding workflows

Unless `-C` is present, the project is the current directory. `-C` behaves like
Git's option: it changes the project context before Layouter resolves the
workflow, relative paths, working directories, and session identity.

For `layouter debug`, Layouter first looks for `.dev/debug.toml` in the selected
project. If that file is absent, it looks for
`~/.config/layouter/debug.toml`. `XDG_CONFIG_HOME` replaces `~/.config` when it
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

The canonical project directory and expanded `session` string form the stable
session identity. Declaration names extend that identity for individual
elements. That is why `debug-checkout` and `debug-payments` can coexist, while
two invocations whose expanded session is simply `debug` deliberately refer to
the same live workspace.

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
   layouter --check debug checkout
   layouter debug checkout
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

## Reconciliation and synchronization

Layouter does not keep a persistent registry or daemon. The source of truth is
the live desktop: i3/Sway marks identify managed compositor elements, while the
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
layouter --dry-run --sync debug checkout
layouter --sync debug checkout
```

For i3/Sway, synchronization restores managed workspace assignments and names,
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

## Command-line reference

Options must appear before the workflow name because every token after the
workflow belongs to it, including tokens beginning with `-`.

| Option | Meaning |
| --- | --- |
| `-C DIR` | Change project context; repeat successively like Git |
| `-f FILE`, `--file FILE` | Use exactly this TOML file |
| `--global` | Force the global workflow file |
| `--list` | List discoverable workflows and argument signatures |
| `--check` | Parse, expand, and validate without desktop access |
| `--dry-run` | Inspect live state and print the plan; combines with `--sync` |
| `--sync` | Correct declared layout and placement of managed elements |
| `--no-focus` | Restore the original compositor focus |
| `--timeout SECONDS` | Set the IPC and discovery timeout |
| `--version` | Print the version |

Some representative invocations:

```sh
layouter -C ~/src/foo --list
layouter -C ~/src/foo --check debug checkout
layouter -C ~/src/foo --global debug checkout
layouter -C ~/src/foo --dry-run debug checkout
layouter -C ~/src/foo --dry-run --sync debug checkout
layouter -C ~/src/foo --sync debug checkout
layouter -C ~/src/foo debug checkout
```

Exit code `0` means success, `1` is a runtime or backend error, `2` is a
configuration or command-line error, and `130` means the invocation was
interrupted.

## Development

Run the test suite, validate the complete example, and build the distributable
artifacts with:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONPATH=src python3 -m layouter -f examples/debug.toml --check debug checkout
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
