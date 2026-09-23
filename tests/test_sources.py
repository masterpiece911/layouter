"""Discovery is safe without Node; optional end-to-end tests use the real runtime."""
import contextlib
from dataclasses import replace
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import unittest
from unittest.mock import patch, Mock

from layouter.cli import main
from layouter.config import bind, declarations, load, resolve
from layouter.errors import ConfigError
from layouter.schema import normalize_document
from layouter.sources import ReactSource

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / 'react' / 'runner.mjs'
READY = shutil.which('node') and (ROOT / 'react/dist/index.js').is_file() and (ROOT / 'react/node_modules/react').is_dir()
PREFIX = "import { defineWorkflow, Workflow, Workspace, Window, useScreen } from '@layouter/react';\n"
BASIC = PREFIX + "export default defineWorkflow({component() {return <Workflow><Workspace name='code'><Window name='editor' command={['zed']} /></Workspace></Workflow>}});"


class SourceFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name).resolve()
        self.local = self.project / '.dev'
        self.global_dir = self.project / 'global'
        self.local.mkdir()
        self.global_dir.mkdir()
        self.global_file = self.global_dir / 'default.toml'

    def load(self, **kwargs):
        return load(self.project, global_file=self.global_file, **kwargs)

class SourceTests(SourceFixture):
    def test_discovery_and_precedence_do_not_execute(self):
        (self.global_dir / 'default.toml').write_text('session="global"')
        (self.local / 'default.tsx').write_text('not even valid TSX')
        (self.global_dir / 'other.tsx').write_text('throw new Error("do not execute")')
        with patch('layouter.sources.subprocess.Popen', side_effect=AssertionError('must not execute')):
            config, paths = self.load(discover=True)
            self.assertEqual(list(config['workflows']), ['default', 'other'])
            self.assertEqual(paths, (self.local / 'default.tsx', self.global_dir / 'other.tsx'))
            _, paths = self.load(force_global=True)
            self.assertEqual(paths, (self.global_file,))
            (self.local / 'other.toml').write_text('session="local"')
            config, _ = self.load(workflow='other')
            self.assertEqual(resolve(config, self.project, 'other').session, 'local')

    def test_toml_explicit_extension_and_no_runtime_dependency(self):
        path = self.project / 'workflow.conf'
        path.write_text('session="plain"')
        with patch('layouter.sources.subprocess.Popen', side_effect=AssertionError('no runtime')):
            config, sources = self.load(selected=str(path))
            snapshot = Mock(side_effect=AssertionError('no snapshot'))
            self.assertEqual(resolve(config, self.project, sources=sources,
                                     output_snapshot=snapshot).session, 'plain')

    def test_output_snapshot_protocol(self):
        from layouter.i3 import Compositor, RequestKind
        from layouter.errors import BackendError
        compositor = Compositor.__new__(Compositor)
        compositor.connection = Mock()
        output = {'name': 'DP-1', 'active': True, 'primary': True, 'scale': 2,
                  'rect': {'x': 0, 'y': 0, 'width': 1920, 'height': 1080}}
        compositor.connection.request.return_value = [{**output, 'nodes': ['must not escape']}]
        self.assertEqual(compositor.outputs(), [output])
        compositor.connection.request.assert_called_once_with(RequestKind.GET_OUTPUTS)
        compositor.connection.request.return_value = {'bad': 'shape'}
        with self.assertRaisesRegex(BackendError, 'invalid outputs'):
            compositor.outputs()

    def test_same_scope_ambiguity_and_explicit_file(self):
        (self.local / 'default.toml').write_text('session="toml"')
        (self.local / 'default.tsx').write_text(BASIC)
        for discover in (True, False):
            with self.assertRaisesRegex(ConfigError, 'Ambiguous workflow'):
                self.load(discover=discover)
        config, paths = self.load(selected='.dev/default.toml')
        self.assertEqual(resolve(config, self.project, sources=paths).session, 'toml')
        config, paths = self.load(selected='.dev/default.tsx')
        self.assertIn('default', config['_react_sources'])

    def test_shadowed_global_ambiguity_does_not_affect_local(self):
        (self.local / 'default.toml').write_text('session="local"')
        (self.global_dir / 'default.toml').write_text('session="global"')
        (self.global_dir / 'default.tsx').write_text(BASIC)
        self.load()
        with self.assertRaisesRegex(ConfigError, 'Ambiguous'):
            self.load(force_global=True)

    def test_list_and_save_never_import_tsx_or_access_desktop(self):
        (self.local / 'default.tsx').write_text('invalid TSX')
        out, err = io.StringIO(), io.StringIO()
        with patch('layouter.sources.subprocess.Popen', side_effect=AssertionError('must not execute')), \
             patch('layouter.cli.Compositor', side_effect=AssertionError('no desktop')), \
             contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            self.assertEqual(main(['-C', str(self.project), '--list']), 0)
            self.assertEqual(main(['-C', str(self.project), '--save-layout']), 2)
        self.assertIn('arguments not evaluated', out.getvalue())
        self.assertIn('cannot rewrite programmable TSX', err.getvalue())

    def test_missing_runtime(self):
        (self.local / 'default.tsx').write_text(BASIC)
        config, paths = self.load()
        with patch.dict(os.environ, {'LAYOUTER_REACT_RUNTIME': str(self.project / 'missing')}):
            with self.assertRaisesRegex(ConfigError, 'React runtime missing'):
                resolve(config, self.project, sources=paths)

    def test_capture_rejects_tsx_destination(self):
        with patch('layouter.cli.Compositor', side_effect=AssertionError('no desktop')), \
             contextlib.redirect_stderr(io.StringIO()) as err:
            self.assertEqual(main(['-C', str(self.project), '--capture', '--file', 'new.tsx']), 2)
        self.assertIn('generates TOML', err.getvalue())

    @unittest.skipUnless(shutil.which('node'), 'Node not installed')
    def test_protocol_errors(self):
        # Tiny fake peers exercise malformed envelopes and subprocess cleanup.
        for output, expected in [('not json', 'protocol/JSON'), ('[]', 'envelope'),
                                 ('{"ok":true,"result":[]}', 'object result'),
                                 ('{"ok":true,"protocol":2,"result":{}}', 'version mismatch'),
                                 ('{"ok":false,"kind":"test","message":"broken"}', 'test failure')]:
            runner = self.project / 'fake.mjs'
            runner.write_text('process.stdout.write(' + json.dumps(output + '\n') + ');')
            with self.subTest(output=output), patch.dict(os.environ, {'LAYOUTER_REACT_RUNTIME': str(runner)}):
                with ReactSource(self.local / 'default.tsx') as source:
                    with self.assertRaisesRegex(ConfigError, expected):
                        source.metadata()

    @unittest.skipUnless(shutil.which('node'), 'Node not installed')
    def test_protocol_deadline_cleans_up_process(self):
        runner = self.project / 'hanging.mjs'
        runner.write_text("process.stdout.write('{'); setInterval(() => {}, 1000);")
        with patch.dict(os.environ, {'LAYOUTER_REACT_RUNTIME': str(runner)}), \
             patch('layouter.sources.selectors.DefaultSelector') as selector:
            selector.return_value.__enter__.return_value.select.return_value = []
            with ReactSource(self.local / 'default.tsx') as source:
                with self.assertRaisesRegex(ConfigError, 'timed out'):
                    source.metadata()
            self.assertIsNotNone(source.process.poll())


