#!/usr/bin/env python3
"""Build a relocatable Architecture: all Debian package from the full executable."""
import gzip
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
binary = root / 'dist/layouter'
if not binary.is_file():
    raise SystemExit('Build the full zipapp first: python3 scripts/build_zipapp.py')
# Reject accidental packaging of an incomplete core under the full executable name.
from zipfile import ZipFile
with ZipFile(binary) as archive:
    if 'layouter/_react_runtime.zip' not in archive.namelist():
        raise SystemExit('Debian package requires the full executable with the React runtime')
epoch = int(os.environ.get('SOURCE_DATE_EPOCH', '1577836800'))
destination = root / 'dist' / f'layouter_{version}_all.deb'
with tempfile.TemporaryDirectory(prefix='layouter-deb-') as temporary:
    stage = Path(temporary)
    stage.chmod(0o755)
    (stage / 'usr/bin').mkdir(parents=True)
    shutil.copyfile(binary, stage / 'usr/bin/layouter')
    (stage / 'usr/bin/layouter').chmod(0o755)
    docs = stage / 'usr/share/doc/layouter'
    docs.mkdir(parents=True)
    notices = [(root / 'LICENSE').read_text()]
    with ZipFile(root / 'src/layouter/_react_runtime.zip') as runtime:
        for name in sorted(runtime.namelist()):
            if 'node_modules/' in name and Path(name).name.lower().startswith('license'):
                notices.append(f'\n--- {name} ---\n' + runtime.read(name).decode())
    (docs / 'copyright').write_text('\n'.join(notices))
    for source, name in [(root / 'README.md', 'README.md.gz'),
                         (root / 'docs/packaging.md', 'packaging.md.gz')]:
        (docs / name).write_bytes(gzip.compress(source.read_bytes(), mtime=epoch))
    (stage / 'DEBIAN').mkdir()
    size = sum(path.stat().st_size for path in (stage / 'usr').rglob('*') if path.is_file())
    (stage / 'DEBIAN/control').write_text(f'''Package: layouter
Version: {version}
Section: utils
Priority: optional
Architecture: all
Maintainer: Layouter authors <noreply@users.noreply.github.com>
Depends: python3 (>= 3.11)
Suggests: nodejs (>= 22), kitty, sway | i3-wm
Installed-Size: {(size + 1023) // 1024}
Homepage: https://github.com/masterpiece911/layouter
Description: Desired-state development workspaces for i3/Sway and kitty
 Creates missing workspace elements while preserving existing applications.
 Includes the portable React/TSX evaluator. Node.js 22 or newer is required
 only when executing TSX workflows. TOML workflows need no Node.js or npm.
''')
    for path in stage.rglob('*'):
        os.utime(path, (epoch, epoch))
    os.utime(stage, (epoch, epoch))
    subprocess.run(['dpkg-deb', '--threads-max=1', '-Zxz', '-z6', '--root-owner-group', '--build', str(stage), str(destination)],
                   env={**os.environ, 'SOURCE_DATE_EPOCH': str(epoch)}, check=True)
subprocess.run(['dpkg-deb', '--contents', str(destination)], check=True, stdout=subprocess.DEVNULL)
print(destination)
