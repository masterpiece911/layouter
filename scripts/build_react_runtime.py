#!/usr/bin/env python3
"""Create the portable, versioned runtime resource. Build tools are never installed at runtime."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import tomllib
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

ROOT = Path(__file__).resolve().parents[1]
REACT = ROOT / 'react'
DESTINATION = ROOT / 'src/layouter/_react_runtime.zip'
PACKAGES = ('react', 'react-reconciler', 'scheduler', 'esbuild-wasm')


def main():
    lock = json.loads((REACT / 'package-lock.json').read_text())
    files = {}
    inventory = []
    for name in PACKAGES:
        directory = REACT / 'node_modules' / name
        if not (directory / 'package.json').is_file():
            raise SystemExit('Missing build dependencies: run npm ci --prefix react first')
        package = json.loads((directory / 'package.json').read_text())
        entry = lock['packages'][f'node_modules/{name}']
        if package['version'] != entry['version']:
            raise SystemExit(f'{name} differs from package-lock.json; run npm ci --prefix react')
        inventory.append({'name': name, 'version': package['version'],
                          'license': package['license'], 'integrity': entry['integrity']})
        for path in sorted(directory.rglob('*')):
            if path.is_file():
                if path.is_symlink():
                    raise SystemExit(f'Unexpected symlink in runtime package: {path}')
                files[path.relative_to(REACT).as_posix()] = path.read_bytes()
    subprocess.run(['npm', 'run', 'build', '--prefix', str(REACT)], check=True)
    for path in sorted((REACT / 'dist').rglob('*')):
        if path.is_file():
            files[path.relative_to(REACT).as_posix()] = path.read_bytes()
    # One evaluator source, with a build-time transformer selection.
    runner = (REACT / 'runner.mjs').read_text().replace("import('esbuild')", "import('esbuild-wasm')")
    files['runner.mjs'] = runner.encode()
    files['LICENSE'] = (ROOT / 'LICENSE').read_bytes()
    files['package.json'] = b'{"private":true,"type":"module"}\n'
    version = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
    manifest = {'version': version, 'protocol': 1, 'node': '>=22', 'transformer': 'esbuild-wasm',
                'packages': inventory,
                'files': {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}}
    files['manifest.json'] = (json.dumps(manifest, sort_keys=True, indent=2) + '\n').encode()
    # Fixed timestamps, permissions, ordering and compression make identical inputs reproducible.
    with ZipFile(DESTINATION, 'w', compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(files.items()):
            info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
            info.compress_type = ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = 0o100644 << 16
            archive.writestr(info, data, compresslevel=9)
    print(f'{DESTINATION} ({DESTINATION.stat().st_size:,} bytes)')


if __name__ == '__main__':
    main()
