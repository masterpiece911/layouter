"""Release signing gates without credentials or requests to Mozilla."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from zipfile import ZipFile

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('firefox_signing', ROOT / 'scripts/sign_firefox_extension.py')
signing = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(signing)


class SigningTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        (self.root / 'dist').mkdir()
        (self.root / 'firefox-extension').mkdir()
        (self.root / 'pyproject.toml').write_text('[project]\nversion = "1.0.0"\n')
        self.manifest = {'version': '1.0.0', 'browser_specific_settings': {'gecko': {'id': 'firefox@layouter.dev'}}}
        (self.root / 'firefox-extension/manifest.json').write_text(json.dumps(self.manifest))
        self.unsigned = self.root / 'dist/layouter-firefox-1.0.0-unsigned.xpi'
        with ZipFile(self.unsigned, 'w') as archive:
            for name in signing.FILES:
                archive.writestr(name, json.dumps(self.manifest) if name == 'manifest.json' else 'original')
        self.sums = self.root / 'dist/SHA256SUMS'
        self.original_sums = f'{hashlib.sha256(self.unsigned.read_bytes()).hexdigest()}  {self.unsigned.name}\n'
        self.sums.write_text(self.original_sums)
        self.env = patch.dict(os.environ, {'WEB_EXT_API_KEY': 'test-issuer', 'WEB_EXT_API_SECRET': 'test-secret'})
        self.env.start()
        self.addCleanup(self.env.stop)

    def returned_xpi(self, args, *, changed=False, signature=True, **kwargs):
        self.assertNotIn('test-secret', ' '.join(args))
        self.assertIn('--channel=unlisted', args)
        self.assertIn('--no-input', args)
        source = Path(args[args.index('--source-dir') + 1])
        self.assertEqual({p.name for p in source.iterdir()}, signing.FILES)
        artifacts = Path(args[args.index('--artifacts-dir') + 1])
        artifacts.mkdir()
        with ZipFile(artifacts / 'mozilla-result.xpi', 'w') as archive:
            for file in source.iterdir():
                archive.writestr(file.name, 'changed' if changed and file.name == 'protocol.js' else file.read_bytes())
            if signature:
                archive.writestr('META-INF/mozilla.rsa', 'test-only signature metadata')

    def test_signed_payload_and_checksum_preserve_original_artifact(self):
        with patch.object(signing.subprocess, 'run', side_effect=self.returned_xpi) as command:
            result = signing.sign(self.root, 'v1.0.0', 'web-ext')
        self.assertEqual(command.call_count, 1)
        self.assertEqual(result.name, 'layouter-firefox-1.0.0.xpi')
        self.assertEqual(self.sums.read_text(), self.original_sums +
                         f'{hashlib.sha256(result.read_bytes()).hexdigest()}  {result.name}\n')
        self.assertTrue(self.unsigned.exists())
        with patch.object(signing.subprocess, 'run') as command:
            with self.assertRaisesRegex(ValueError, 'already exists'):
                signing.sign(self.root, 'v1.0.0', 'web-ext')
            command.assert_not_called()

    def test_bad_tag_version_checksum_or_credentials_never_submit(self):
        for failure in ('tag', 'version', 'checksum', 'credentials'):
            with self.subTest(failure=failure), patch.object(signing.subprocess, 'run') as command:
                manifest = dict(self.manifest, version='2.0.0') if failure == 'version' else self.manifest
                (self.root / 'firefox-extension/manifest.json').write_text(json.dumps(manifest))
                self.sums.write_text('bad checksum\n' if failure == 'checksum' else self.original_sums)
                with patch.dict(os.environ, {'WEB_EXT_API_SECRET': '' if failure == 'credentials' else 'test-secret'}):
                    with self.assertRaises(ValueError):
                        signing.sign(self.root, 'v2.0.0' if failure == 'tag' else 'v1.0.0', 'web-ext')
                command.assert_not_called()

    def test_changed_or_unsigned_download_never_becomes_release_asset(self):
        for changed, signature in ((True, True), (False, False)):
            with self.subTest(changed=changed, signature=signature):
                def run(args, **kwargs):
                    self.returned_xpi(args, changed=changed, signature=signature, **kwargs)
                with patch.object(signing.subprocess, 'run', side_effect=run):
                    with self.assertRaises(ValueError):
                        signing.sign(self.root, 'v1.0.0', 'web-ext')
                self.assertFalse((self.root / 'dist/layouter-firefox-1.0.0.xpi').exists())
                self.assertEqual(self.sums.read_text(), self.original_sums)

    def test_recovery_accepts_reserialized_manifest_without_credentials_or_submission(self):
        recovered = self.root / 'approved.xpi'
        with ZipFile(recovered, 'w') as archive:
            for name, content in signing.payload(self.unsigned).items():
                archive.writestr(name, json.dumps(json.loads(content), indent=2)
                                 if name == 'manifest.json' else content)
            archive.writestr('META-INF/cose.sig', 'test-only signature metadata')
        with patch.dict(os.environ, {'WEB_EXT_API_KEY': '', 'WEB_EXT_API_SECRET': ''}), \
                patch.object(signing.subprocess, 'run') as command:
            result = signing.sign(self.root, 'v1.0.0', 'unused', recovered)
        command.assert_not_called()
        self.assertEqual(result.read_bytes(), recovered.read_bytes())

    def test_manifest_unicode_formatting_allowed_but_semantic_changes_rejected(self):
        original = {'manifest.json': '{"name": "Layouter — companion"}'.encode()}
        recovered = self.root / 'approved.xpi'
        for manifest, accepted in (({'name': 'Layouter — companion'}, True),
                                   ({'name': 'Different companion'}, False)):
            with ZipFile(recovered, 'w') as archive:
                archive.writestr('manifest.json', json.dumps(manifest))
                archive.writestr('META-INF/cose.sig', 'test-only signature metadata')
            if accepted:
                signing.verify_signed_payload(original, recovered)
            else:
                with self.assertRaisesRegex(ValueError, 'manifest.json'):
                    signing.verify_signed_payload(original, recovered)

    def test_no_download_blocks_release(self):
        with patch.object(signing.subprocess, 'run'):
            with self.assertRaisesRegex(ValueError, 'exactly one signed XPI'):
                signing.sign(self.root, 'v1.0.0', 'web-ext')
        self.assertEqual(self.sums.read_text(), self.original_sums)
