import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

from layouter import __version__
from layouter.errors import ConfigError
from layouter.react_runtime import runtime_runner


class RuntimeResourceTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        os.environ.pop('LAYOUTER_REACT_RUNTIME', None)
        resource = patch('layouter.react_runtime.files', return_value=self.root)
        resource.start()
        self.addCleanup(resource.stop)

    def make_runtime(self, *, version=__version__, digest=None, name='runner.mjs'):
        content = b'// executable asset\n'
        manifest = {'version': version, 'protocol': 1,
                    'files': {name: digest or hashlib.sha256(content).hexdigest()}}
        with ZipFile(self.root / '_react_runtime.zip', 'w') as archive:
            archive.writestr('manifest.json', json.dumps(manifest))
            archive.writestr(name, content)

    def test_private_extraction_and_cleanup_on_error(self):
        self.make_runtime()
        with self.assertRaisesRegex(ValueError, 'component error'):
            with runtime_runner() as runner:
                self.assertEqual(runner.read_text(), '// executable asset\n')
                self.assertEqual(runner.parent.stat().st_mode & 0o777, 0o700)
                parent = runner.parent
                raise ValueError('component error')
        self.assertFalse(parent.exists())

    def test_overrides_are_explicit_not_project_discovery(self):
        runner = self.root / 'custom.mjs'
        runner.write_text('// custom')
        with patch.dict(os.environ, {'LAYOUTER_REACT_RUNTIME': str(runner)}):
            with runtime_runner() as chosen:
                self.assertEqual(chosen, runner)
        self.assertTrue(runner.exists())

    def test_missing_mismatched_damaged_or_unsafe_asset(self):
        with self.assertRaisesRegex(ConfigError, 'full Layouter release'):
            with runtime_runner():
                pass
        for kwargs, message in [({'version': '99.0.0'}, 'version mismatch'),
                                ({'digest': 'bad'}, 'integrity failure'),
                                ({'name': '../runner.mjs'}, 'Invalid path')]:
            self.make_runtime(**kwargs)
            with self.subTest(kwargs=kwargs), self.assertRaisesRegex(ConfigError, message):
                with runtime_runner():
                    pass
