# React/TSX workflows

TSX is an optional programmable frontend. It renders a readable Layouter document,
which Python validates and resolves into the same `Workflow` used by TOML. The
existing reconciler still handles all i3/Sway and kitty operations.

## Try it from a checkout

Full releases include the React runtime. Install Node.js 22 or newer to use
TSX; npm and runtime environment variables are not needed. TOML still needs only
Python. See [packaging](packaging.md) for zipapp, wheel, and Debian installation.

To prepare a git checkout:

```sh
npm ci --prefix react
python3 scripts/build_react_runtime.py
PYTHONPATH=src python3 -m layouter --check --file examples/morning.tsx morning garden
```

Developers working on the evaluator can instead build with `npm run build
--prefix react` and explicitly set `LAYOUTER_REACT_RUNTIME` to the absolute
`react/runner.mjs` path. This override uses the native esbuild development
runtime. Normal installations use the versioned portable runtime shipped with
Python and require no override.

To launch, place a workflow in `.dev/morning.tsx` or
`~/.config/layouter/morning.tsx` and run `layouter morning garden`. Local files
continue to override global files as a whole, including across formats. If both
`morning.toml` and `morning.tsx` exist in the selected scope, Layouter reports ambiguity;
`--file` selects one exact file. A shadowed global scope is not evaluated.

`--list` shows TSX filenames without importing their modules or inspecting their
argument declarations. Do not evaluate TSX for generic shell completion.

## Authoring

```tsx
import {
  defineWorkflow, Workflow, Workspace, Window, Kitty, Pane,
  useArg, useScreen,
} from '@layouter/react';

function Tools() {
  const mode = useArg('environment');
  return <Kitty name="tools" layout="tall">
    <Pane name="server" command={['npm', 'run', mode]} />
    <Pane name="shell" />
  </Kitty>;
}

export default defineWorkflow({
  args: {
    environment: {
      position: 0, default: 'dev', choices: ['dev', 'prod'],
      help: 'Target environment',
    },
  },
  component({ args }) {
    const { width } = useScreen();
    return <Workflow session={`work-${args.environment}`} focus="code">
      <Workspace name="code" number={2}
                 layout={width >= 1920 ? 'splith' : 'splitv'}>
        <Window name="editor" command={['zed', '{project}']} />
        <Tools />
        <Kitty name="scratch" floating position="center" width={1000} height={700}>
          <Pane name="shell" />
        </Kitty>
      </Workspace>
    </Workflow>;
  },
});
```

Argument declarations belong to `defineWorkflow`, before rendering. Python
applies its existing `position`, `required`, `default`, `choices`, and `help`
rules exactly once. The component gets the bound string values in `args`;
nested components can use `useWorkflowArgs()` or `useArg(name)`. Choice literals
are inferred for the top-level component's argument type. Hook lookup reports
an unknown argument name at runtime.

Ordinary function components, fragments, arrays, conditionals, React context,
and synchronous hooks work. Use normal JavaScript composition, without a
Layouter-specific conditional language. This is one committed render per
invocation, not an interactive app: asynchronous loading, Suspense-driven
workflows, and effect-driven subsequent layouts are outside the workflow model.
Keep rendering pure; use the supplied snapshot to decide desired state.

The host primitives are `Workflow`, `Workspace`, `Container`, `Window`, `Kitty`,
`Tab`, and `Pane`. Their props follow the readable TOML fields; `syncDisplays`
is the spelling of `sync_displays` in TSX. Nest children instead of assigning
`workspace`, `window`, `container`, `kitty`, `tab`, or `pane` arrays. A workflow
must render exactly one `Workflow` root. Text children and invalid parent/child
relationships produce errors.

Give every structural `Container` a name. React's `key` controls React identity;
it never supplies a Layouter identity. The compiler assigns tiled child `order`
from JSX order, including when windows, containers, and kitty instances are
interleaved. Floating windows/kitty must be direct workspace children. Use
`floating` plus either named `position` or pixel `x`/`y`; Python retains the
final geometry, matcher, path, identity, and focus validation.

