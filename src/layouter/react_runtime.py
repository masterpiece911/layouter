"""Locate release-owned executable resources without npm or project-local discovery."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
from importlib.resources import files
import io
import json
import os
from pathlib import Path, PurePosixPath
import tempfile
from zipfile import BadZipFile, ZipFile

from . import __version__
from .errors import ConfigError

PROTOCOL_VERSION = 1


@contextmanager
def runtime_runner():
    """An explicit development override, otherwise the runtime shipped with Python.

    A private temporary extraction works identically from wheels and zipapps.
    No shared executable cache, stale versions, npm install, or network access.
    Cleanup runs after the evaluator stops, including failed/interrupted renders.
    """
    override = os.environ.get('LAYOUTER_REACT_RUNTIME')
    if override:
        runner = Path(override).expanduser().resolve()
        if not runner.is_file():
            raise ConfigError(f'React runtime missing: override runner does not exist: {runner}')
        yield runner
        return
    resource = files('layouter').joinpath('_react_runtime.zip')
    if not resource.is_file():
        raise ConfigError('React runtime missing: install the full Layouter release. '
                          'For a source checkout, run npm ci --prefix react then '
                          'python3 scripts/build_react_runtime.py, or set LAYOUTER_REACT_RUNTIME.')
    with tempfile.TemporaryDirectory(prefix='layouter-react-') as folder:
        root = Path(folder)
        try:
            with ZipFile(io.BytesIO(resource.read_bytes())) as archive:
                manifest = json.loads(archive.read('manifest.json'))
                if manifest['version'] != __version__ or manifest['protocol'] != PROTOCOL_VERSION:
                    raise ConfigError('Bundled React runtime version mismatch; reinstall Layouter')
                expected = manifest['files']
                if not isinstance(expected, dict) or set(archive.namelist()) != set(expected) | {'manifest.json'}:
                    raise ConfigError('Bundled React runtime file manifest mismatch; reinstall Layouter')
                for name, digest in expected.items():
                    path = PurePosixPath(name)
                    if path.is_absolute() or '..' in path.parts or '\\' in name:
                        raise ConfigError('Invalid path in bundled React runtime')
                    content = archive.read(name)
                    if hashlib.sha256(content).hexdigest() != digest:
                        raise ConfigError(f'Bundled React runtime integrity failure: {name}')
                    target = root.joinpath(*path.parts)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(content)
        except (OSError, BadZipFile, KeyError, ValueError, TypeError) as exc:
            raise ConfigError(f'Cannot load bundled React runtime: {exc}') from exc
        yield root / 'runner.mjs'
