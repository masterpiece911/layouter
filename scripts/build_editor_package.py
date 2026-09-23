#!/usr/bin/env python3
"""Optional npm tarball for editor resolution; runtime evaluation does not require it."""
import io
import json
from pathlib import Path
import tarfile
import tomllib
import gzip

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
package = json.loads((root / 'react/package.json').read_text())
files = {'package/LICENSE': (root / 'LICENSE').read_bytes()}
for stem in ('index', 'types', 'context', 'primitives'):
    for suffix in ('.js', '.d.ts'):
        path = root / 'react/dist' / (stem + suffix)
        files['package/dist/' + path.name] = path.read_bytes()
files['package/package.json'] = (json.dumps({
    'name': '@layouter/react', 'version': version, 'type': 'module', 'license': 'MIT',
    'description': 'Authoring API for Layouter React workflows',
    'exports': {'.': {'types': './dist/index.d.ts', 'default': './dist/index.js'}},
    'peerDependencies': {'react': package['dependencies']['react']},
    'dependencies': {'@types/react': package['devDependencies']['@types/react']},
}, indent=2) + '\n').encode()
stream = io.BytesIO()
with tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
    for name, content in sorted(files.items()):
        info = tarfile.TarInfo(name)
        info.size = len(content)
        info.mode = 0o644
        info.mtime = 1577836800
        archive.addfile(info, io.BytesIO(content))
destination = root / 'dist' / f'layouter-react-{version}.tgz'
destination.parent.mkdir(exist_ok=True)
destination.write_bytes(gzip.compress(stream.getvalue(), mtime=1577836800))
print(destination)
