#!/usr/bin/env python3
"""Build the portable release executable, or an explicitly TOML-only core."""
import argparse
from pathlib import Path
import shlex
import stat
import sys
import tomllib
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

root = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--core', action='store_true', help='omit the optional React runtime; no npm build needed')
p.add_argument('--output', type=Path, help='output executable path')
p.add_argument('--local-python', action='store_true',
               help='pin the launcher to this build interpreter for a local installation')
args = p.parse_args()
runtime = root / 'src/layouter/_react_runtime.zip'
if not args.core and not runtime.is_file():
    p.error('Build the runtime first: npm ci --prefix react && python3 scripts/build_react_runtime.py; '
            'or explicitly use --core for TOML only')
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
if not args.core:
    import json
    with ZipFile(runtime) as archive:
        if json.loads(archive.read('manifest.json'))['version'] != version:
            p.error('Runtime version is stale; rebuild it')
destination = args.output or root / 'dist' / ('layouter-core' if args.core else 'layouter')
destination.parent.mkdir(parents=True, exist_ok=True)
if args.local_python:
    # A shell launcher supports interpreter paths containing spaces and avoids
    # kernel shebang length limits. Python reads the ZIP after this prefix.
    launcher = f'#!/bin/sh\nexec {shlex.quote(sys.executable)} "$0" "$@"\n'
else:
    launcher = '#!/usr/bin/env python3\n'
destination.write_bytes(launcher.encode('utf-8'))
with ZipFile(destination, 'a', compression=ZIP_DEFLATED, compresslevel=9) as archive:
    paths = [(path.relative_to(root / 'src').as_posix(), path) for path in (root / 'src').rglob('*')
             if path.is_file() and '__pycache__' not in path.parts and path.suffix != '.pyc'
             and not any(part.endswith('.egg-info') for part in path.parts)
             and not (args.core and path == runtime)]
    paths.append(('LICENSE', root / 'LICENSE'))
    for name, path in sorted(paths):
        info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, path.read_bytes(), compresslevel=9)
destination.chmod(0o755)
print(destination)
