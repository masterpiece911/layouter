# Packaging and releases

Layouter ships its Python implementation and a **bundled portable React runtime**
together. Users install one artifact; they do not run npm, install React, point
at a checkout, or set a runtime environment variable. Python 3.11+ runs the CLI.
Node.js 22+ is additionally required only for TSX workflows. i3/Sway and kitty
remain desktop requirements when actually launching applications.

## Distribution formats

| Artifact | Installation | Contents |
| --- | --- | --- |
| `layouter` | Copy to `~/.local/bin/layouter` and make executable | Python zipapp plus the complete portable TSX runtime |
| `layouter-VERSION-py3-none-any.whl` | `pipx install ./layouter-VERSION-py3-none-any.whl` | The same implementation and runtime, with a generated CLI entry point |
| `layouter_VERSION_all.deb` | `sudo apt install ./layouter_VERSION_all.deb` | Full executable in `/usr/bin`, documentation in `/usr/share/doc/layouter` |
| `layouter-VERSION.tar.gz` | Standard Python source distribution | Prebuilt runtime included; installing/building a wheel needs no npm |
| `layouter-core` | Optional small TOML-only executable | Python implementation without the runtime; TSX reports an actionable error |
| `layouter-react-VERSION.tgz` | Optional npm dependency for workflow authors | Type declarations and authoring API for editor resolution; not needed to execute workflows |

The `.deb` is `Architecture: all`: its JavaScript and WebAssembly runtime is the
same on amd64 and arm64. Node is a `Suggests`, not a `Depends`, so TOML users are
not forced to install it. If the distribution's Node is too old, install a
supported Node separately before using TSX. Package installation never installs
npm dependencies, downloads code, or executes workflow modules. No maintainer
scripts are needed. Installing the zipapp as a Debian payload also keeps the
package independent of distro-specific Python site-package paths.

The optional authoring tarball can be installed in a project's dev dependencies
with `npm install --save-dev /path/to/layouter-react-VERSION.tgz`. It supplies
`@layouter/react` imports and types to the editor. The evaluator always resolves
React and the Layouter API to its own shipped versions, so authoring dependencies
do not accidentally create multiple React/context instances at runtime.

## Why bundle WebAssembly?

The runtime bundles esbuild's portable WebAssembly transformer so one release
can run across supported architectures. A separate npm companion would require
another installation lifecycle; native esbuild would require platform-specific
artifacts. WebAssembly trades startup cost and archive size for a self-contained
runtime with no end-user npm setup.

For repeatable startup and artifact-size measurements, build a release and run:

```sh
python3 scripts/benchmark_packaging.py --runs 5
```

The benchmark compares fresh TOML, portable TSX, and native-override CLI
processes. Compare results on the same machine and toolchain.

Native esbuild is available for source development through
`LAYOUTER_REACT_RUNTIME=/absolute/path/to/react/runner.mjs`. Normal installations
use the bundled runtime.

## Runtime lifecycle

The versioned `_react_runtime.zip` resource is shipped inside the Python package,
including when that package is inside a zipapp. Python extracts it to a private
temporary directory for the selected TSX invocation, checks its version and file
hashes, runs Node, then removes it. This avoids shared executable caches, stale
runtime upgrades, writable installation directories, and concurrent extraction
races. TOML commands and workflow listing never extract or start it.

The archive includes React, reconciler, scheduler, esbuild-wasm, their license
files, and a manifest with dependency versions, npm integrity identifiers, and
SHA-256 file hashes. The manifest detects damaged or mismatched assets; it is
not a signature or a substitute for trusting the release. The Python/JS protocol
has an explicit version. An incompatible external override fails clearly.

TSX remains executable configuration, including in `--check`; packaging does not
sandbox it. Selecting a workflow authorizes execution; review project-local
workflows before running them.

## Building releases

For a local checkout installation, `make install PYTHON=/path/to/python3.11`
builds a launcher pinned to the selected interpreter. It supports `PREFIX`,
`BINDIR`, and `DESTDIR`; the destination does not change the interpreter path.
Keep that Python installation available for as long as you use the launcher.
Portable release zipapps built with `make zipapp` or `scripts/build_zipapp.py`
continue to use `python3` from `PATH`, which must be Python 3.11 or newer.

Maintainers need Python 3.11+, Node 22+, npm, Python's `build` package and the
setuptools/wheel build dependencies, plus `dpkg-deb` for the Debian artifact.
Build dependencies are distinct from end-user dependencies. Start from a clean
checkout and use the committed npm lockfile:

```sh
make release
```

`make release` creates `.venv/build` and installs `requirements-build.txt` there
without modifying system Python. Override `PYTHON` or `BUILD_VENV` when needed.
Node.js, npm, and `dpkg-deb` must already be available.

The release script installs locked npm build dependencies, runs renderer/type
checks, builds the portable runtime, runs Python tests, builds the full/core
zipapps, wheel, sdist, Debian and authoring packages, then verifies the artifacts
away from the checkout. `SHA256SUMS` contains the release checksums. It does not
publish anything. CI runs the same pipeline and uploads artifacts for review.

For individual builds:

```sh
npm ci --prefix react
python3 scripts/build_react_runtime.py
python3 scripts/build_zipapp.py
python3 scripts/build_zipapp.py --core
python3 -m build
python3 scripts/build_deb.py
python3 scripts/build_editor_package.py
python3 scripts/verify_release.py
```

A Python wheel/sdist build fails if its generated runtime is missing or from a
different Layouter version; it never silently ships a broken TSX installation or
runs npm during a pip build. The release sdist already contains that resource.
Building from a git checkout therefore requires the explicit maintainer runtime
build above. The core zipapp can still be built using Python alone.

Runtime archives, zipapps, and authoring tarballs use sorted entries, fixed
metadata and permissions. Debian builds honor `SOURCE_DATE_EPOCH` (default:
2020-01-01). The release pipeline sets this for Python packaging too. This makes
repeated builds from the same inputs reproducible within a fixed toolchain;
cross-toolchain byte identity is not promised. Dependency upgrades require
updating the lockfile, rebuilding, and rerunning all gates.

## Verification boundary

`verify_release.py` copies the executable to a new directory, clears runtime
and Python path overrides, supplies only Python/Node on `PATH`, and checks TOML
and TSX fixtures from a project whose path contains spaces. It also checks TOML
and listing with Node absent, the core artifact's missing-runtime error, an
offline wheel installation in a new virtual environment, the extracted Debian
installed file layout, and temporary runtime cleanup. No npm or checkout is
needed by these executions. Debian testing validates its payload and layout;
it does not change the host package database.

CI is configured to cover Node 22/24, Python 3.11–3.14, and full artifacts on Linux x64 and arm64.
Live compositor smoke tests remain a separate desktop gate. Release artifacts
are built for review; no registry upload or GitHub release is automatic.

References: [esbuild installation and portability](https://esbuild.github.io/getting-started/),
[setuptools package data](https://setuptools.pypa.io/en/stable/userguide/datafiles.html),
[Node TypeScript limitations](https://nodejs.org/api/typescript.html).
