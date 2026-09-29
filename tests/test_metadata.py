"""The launcher metadata contract uses launch validation without launching."""
import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layouter.cli import main
from layouter.errors import ConfigError
from layouter.config import load, resolve
from layouter.metadata import describe_workflow, list_args, list_workflows


class MetadataTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.project = Path(temporary.name)
        self.local = self.project / '.dev'
        self.local.mkdir()
        self.global_dir = self.project / 'config' / 'layouter'
        self.global_dir.mkdir(parents=True)
        environment = patch.dict(os.environ, {'XDG_CONFIG_HOME': str(self.global_dir.parent)})
        environment.start()
        self.addCleanup(environment.stop)
        desktop = patch('layouter.cli.Compositor', side_effect=AssertionError('no desktop access'))
        desktop.start()
        self.addCleanup(desktop.stop)
        (self.local / 'default.toml').write_text('''description = "Development tools"
session = "dev-{service}"
[args]
mode = { position = 1, default = "dev", choices = ["dev", "prod"] }
service = { position = 0, help = "Service to launch" }
extra = { position = 2, required = false }
''')

    def cli(self, *args):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = main(['-C', str(self.project), *args])
        return code, out.getvalue(), err.getvalue()

    def test_order_and_effective_defaults_without_binding(self):
        self.assertEqual(list_args(self.project), [
            {'name': 'service', 'position': 0, 'required': True, 'help': 'Service to launch'},
            {'name': 'mode', 'position': 1, 'required': False, 'default': 'dev', 'choices': ['dev', 'prod']},
            {'name': 'extra', 'position': 2, 'required': False, 'default': ''},
        ])
        record = describe_workflow(self.project)
        self.assertEqual(record['source'], str(self.local / 'default.toml'))
        self.assertEqual(record['description'], 'Development tools')
        self.assertEqual(record['format'], 'toml')

    def test_json_contracts(self):
        for mode, key in [('--list', 'workflows'), ('--list-args', 'args'), ('--describe', 'workflow')]:
            with self.subTest(mode=mode):
                code, out, err = self.cli(mode, '--json')
                self.assertEqual((code, err), (0, ''))
                payload = json.loads(out)
                self.assertEqual(payload['schema_version'], 1)
                self.assertIn(key, payload)
        self.assertEqual(json.loads(self.cli('--list-args', '--json')[1])['args'], list_args(self.project))
        self.assertEqual(json.loads(self.cli('--describe', '--json')[1])['workflow'], describe_workflow(self.project))

    def test_discovery_precedence_and_unknown_tsx_args(self):
        (self.global_dir / 'default.toml').write_text('description="Global"')
        (self.global_dir / 'other.tsx').write_text('throw new Error("must not execute")')
        with patch('layouter.sources.subprocess.Popen', side_effect=AssertionError('must not execute')):
            records = list_workflows(self.project)
            self.assertEqual([r['name'] for r in records], ['default', 'other'])
            self.assertEqual(records[0]['description'], 'Development tools')
            self.assertIsNone(records[1]['args'])
            self.assertIsNone(records[1]['description'])
            self.assertEqual(self.cli('--list', '--json')[0], 0)
        self.assertEqual(describe_workflow(self.project, force_global=True)['description'], 'Global')
        self.assertEqual(self.cli('--global', '--describe', '--json')[0], 0)

    def test_explicit_file_and_empty_arguments(self):
        (self.project / 'custom.conf').write_text('session="custom"')
        record = describe_workflow(self.project, 'custom', selected='custom.conf')
        self.assertEqual(record['args'], [])
        self.assertEqual(record['format'], 'toml')
        code, out, _ = self.cli('--file', 'custom.conf', '--list-args', '--json', 'custom')
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['workflow'], 'custom')

    def test_selected_tsx_only_reads_metadata(self):
        (self.local / 'react.tsx').write_text('placeholder')
        with patch('layouter.sources.ReactSource.__enter__', lambda source: source), \
             patch('layouter.sources.ReactSource.__exit__'), \
             patch('layouter.sources.ReactSource.metadata', return_value={'args': {
                 'service': {'position': 0, 'choices': ['api', 'web']}}}) as metadata, \
             patch('layouter.sources.ReactSource.materialize', side_effect=AssertionError('no rendering')):
            code, out, err = self.cli('--list-args', '--json', 'react')
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(json.loads(out)['args'][0]['choices'], ['api', 'web'])
            metadata.assert_called_once()

    def test_invalid_declarations_and_options_produce_no_json(self):
        (self.local / 'bad.toml').write_text('[args]\nbad = { position=2 }')
        for args in [('--list-args', '--json', 'bad'), ('--describe', '--json', 'missing'),
                     ('--json',), ('--list-args', 'default', 'unwanted')]:
            with self.subTest(args=args):
                code, out, err = self.cli(*args)
                self.assertEqual(code, 2)
                self.assertEqual(out, '')
                self.assertTrue(err.startswith('layouter:'))
        with self.assertRaises(ConfigError):
            list_args(self.project, 'bad')

    def test_text_output_retains_list_signature_and_shows_details(self):
        self.assertEqual(self.cli('--list')[1], 'default <service> [mode=dev] [extra=]\n')
        text = self.cli('--list-args')[1]
        self.assertIn('0: service (required) — Service to launch', text)
        self.assertIn("choices=['dev', 'prod']", text)

    def test_description_text_and_json(self):
        for mode in ('--list', '--list-args', '--describe'):
            self.assertEqual(self.cli(mode)[1], self.cli(mode, '--text')[1])
        self.assertIn('Description: Development tools', self.cli('--describe', '--text')[1])
        record = json.loads(self.cli('--describe', '--json')[1])['workflow']
        self.assertEqual(record['description'], 'Development tools')

    def test_description_is_optional_literal_string(self):
        path = self.local / 'plain.toml'
        for value, expected in [('', None), ('description=""', ''),
                                ('description="Open {service} tools — 開発"', 'Open {service} tools — 開発')]:
            path.write_text(value + '\nsession="plain"')
            self.assertEqual(describe_workflow(self.project, 'plain')['description'], expected)
            config, sources = load(self.project, workflow='plain')
            resolve(config, self.project, 'plain', sources=sources)
        for value in ('42', 'true', '["summary"]'):
            path.write_text('description=' + value)
            with self.assertRaisesRegex(ConfigError, 'workflow description'):
                describe_workflow(self.project, 'plain')
            config, sources = load(self.project, workflow='plain')
            with self.assertRaisesRegex(ConfigError, 'workflow description'):
                resolve(config, self.project, 'plain', sources=sources)
