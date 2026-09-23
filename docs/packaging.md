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

The prototype depended on a checkout's `node_modules` and an explicit runner
path. That is useful for development but unsuitable as the default installation.
A separate npm companion would add another installation and version lifecycle.
Bundling native esbuild would require architecture-specific releases. The chosen
runtime uses esbuild's portable WebAssembly distribution instead.

Before packaging, five fresh CLI processes on the development Linux container
measured a median of **174 ms** with native esbuild and **708 ms** with
esbuild-wasm for the equivalent TSX fixture. The runtime archive is approximately
**4.23 MB compressed**. The portable path accepts roughly half a second of extra
startup cost in exchange for one cross-architecture distribution with no npm
setup. These measurements are local observations, not performance guarantees.
Including archive extraction and integrity checks, the actual full zipapp
measured **806 ms** median for TSX and **107 ms** for TOML; the full executable is
about **4.16 MB**, versus **48 kB** for the core build. See
`scripts/benchmark_packaging.py` for repeatable installed-artifact timing.

Native esbuild remains the source-development runner, selected explicitly with
`LAYOUTER_REACT_RUNTIME=/absolute/path/to/react/runner.mjs`. It is not a separately
supported release family. We can revisit native builds if measured desktop usage
shows the portable startup cost is a problem. Migrating the Python core to Node
is unnecessary for this packaging design.

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
sandbox it. The project-local trust policy is a separate feature/release decision.

## Building releases

Maintainers need Python 3.11+, Node 22+, npm, Python's `build` package and the
setuptools/wheel build dependencies, plus `dpkg-deb` for the Debian artifact.
Build dependencies are distinct from end-user dependencies. Start from a clean
checkout and use the committed npm lockfile:

```sh
python3 -m pip install -r requirements-build.txt
python3 scripts/build_release.py
```

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
