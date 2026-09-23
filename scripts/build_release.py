#!/usr/bin/env python3
"""Build and verify all reviewable release artifacts. Never publish or install globally."""
import gzip
import hashlib
import io
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tomllib

root = Path(__file__).resolve().parents[1]
version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
env = {**os.environ, 'SOURCE_DATE_EPOCH': os.environ.get('SOURCE_DATE_EPOCH', '1577836800')}
# Release checks must exercise the bundled runtime, not an inherited developer override.
env.pop('LAYOUTER_REACT_RUNTIME', None)


def run(*args, extra_env=None):
    subprocess.run([str(arg) for arg in args], cwd=root, env={**env, **(extra_env or {})}, check=True)


def script(name, *args):
    run(sys.executable, root / 'scripts' / name, *args)


run('npm', 'ci', '--prefix', 'react')
run('npm', 'test', '--prefix', 'react')
script('build_react_runtime.py')
run(sys.executable, '-m', 'unittest', 'discover', '-s', 'tests', extra_env={'PYTHONPATH': 'src'})
script('build_zipapp.py')
script('build_zipapp.py', '--core')
run(sys.executable, '-m', 'build', '--no-isolation')
# SOURCE_DATE_EPOCH governs wheels; normalize the sdist's tar/gzip metadata too.
source = root / 'dist' / f'layouter-{version}.tar.gz'
stream = io.BytesIO()
with tarfile.open(source, 'r:gz') as original, tarfile.open(fileobj=stream, mode='w', format=tarfile.PAX_FORMAT) as archive:
    for member in sorted(original.getmembers(), key=lambda member: member.name):
        member.mtime = int(env['SOURCE_DATE_EPOCH'])
        member.uid = member.gid = 0
        member.uname = member.gname = ''
        member.pax_headers = {}
        member.mode = 0o755 if member.isdir() else 0o644
        archive.addfile(member, original.extractfile(member) if member.isfile() else None)
source.write_bytes(gzip.compress(stream.getvalue(), mtime=int(env['SOURCE_DATE_EPOCH'])))
script('build_deb.py')
script('build_editor_package.py')
script('verify_release.py')
names = ['layouter', 'layouter-core', f'layouter-{version}-py3-none-any.whl',
         source.name, f'layouter_{version}_all.deb', f'layouter-react-{version}.tgz']
lines = [f"{hashlib.sha256((root / 'dist' / name).read_bytes()).hexdigest()}  {name}\n" for name in names]
(root / 'dist/SHA256SUMS').write_text(''.join(lines))
print('Verified release artifacts and SHA256SUMS are in dist/; nothing has been published.')
