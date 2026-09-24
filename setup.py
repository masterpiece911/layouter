"""Guard release completeness; npm is a maintainer build dependency, never a pip hook."""
from pathlib import Path
import json
import tomllib
from zipfile import ZipFile
from setuptools import setup
from setuptools.command.build_py import build_py
from setuptools.command.sdist import sdist


def check_runtime():
    root = Path(__file__).parent
    runtime = root / 'src/layouter/_react_runtime.zip'
    if not runtime.is_file():
        raise RuntimeError('Missing bundled React runtime. From a git checkout run '
                           '`npm ci --prefix react` and `python3 scripts/build_react_runtime.py`. '
                           'Release wheels and sdists already include it.')
    version = tomllib.loads((root / 'pyproject.toml').read_text())['project']['version']
    with ZipFile(runtime) as archive:
        if json.loads(archive.read('manifest.json'))['version'] != version:
            raise RuntimeError('Stale bundled runtime; rebuild before packaging')


class Build(build_py):
    def run(self):
        check_runtime()
        super().run()


class Source(sdist):
    def run(self):
        check_runtime()
        super().run()


setup(cmdclass={'build_py': Build, 'sdist': Source})
