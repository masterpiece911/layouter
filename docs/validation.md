# Testing and validation

## Python tests

Run from a source checkout with Python 3.11 or newer. The suite needs no
third-party Python test packages or running desktop. Allow local Unix socket
binding and subprocess execution for the backend and packaging fixtures.

```sh
make test
make check
```

Select an interpreter with `make test PYTHON=python3.12`. To run one test module:

```sh
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_backends.py' -v
```

The packaging test rebuilds `dist/layouter-core`. If the execution environment
blocks sockets or subprocesses, rerun in an environment that permits them.

## React and TSX integration

Install Node.js 22+ and npm, then prepare the evaluator and run both suites:

```sh
make deps
npm test --prefix react
python3 scripts/build_react_runtime.py
make test
```

The npm command runs renderer and TypeScript checks. Building the runtime before
the Python suite enables the TSX integration cases; those cases are skipped
when their runtime prerequisites are absent. Include them when changing source
discovery, argument binding, rendering, or the evaluator protocol.

Use `--check` to validate a workflow without a compositor connection:

```sh
PYTHONPATH=src python3 -m layouter --check --file examples/morning.toml morning garden
PYTHONPATH=src python3 -m layouter --check --file examples/responsive.tsx morning garden
```

TSX validation executes the workflow with an empty display snapshot. Exercise
conditional display branches with explicit output snapshots in renderer tests.

## Release artifacts

With Python venv support, Node.js 22+, npm, and `dpkg-deb` available, run:

```sh
make release
```

This installs build dependencies, runs the automated suites, builds the release
artifacts, and checks their behavior outside the checkout. To repeat artifact
verification after a build:

```sh
python3 scripts/verify_release.py
```

See [packaging](packaging.md) for artifact formats and verification boundaries.

## Live desktop checks

Follow the [smoke-test procedure](smoke-test.md) in both i3 and Sway sessions.
Check application discovery, pane creation, focus, preservation of existing
work, and explicit synchronization against the live desktop. Use these checks
alongside the automated backend fixtures when changing desktop operations.
