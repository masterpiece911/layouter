#!/usr/bin/env python3
"""Exercise artifacts away from the checkout, without npm, overrides or network installs."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / 'pyproject.toml').read_text())['project']['version']
NODE = shutil.which('node')
if not NODE:
    raise SystemExit('Release verification requires Node.js >=22')


def run(argv, cwd, env, expected=0):
    result = subprocess.run([str(arg) for arg in argv], cwd=cwd, env=env, text=True,
                            capture_output=True, timeout=60)
    if result.returncode != expected:
        raise RuntimeError(f'{argv}: expected {expected}, got {result.returncode}\n{result.stdout}\n{result.stderr}')
    return result


with tempfile.TemporaryDirectory(prefix='layouter-release-test-') as temporary:
    stage = Path(temporary)
    bin_dir = stage / 'bin'
    bin_dir.mkdir()
    (bin_dir / 'python3').symlink_to(sys.executable)
    (bin_dir / 'node').symlink_to(NODE)
    home = stage / 'home'
    home.mkdir()
    tmp = stage / 'tmp'
    tmp.mkdir()
    project = stage / 'project with spaces'
    (project / '.dev').mkdir(parents=True)
    env = {'PATH': str(bin_dir), 'HOME': str(home), 'XDG_CONFIG_HOME': str(home / '.config'),
           'TMPDIR': str(tmp), 'LANG': 'C.UTF-8'}
    for suffix in ('toml', 'tsx'):
        shutil.copyfile(ROOT / f'tests/fixtures/react-parity.{suffix}', project / '.dev' / f'parity.{suffix}')
    # Separate names for listing; explicit files exercise same-scope ambiguity bypass.
    (project / '.dev/parity.tsx').rename(project / '.dev/react.tsx')
    full = stage / 'layouter'
    shutil.copyfile(ROOT / 'dist/layouter', full)
    full.chmod(0o755)

    def smoke(command):
        for suffix, name in [('toml', 'parity'), ('tsx', 'react')]:
            result = run([*command, '--check', '--file', f'.dev/{name}.{suffix}', 'dev', 'prod'], project, env)
            assert 'Valid: dev' in result.stdout, result
        assert 'arguments not evaluated' in run([*command, '--list'], project, env).stdout
        assert not list(tmp.glob('layouter-react-*')), 'Runtime temporary extraction leaked'

    smoke([full])
    print('PASS: relocated full zipapp; TOML/TSX, no npm or checkout imports')
    # TOML and discovery cannot require Node, even when a runtime is bundled.
    (bin_dir / 'node').unlink()
    run([full, '--check', '--file', '.dev/parity.toml'], project, env)
    run([full, '--list'], project, env)
    missing = run([full, '--check', '--file', '.dev/react.tsx'], project, env, 2)
    assert 'Node.js 22 or newer' in missing.stderr
    (bin_dir / 'node').symlink_to(NODE)
    print('PASS: TOML/list without Node; actionable TSX error')
    core = stage / 'layouter-core'
    shutil.copyfile(ROOT / 'dist/layouter-core', core)
    core.chmod(0o755)
    run([core, '--check', '--file', '.dev/parity.toml'], project, env)
    assert 'full Layouter release' in run([core, '--check', '--file', '.dev/react.tsx'], project, env, 2).stderr
    print('PASS: explicit core artifact and missing-runtime diagnostic')
    wheel = ROOT / 'dist' / f'layouter-{VERSION}-py3-none-any.whl'
    with ZipFile(wheel) as archive:
        assert 'layouter/_react_runtime.zip' in archive.namelist()
    virtual = stage / 'venv'
    venv.EnvBuilder(with_pip=True).create(virtual)
    python = virtual / 'bin/python'
    run([python, '-m', 'pip', 'install', '--no-index', '--no-deps', wheel], stage, env)
    smoke([python, '-I', '-m', 'layouter'])
    print('PASS: wheel installed offline into an isolated virtual environment')
    deb = ROOT / 'dist' / f'layouter_{VERSION}_all.deb'
    dpkg = shutil.which('dpkg-deb')
    unpacked = stage / 'deb'
    run([dpkg, '-x', deb, unpacked], stage, dict(os.environ))
    smoke([unpacked / 'usr/bin/layouter'])
    print('PASS: Debian installed file layout; TOML/TSX')