Strings still pass through Python's existing interpolation rules, including
`{project}`, `{mode:q}`, and `{mode:url}`. As in TOML, literal braces in produced
strings must be doubled. JavaScript interpolation does not disable that later
Python expansion.

For editor types, install the optional release authoring tarball (`layouter-react-VERSION.tgz`)
as a dev dependency, or map `@layouter/react` to its `react/dist/index.d.ts`
with your TypeScript `paths` configuration. The evaluator resolves this package
and React to its own pinned runtime so imported components share one React and
one Layouter context. Relative TS/TSX imports and statically bundled project
packages are supported. The bundler does not preserve a workflow's
original `import.meta.url`; use `useLayouter().project` or component props for
project-relative paths. Native addons and arbitrary dynamic imports are not a
supported packaging contract yet.

## Display snapshot and CLI modes

`useOutputs()` returns the output snapshot, including inactive outputs.
`usePrimaryOutput()` returns the first active primary output, falling back to
the first active output. `useOutput(name)` finds an active named output.
`useScreen(name?)` provides `output`, logical `width`/`height`, and `scale`, with
unknown defaults of `undefined`, `0`, `0`, and `1`. Dimensions come directly
from the compositor's output rectangle; the scale is not applied a second time.

Normal runs read one `GET_OUTPUTS` snapshot before rendering. The evaluation
context contains `project`, `projectName`, `workflow`, bound `args`, and outputs;
it never contains the live window tree. There is no subscription or daemon.

`--check` never opens a compositor connection. It renders with no outputs and
therefore validates only the fallback branch. Test responsive branches with
explicit snapshots, as the renderer tests do. `--dry-run`, `--sync`,
`--sync-displays`, and `--no-focus` reach the existing reconciler unchanged.

`--save-layout` rejects TSX before executing it: arbitrary program source cannot
be safely round-tripped. Use a TOML workflow for saving live arrangements.
`--capture` continues to generate TOML.

## Executable configuration

**TOML is data. TSX is executable configuration.** Selecting a TSX workflow runs
its module with your user permissions, including in `--check` and `--dry-run`.
Only use trusted workflows and imports. The evaluator is not a security sandbox.
Selecting a workflow, including the default workflow, authorizes execution.
Review project-local workflows before invoking them.

Listing does not execute TSX. The runtime itself has no compositor integration;
its only product is JSON desired state. Python remains the semantic validator
and reconciler.

## Protocol and maintenance

One Node process reads newline-delimited JSON on stdin. Python sends a
`metadata` request with the absolute `workflow` path, validates/binds the returned
`args`, then sends a `render` request with that same path and `context`. The
module imports once. Every request and response includes `"protocol": 1`.
Responses are `{ "ok": true, "result": {...} }` or
`{ "ok": false, "kind": "...", "message": "..." }`. Console diagnostics go to
stderr. Direct writes to stdout violate the protocol. Each response has a
30-second deadline and 16 MiB limit; Python closes the process on success,
validation failure, interruption, or timeout.

Runtime absence, transformation, module execution, React rendering, protocol,
and Python validation errors have separate diagnostics. Rendering uses a small
in-memory host renderer; all `react-reconciler` imports and unstable host API
calls are isolated in `react/src/renderer.tsx`. React 19.3.0 and reconciler
0.34.0 are pinned together, with contract tests and a lockfile. The host adapter
is deliberately isolated from the typed workflow API because the reconciler
host API is experimental.

```sh
npm test --prefix react
PYTHONPATH=src python3 -m unittest discover -s tests
```

Python tests run without the optional runtime and skip its end-to-end cases.
CI also installs/builds it on Node 22/24 and runs renderer, type, parity,
protocol, discovery, CLI, and zipapp tests. Existing compositor smoke tests still
need their desktop prerequisites.

## Release packaging

The packaging decision and measured tradeoffs now live in
[Packaging and releases](packaging.md). Releases bundle a portable WebAssembly
transformer with React, so end users do not install npm dependencies. The Python
core, normalizer, reconciler, and JSON evaluator boundary are unchanged.