@unittest.skipUnless(READY, 'optional React runtime not built (npm ci && npm run build in react/)')
class ReactIntegrationTests(SourceFixture):
    def setUp(self):
        super().setUp()
        env = patch.dict(os.environ, {'LAYOUTER_REACT_RUNTIME': str(RUNTIME)})
        env.start()
        self.addCleanup(env.stop)

    def evaluate(self, code=BASIC, supplied=None, outputs=None):
        path = self.local / 'default.tsx'
        path.write_text(code)
        config, sources = self.load()
        return resolve(config, self.project, supplied=supplied, sources=sources, output_snapshot=outputs)

    def test_toml_tsx_parity(self):
        tsx = ROOT / 'tests/fixtures/react-parity.tsx'
        toml = ROOT / 'tests/fixtures/react-parity.toml'
        document = tomllib.loads(toml.read_text())
        with ReactSource(tsx) as source:
            metadata = source.metadata()
            self.assertEqual(declarations(metadata), declarations(document))
            bound = bind(metadata, ['prod'])
            rendered = source.materialize({'project': str(self.project), 'projectName': self.project.name,
                                          'workflow': 'default', 'args': bound, 'outputs': []})
        rendered['args'] = metadata['args']
        # Use the same literal env value before comparing normalization.
        document['env']['MODE'] = 'prod'
        canonical = normalize_document(document)
        self.assertEqual(normalize_document(rendered), canonical)
        toml_config, toml_sources = load(self.project, str(toml))
        tsx_config, tsx_sources = load(self.project, str(tsx))
        expected = resolve(toml_config, self.project, supplied=['prod'], sources=toml_sources)
        actual = resolve(tsx_config, self.project, supplied=['prod'], sources=tsx_sources)
        self.assertEqual(replace(actual, sources=expected.sources), expected)
        self.assertNotEqual(actual.session_id, expected.session_id)
        self.assertEqual([n.id for n in actual.nodes], ['workspace-code', 'editor', 'inline', 'nested', 'browser', 'tabs', 'last', 'floating-window', 'floating-kitty'])
        self.assertEqual(actual.by_id['tabs'].tabs[0].panes[0].env,
                         {'ROOT': 'root', 'MODE': 'prod', 'TERM_ENV': 'term', 'TAB_ENV': 'tab', 'PANE_ENV': 'pane'})

    def test_bind_once_before_render_and_import_once(self):
        marker = self.project / 'imports'
        code = PREFIX + f"""
import {{ appendFileSync }} from 'node:fs';
appendFileSync({json.dumps(str(marker))}, 'import\\n');
export default defineWorkflow({{
  args: {{ mode: {{position: 0, choices: ['dev', 'prod'], default: 'dev', help: 'Environment'}} }},
  component({{args}}) {{ return <Workflow session={{args.mode}}><Workspace name='code' /></Workflow> }}
}});"""
        from layouter.config import bind as original_bind
        with patch('layouter.config.bind', wraps=original_bind) as binder:
            self.assertEqual(self.evaluate(code, ['prod']).session, 'prod')
            self.assertEqual(binder.call_count, 1)
        self.assertEqual(marker.read_text(), 'import\n')
        invalid = code.replace("return <Workflow", "throw new Error('render must not run'); return <Workflow")
        with self.assertRaisesRegex(ConfigError, 'choose one of'):
            self.evaluate(invalid, ['staging'])

    def test_check_has_no_desktop_and_normal_runs_snapshot_once(self):
        code = PREFIX + "export default defineWorkflow({component() {const {width}=useScreen();return <Workflow><Workspace name='code' layout={width>=1920?'splith':'splitv'} /></Workflow>}});"
        self.evaluate(code)
        with patch('layouter.cli.Compositor', side_effect=AssertionError('no desktop')), \
             contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(main(['-C', str(self.project), '--check']), 0)
        snapshot = Mock(return_value=[{'name': 'DP-1', 'active': True, 'rect': {'width': 2560, 'height': 1440}}])
        self.assertEqual(self.evaluate(code, outputs=snapshot).nodes[0].layout, 'splith')
        snapshot.assert_called_once_with()

    def test_modes_enter_existing_reconciler(self):
        self.evaluate()
        with patch('layouter.cli.Compositor') as compositor, patch('layouter.cli.Reconciler') as reconciler, \
             patch('layouter.cli.Runtime'), contextlib.redirect_stdout(io.StringIO()):
            compositor.return_value.__enter__.return_value.outputs.return_value = []
            compositor.return_value.__enter__.return_value.path = '/fake/socket'
            self.assertEqual(main(['-C', str(self.project), '--dry-run', '--sync', '--sync-displays']), 0)
            reconciler.return_value.plan.assert_called_once_with(sync=True, sync_displays=True)
            self.assertEqual(main(['-C', str(self.project), '--sync', '--sync-displays', '--no-focus']), 0)
            reconciler.return_value.run.assert_called_once_with(no_focus=True, sync=True, sync_displays=True)

    def test_failure_categories(self):
        for code, message in [
            ('export default <', 'TS/TSX transformation'),
            ("throw new Error('import failed')", 'workflow module execution'),
            (PREFIX + "export default defineWorkflow({component(){throw new Error('render failed')}})", 'workflow render'),
            (BASIC.replace("command={['zed']}", 'command={[]}'), 'argv array'),
            (BASIC.replace("command={['zed']}", 'command={() => []}'), 'JSON values'),
            (BASIC.replace("command={['zed']}", "command={['zed']} floating width={-1}"), 'positive integer'),
        ]:
            with self.subTest(message=message), self.assertRaisesRegex(ConfigError, message):
                self.evaluate(code)

    def test_relative_imports_and_project_paths(self):
        (self.local / 'component.tsx').write_text(
            "import {Window, useLayouter} from '@layouter/react'; export function Editor() {"
            "const {project, projectName}=useLayouter(); "
            "return <Window name='editor' command={['zed', project, projectName]} />}")
        code = PREFIX + "import {Editor} from './component'; " + \
            "export default defineWorkflow({component(){return <Workflow><Workspace name='code'><Editor /></Workspace></Workflow>}});"
        workflow = self.evaluate(code)
        self.assertEqual(workflow.by_id['editor'].command, ('zed', str(self.project), self.project.name))
        self.assertEqual(workflow.sources, ((self.local / 'default.tsx').resolve(),))

    def test_console_output_stays_off_protocol(self):
        code = "console.log('module diagnostic');\n" + BASIC.replace('return <Workflow>', "console.log('render diagnostic'); return <Workflow>")
        self.assertEqual(self.evaluate(code).nodes[1].id, 'editor')

    def test_zipapp_with_external_runtime(self):
        subprocess.run([sys.executable, str(ROOT / 'scripts/build_zipapp.py'), '--core'], cwd=ROOT,
                       check=True, capture_output=True)
        path = self.local / 'default.tsx'
        path.write_text(BASIC)
        result = subprocess.run([str(ROOT / 'dist/layouter-core'), '-C', str(self.project), '--check'],
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Valid: default', result.stdout)
