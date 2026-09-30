#!/usr/bin/env python3
"""Sign a verified release XPI through Mozilla; never used by local builds/tests."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
from zipfile import ZipFile

FILES = {'manifest.json', 'protocol.js', 'background.js', 'badges.js', 'icon.svg'}


def payload(path):
    with ZipFile(path) as archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise ValueError('Duplicate entries in extension archive')
        return {name: archive.read(name) for name in names if not name.endswith('/')}


def verify_signed_payload(original, path):
    downloaded = payload(path)
    # Packaging guard; Firefox verifies the cryptographic signature on install.
    signatures = {name for name in downloaded if name.startswith('META-INF/')}
    if not ({'META-INF/mozilla.rsa', 'META-INF/cose.sig'} & signatures):
        raise ValueError('Downloaded extension has no Mozilla signature metadata')
    contents = {k: v for k, v in downloaded.items() if k not in signatures}
    if set(contents) != set(original):
        raise ValueError('Signed extension payload differs from the tested unsigned artifact')
    for name, expected in original.items():
        actual = contents[name]
        if name == 'manifest.json':
            # AMO reserializes JSON (including Unicode escapes and whitespace).
            actual = json.dumps(json.loads(actual), sort_keys=True)
            expected = json.dumps(json.loads(expected), sort_keys=True)
        if actual != expected:
            raise ValueError(f'Signed extension payload differs from the tested unsigned artifact: {name}')


def sign(root, tag, web_ext, signed_xpi=None):
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    manifest = json.loads((root / 'firefox-extension/manifest.json').read_text())
    if tag != f'v{version}' or manifest['version'] != version:
        raise ValueError('Tag, Python package version and Firefox manifest version must match')
    dist = root / 'dist'
    unsigned = dist / f'layouter-firefox-{version}-unsigned.xpi'
    sums = dist / 'SHA256SUMS'
    expected = f'{hashlib.sha256(unsigned.read_bytes()).hexdigest()}  {unsigned.name}'
    if expected not in sums.read_text().splitlines():
        raise ValueError('Unsigned extension checksum does not match release checksums')
    original = payload(unsigned)
    if set(original) != FILES or json.loads(original['manifest.json']) != manifest:
        raise ValueError('Unsigned extension does not match the release manifest/payload layout')
    if manifest['browser_specific_settings']['gecko']['id'] != 'firefox@layouter.dev':
        raise ValueError('Unexpected extension ID')
    signed = dist / f'layouter-firefox-{version}.xpi'
    if signed.exists():
        raise ValueError('Signed output already exists; preserve it rather than submitting again')
    if signed_xpi is not None:
        verify_signed_payload(original, signed_xpi)
        shutil.copyfile(signed_xpi, signed)
    else:
        for key in ('WEB_EXT_API_KEY', 'WEB_EXT_API_SECRET'):
            if not os.environ.get(key):
                raise ValueError(f'Missing {key}; configure the AMO GitHub Actions secrets')
        with tempfile.TemporaryDirectory(prefix='layouter-sign-') as folder:
            stage = Path(folder)
            source, artifacts = stage / 'source', stage / 'signed'
            source.mkdir()
            for name, content in original.items():
                (source / name).write_bytes(content)
            subprocess.run([web_ext, 'sign', '--channel=unlisted', '--no-input',
                            '--no-config-discovery', '--source-dir', str(source),
                            '--artifacts-dir', str(artifacts), '--approval-timeout=900000'],
                           cwd=stage, check=True)
            results = list(artifacts.glob('*.xpi'))
            if len(results) != 1:
                raise ValueError('Mozilla did not return exactly one signed XPI; check AMO review status')
            verify_signed_payload(original, results[0])
            shutil.copyfile(results[0], signed)
    lines = [line for line in sums.read_text().splitlines()
             if not line.endswith(f'  {signed.name}')]
    lines.append(f'{hashlib.sha256(signed.read_bytes()).hexdigest()}  {signed.name}')
    sums.write_text('\n'.join(lines) + '\n')
    return signed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--web-ext', default='web-ext')
    parser.add_argument('--signed-xpi', type=Path,
                        help='Verify and import an already approved XPI without submitting to Mozilla')
    args = parser.parse_args()
    try:
        print(sign(Path(__file__).resolve().parents[1], args.tag, args.web_ext, args.signed_xpi))
    except (ValueError, OSError, subprocess.CalledProcessError) as error:
        parser.exit(1, f'Firefox signing failed: {error}\n')


if __name__ == '__main__':
    main()
