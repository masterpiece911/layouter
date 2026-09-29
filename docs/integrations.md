# Building integrations with Layouter

Launchers such as vicinae-layouter can use Layouter to discover workflows, build
argument forms, and launch a selected workspace. Let Layouter handle source
selection and validation; consume its metadata instead of parsing workflow files.

## Discover, inspect, launch

1. Select a project directory and run `--list --json` to populate a workflow picker.
2. Run `--describe --json` for the selected workflow to obtain its summary and
   argument declarations. Use `--list-args --json` if only the arguments are needed.
3. Collect values in positional order and launch the workflow with those values.

```sh
layouter -C /home/me/src/garden --list --json
layouter -C /home/me/src/garden --describe --json morning
layouter -C /home/me/src/garden --list-args --json morning
layouter -C /home/me/src/garden morning api dev
```

Options go **before** the workflow name. Every token after the name is a workflow
argument, including tokens beginning with `-`. Metadata inspection takes no
argument values and does not require required arguments to be filled in.

All three helpers default to human-readable output, also available explicitly
with `--text`. Integrations should use `--json`; text output is for people.

## Read the metadata contract

Successful JSON output is one object with `schema_version: 1`. Check the schema
version and ignore unknown fields within a supported version. The response
fields depend on the helper:

| Command | Response fields, besides `schema_version` |
| --- | --- |
| `--list --json` | `workflows`: array of workflow records sorted by name |
| `--describe --json NAME` | `workflow`: one workflow record |
| `--list-args --json NAME` | `workflow`: name string; `args`: argument records |

A workflow record looks like this:

```json
{
  "name": "morning",
  "source": "/home/me/src/garden/.dev/morning.toml",
  "format": "toml",
  "description": "Open the editor, start the API, and launch the browser.",
  "args": [
    {"name": "service", "position": 0, "required": true, "help": "Service to launch"},
    {"name": "mode", "position": 1, "required": false, "default": "dev", "choices": ["dev", "prod"]}
  ]
}
```

`source` is the absolute path of the selected file. `format` is `toml` or `tsx`.
`description` is an optional summary, returned as a string or null. Display it as
plain text. Descriptions are literal: they are not expanded using argument values.

For TOML, authors set `description` at the top level. For TSX, authors set it on
`defineWorkflow` alongside `args` and `component`. A description provided only
by a rendered `<Workflow>` prop is unavailable during metadata inspection.

## Build an argument form

Argument records arrive in binding order and include `name`, zero-based
`position`, and `required`. Optional fields include `help` and `choices`.

- Use `help` as explanatory text and `choices` as a selection list when present.
- Required arguments have no `default`; optional arguments include their effective
  string default, including `""` when no default was declared.
- Keep all values as strings. An empty string is a supplied value, not an omitted
  argument, and is still subject to any declared choices.
- Supply values in order. You can omit trailing optional arguments to apply
  defaults. To override a later optional argument, supply values or defaults for
  every preceding position.

An empty `args` array means there are no arguments. A null `args` value means the
arguments have not been inspected; it does **not** mean the workflow takes none.
Layouter remains the final authority on argument validation at launch time.

## Keep discovery separate from TSX execution

`--list` never imports TSX modules. Its TSX records have null `args` and
`description`, allowing a picker to list workflows without executing them.

Explicitly inspecting a selected TSX workflow with `--describe` or `--list-args`
imports its module to read metadata. This requires Node.js 22+ and the Layouter
React runtime, and runs module-level code with the invoking user's permissions.
Only inspect trusted workflows. Do this when the user selects a workflow rather
than evaluating every TSX file during background discovery or shell completion.
The component is not rendered and Layouter does not access the desktop for these
metadata operations. TOML metadata needs neither Node nor a desktop session.

## Use consistent source selection

Use the same `-C` project directory for discovery, inspection, and launch.
Project-local `.dev/` workflows override global workflows of the same name,
including across formats. Global files live under `$XDG_CONFIG_HOME/layouter/`,
or `~/.config/layouter/` when that variable is unset.

Use `--global` throughout the flow to select only global files. Alternatively,
use `--file PATH` to select an exact source; it is mutually exclusive with
`--global`. Relative file paths are resolved against the selected project.
Having both TOML and TSX files for one name in the selected scope is an ambiguity
error unless an explicit file is selected.

An integration can pass a discovered record's `source` to `--file` for both
inspection and launch to keep using that file. Retain the same workflow name,
as well as the project directory. Files can still change between calls; refresh
metadata when relevant files change and handle launch-time validation errors.

## Invoke Layouter as a subprocess

Pass an executable and an argument array, without constructing a shell command.
This preserves spaces and special characters in paths and workflow values.
For a Node-based integration:

```js
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';

const run = promisify(execFile);
const project = '/home/me/src/garden';

async function metadata(options) {
  const { stdout, stderr } = await run('layouter', ['-C', project, ...options], {
    encoding: 'utf8',
    maxBuffer: 4 * 1024 * 1024,
  });
  // Successful TSX evaluation can still emit diagnostics on stderr.
  if (stderr) console.error(stderr);
  const result = JSON.parse(stdout);
  if (result.schema_version !== 1) {
    throw new Error('Unsupported Layouter metadata schema');
  }
  return result;
}

const { workflows } = await metadata(['--list', '--json']);
// After the user selects a workflow:
const selected = workflows.find(workflow => workflow.name === 'morning');
if (!selected) throw new Error('Workflow morning is unavailable');
const source = ['--file', selected.source];
const { workflow } = await metadata([...source, '--describe', '--json', selected.name]);
// Build a form from workflow.args and show workflow.description.
// After the user submits the form, pass its values in positional order:
const values = ['api', 'dev'];
await run('layouter', ['-C', project, ...source, selected.name, ...values]);
```

`execFile` rejects on a nonzero exit code. Catch errors at the UI boundary and
show the process diagnostics rather than attempting to parse stdout as metadata.
Launch output is human-readable progress; `--json` applies only to the three
metadata helpers. For long-running launches, use a streaming subprocess API if
the UI needs to show progress as it arrives.

## Handle validation and errors

Exit code `0` indicates success, `1` a runtime or backend error, `2` a configuration
or command-line error, and `130` interruption. Metadata failures report errors
on stderr and produce no JSON result. No workflows found is currently an error
with code `2`, not a successful empty array; that code also covers other errors,
so retain the diagnostic rather than treating every `2` as an empty picker.

Metadata inspection validates declarations, not the complete desired layout.
Use `--check NAME VALUES...` for full validation without desktop access; TSX
validation imports and renders the workflow. `--dry-run` requires desktop access
and prints a plan. Ordinary launch creates missing elements and preserves
existing arrangements; offer `--sync` as an explicit action when users want to
restore the declared arrangement too.

## Python integrations

When the Python package is installed, use the same metadata directly:

```python
from pathlib import Path
from layouter.metadata import list_workflows, describe_workflow, list_args

project = Path('/home/me/src/garden')
workflows = list_workflows(project)
workflow = describe_workflow(project, 'morning')
arguments = list_args(project, 'morning')
```

These return JSON-compatible lists and dictionaries without the CLI version
envelope. They accept `selected='path/to/file.toml'` or `force_global=True` for
source selection, and raise `ConfigError` for configuration failures. A standalone
Layouter executable does not require consumers to import Python modules; use
its CLI from other runtimes.

See the [workflow reference](workflows.md#metadata-for-launchers-and-integrations)
for the API contract and [React workflows](react-workflows.md) for authoring.
