#!/usr/bin/env python3
"""Build a reproducible unsigned companion archive for testing or Mozilla signing."""
import json
from pathlib import Path
import stat
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

root = Path(__file__).resolve().parents[1]
source = root / 'firefox-extension'
manifest = json.loads((source / 'manifest.json').read_text())
version = manifest['version']
output = root / 'dist' / f'layouter-firefox-{version}-unsigned.xpi'
output.parent.mkdir(exist_ok=True)
with ZipFile(output, 'w') as archive:
    for name in ('background.js', 'badges.js', 'icon.svg', 'manifest.json', 'protocol.js'):
        info = ZipInfo(name, date_time=(2020, 1, 1, 0, 0, 0))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o644) << 16
        info.compress_type = ZIP_DEFLATED
        archive.writestr(info, (source / name).read_bytes())
print(output)
