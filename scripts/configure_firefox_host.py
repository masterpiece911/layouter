#!/usr/bin/env python3
"""Generate an opt-in native host wrapper/manifest in an explicitly selected directory.

Copy the JSON into the browser's documented native-messaging-hosts directory.
The wrapper and chosen Layouter executable must remain at their absolute paths.
"""
import argparse
import json
from pathlib import Path
import shlex

p = argparse.ArgumentParser(description=__doc__)
p.add_argument('--layouter', required=True, type=Path, help='installed Layouter executable (zipapp or console script)')
p.add_argument('--output', required=True, type=Path, help='persistent output directory for the wrapper and manifest')
args = p.parse_args()
executable = args.layouter.expanduser().resolve()
if not executable.is_file():
    p.error(f'Layouter executable does not exist: {executable}')
output = args.output.expanduser().resolve()
output.mkdir(parents=True, exist_ok=True)
host = output / 'layouter-firefox-host'
host.write_text(f'#!/bin/sh\nexec {shlex.quote(str(executable))} --firefox-host "$@"\n')
host.chmod(0o755)
manifest = output / 'org.layouter.firefox.json'
manifest.write_text(json.dumps({
    'name': 'org.layouter.firefox', 'description': 'Layouter Firefox companion bridge',
    'path': str(host), 'type': 'stdio', 'allowed_extensions': ['firefox@layouter.dev'],
}, indent=2) + '\n')
print(manifest)
