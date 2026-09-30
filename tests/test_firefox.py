"""Browser preservation, containment, identity and transport regression coverage."""
import copy
from dataclasses import replace
from contextlib import contextmanager
import io
import json
import os
from pathlib import Path
import socket
import struct
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from layouter.config import resolve
from layouter.errors import AmbiguousState, BackendError, ConfigError
from layouter.firefox import Companion, FirefoxRegistry, FirefoxTabState, FirefoxWindowBackend, FirefoxWindowState
from layouter.firefox_host import FIREFOX_PROTOCOL_VERSION, read_native, write_native, serve
from layouter.i3 import Compositor
from layouter.model import FirefoxTab
from layouter.reconcile import Reconciler
from layouter.runtime import Runtime
from layouter.schema import normalize_document
from test_reconcile import FakeI3


def workflow(**fields):
    return resolve(normalize_document({"session": "dev", "workspace": [{"number": 3,
        "firefox": [{"name": "work", **fields}]}]}), Path.cwd())


class FirefoxConfigTests(unittest.TestCase):
    def test_defaults_literal_tab_identity_and_parent_scope(self):
        w = workflow(tab=[{"url": "https://example.com/a?b=c", "pinned": True},
                          {"name": "jira", "url": "https://jira", "active": True}])
        n = w.leaves[0]
        self.assertEqual((n.kind, n.executable, n.args), ("firefox", "firefox", ()))
        self.assertEqual(n.firefox_tabs[0], FirefoxTab("https://example.com/a?b=c", "https://example.com/a?b=c", True))
        self.assertEqual(n.firefox_tabs[1].id, "jira")
        data = {"workspace": [{"number": 3, "firefox": [
            {"name": name, "tab": [{"url": "https://same"}]} for name in ("one", "two")]}]}
        self.assertEqual(len(resolve(normalize_document(data), Path.cwd()).leaves), 2)

    def test_launch_identity_and_legacy_identity(self):
        w = workflow(args=["-P", "Work"])
        n = w.leaves[0]
        self.assertEqual(w.element_key(n), workflow(args=["-P", "Work"]).element_key(n))
        for changed in (replace(n, args=("-P", "Personal")), replace(n, executable="zen-browser"),
                        replace(n, args=("-P Work",))):
            self.assertNotEqual(w.element_key(n), w.element_key(changed))
            self.assertNotEqual(w.mark_for(n), w.mark_for(changed))
        for kind in ("app", "kitty", "container"):
            old = replace(n, kind=kind)
            self.assertEqual(w.element_key(old), w.element_id(old.id))
            self.assertEqual(w.mark_for(old), w.mark(old.id))

    def test_invalid_tabs_and_fields(self):
        invalid = [dict(tab=[{"url": "x"}, {"url": "x"}]),
                   dict(tab=[{"name": "n", "url": "x"}, {"name": "n", "url": "y"}]),
                   dict(tab=[{"url": "x", "active": True}, {"url": "y", "active": True}]),
                   dict(tab=[{"url": ""}]), dict(tab=[{"name": "", "url": "x"}]),
                   dict(tab=[{"url": "x", "pinned": 1}]), dict(tab=[{}]),
                   dict(args="-P Work"), dict(args=[1]), dict(width=500),
                   dict(floating=True, size=20)]
        invalid += [{field: True} for field in ("command", "match", "adopt", "private", "profile", "initial_urls", "initial_tabs")]
        for fields in invalid:
            with self.subTest(fields=fields), self.assertRaises(ConfigError):
                workflow(**fields)

    def test_disabled_and_floating(self):
        self.assertEqual(workflow(enabled=False).leaves, ())
        self.assertEqual(workflow(tab=[{"enabled": False}]).leaves[0].firefox_tabs, ())
        w = resolve(normalize_document({"workspace": [{"number": 3, "floating": {
            "firefox": [{"name": "scratch", "width": 500}]}}]}), Path.cwd())
        self.assertTrue(w.leaves[0].floating)
        self.assertEqual(w.leaves[0].width, 500)
        workflow(tab=[{"url": "x", "active": True}, {"url": "y", "active": True, "enabled": False}])


class FakeBrowser:
    def __init__(self, key):
        self.key = key
        self.owned = True
        self.window_id = 10
        self.tabs = []
        self.calls = []
        self.native = None

    def call(self, op, **data):
        self.calls.append((op, data))
        if op == "inspect":
            return {"owned": self.owned, "windowId": self.window_id, "tabs": [{"id": t["id"], "identity": t["identity"]} for t in self.tabs if t["identity"] != "unmanaged"]}
        if op == "create-window":
            self.window_id = 20
            self.tabs = []
            self.native()
        elif op in {"ensure-tabs", "sync-tabs"}:
            for spec in data['tabs']:
                tab = next((t for t in self.tabs if t['identity'] == spec['id']), None)
                if op == 'sync-tabs' and tab and tab['url'] != spec['url']:
                    tab['identity'] = 'unmanaged'
                    tab = None
                if tab is None:
                    tab = dict(id=max([t['id'] for t in self.tabs] + [0]) + 1,
                               identity=spec['id'], url=spec['url'], pinned=spec['pinned'],
                               active=spec['active'], index=len(self.tabs))
                    self.tabs.append(tab)
                if op == 'sync-tabs':
                    tab['pinned'] = spec['pinned']
                    if spec['active']:
                        for t in self.tabs:
                            t['active'] = t is tab


class FirefoxBackendTests(unittest.TestCase):
    def setUp(self):
        self.w = workflow(tab=[{"name": "jira", "url": "https://jira", "pinned": True, "active": True}])
        self.n = self.w.leaves[0]
        self.i3 = FakeI3(self.w)
        self.registry = FirefoxRegistry(Runtime(Path('/unused')), 1)
        self.browser = FakeBrowser(self.w.element_key(self.n))
        self.registry.companions = lambda: [self.browser]
        self.backend = FirefoxWindowBackend(self.w, self.n, Runtime(Path('/unused')), self.registry)
        self.live = self.i3.add(wm_class='arbitrary-browser')
        self.i3.claim_existing = lambda w, n, live: self.i3.mark(live, w.mark_for(n))
        self.i3.wait_for_title_token = Mock(return_value=self.live)
        self.browser.native = lambda: setattr(self, 'live', self.i3.add(wm_class='brand-free'))

    def test_recover_preserves_placement_and_second_run_has_no_probe(self):
        self.assertEqual(self.backend.ensure(self.i3), 'recover')
        self.assertEqual(self.i3.mutations, [('mark', self.live['id'])])
        self.assertEqual([op for op, _ in self.browser.calls].count('probe-window'), 1)
        self.assertEqual([op for op, _ in self.browser.calls].count('clear-probe'), 1)
        self.browser.calls.clear()
        self.assertEqual(self.backend.ensure(self.i3), 'keep')
        self.assertNotIn('probe-window', [op for op, _ in self.browser.calls])

    def test_probe_subscription_precedes_mutation(self):
        original = self.browser.call
        def checked(op, **data):
            if op in {"probe-window", "clear-probe"}:
                self.assertTrue(self.i3.subscribed)
            return original(op, **data)
        self.browser.call = checked
        self.backend.ensure(self.i3)

    def test_owner_recreation_does_not_require_launch_executable(self):
        self.backend.node = replace(self.n, executable="definitely-not-installed")
        self.browser.window_id = None
        reconciler = Reconciler(self.w, self.i3, Runtime(Path('/unused')),
                               firefox_factory=lambda *args: self.backend)
        with patch('layouter.reconcile.check_executable', side_effect=AssertionError('unneeded launch check')):
            reconciler.preflight(reconciler.plan())

    def test_probe_cleared_on_failure(self):
        self.i3.wait_for_title_token.side_effect = AmbiguousState('two native windows')
        with self.assertRaises(AmbiguousState):
            self.backend.ensure(self.i3)
        self.assertEqual(self.browser.calls[-1][0], 'clear-probe')
        self.assertEqual(self.i3.mutations, [])

    def test_cleanup_failure_preserves_ambiguity_error(self):
        self.i3.wait_for_title_token.side_effect = AmbiguousState('two native windows')
        original = self.browser.call
        def fail_clear(op, **data):
            if op == 'clear-probe':
                raise BackendError('browser disconnected')
            return original(op, **data)
        self.browser.call = fail_clear
        with self.assertRaisesRegex(AmbiguousState, 'two native windows; also could not clear'):
            self.backend.ensure(self.i3)

    def test_tab_url_preservation_and_sync_detaches(self):
        self.backend.reconcile_tabs(self.i3, sync=False)
        self.browser.tabs[0].update(url='https://jira/unsaved', pinned=False, active=False)
        self.browser.calls.clear()
        self.backend.reconcile_tabs(self.i3, sync=False)
        self.assertEqual([op for op, _ in self.browser.calls], ['inspect', 'ensure-tabs', 'inspect'])
        self.backend.reconcile_tabs(self.i3, sync=True)
        self.assertEqual(len(self.browser.tabs), 2)
        self.assertEqual(self.browser.tabs[0]['url'], 'https://jira/unsaved')
        self.assertEqual(self.browser.tabs[0]['identity'], 'unmanaged')
        self.assertTrue(self.browser.tabs[1]['pinned'])
        self.assertTrue(self.browser.tabs[1]['active'])
        self.assertNotIn('remove-tab', [op for op, _ in self.browser.calls])
        self.assertNotIn('https://jira/unsaved', json.dumps(self.browser.calls))
        self.assertEqual([op for op, _ in self.browser.calls],
                         ['inspect', 'ensure-tabs', 'inspect', 'inspect', 'sync-tabs', 'inspect'])
        for op, data in self.browser.calls:
            if op in {'ensure-tabs', 'sync-tabs'}:
                self.assertEqual(data['tabs'], [dict(id='jira', url='https://jira', pinned=True, active=True)])

    def test_wire_state_has_only_structural_fields(self):
        self.backend.reconcile_tabs(self.i3, sync=False)
        self.assertEqual(self.backend.inspect(self.i3.tree()).tabs, (FirefoxTabState(1, 'jira'),))
        self.assertEqual(set(FirefoxTabState.__dataclass_fields__), {'id', 'identity'})

    def test_browsing_fields_are_not_accepted_as_wire_tab_state(self):
        self.browser.call = lambda *args, **kw: {
            'owned': True, 'windowId': 10,
            'tabs': [dict(id=1, identity='jira', url='https://secret.invalid/do-not-leak')],
        }
        with self.assertRaises(BackendError) as error:
            self.backend.inspect(self.i3.tree())
        self.assertNotIn('https://secret.invalid/do-not-leak', str(error.exception))

    def test_missing_tab_replaced_and_second_run_is_additive(self):
        moved = dict(id=99, identity='jira', url='https://jira/work', pinned=False, active=False, index=0)
        # Browser inspect excludes out-of-parent metadata, irrespective of destination ownership.
        elsewhere = copy.deepcopy(moved)
        self.backend.reconcile_tabs(self.i3, sync=False)
        self.assertEqual(elsewhere, moved)
        self.assertEqual(len(self.browser.tabs), 1)
        self.backend.reconcile_tabs(self.i3, sync=False)
        self.assertEqual(len(self.browser.tabs), 1)

    def test_closed_window_creates_only_declared_tabs(self):
        self.browser.window_id = None
        self.i3.wait_for_title_token = lambda *args: self.live
        self.assertEqual(self.backend.ensure(self.i3), 'create')
        self.assertEqual([t['identity'] for t in self.browser.tabs], ['jira'])
        self.assertNotIn('restore', [op for op, _ in self.browser.calls])
        self.assertIn(('prepare', 'work'), self.i3.mutations)

    def test_empty_window(self):
        self.backend.node = replace(self.n, firefox_tabs=())
        self.browser.window_id = None
        self.i3.wait_for_title_token = lambda *args: self.live
        self.assertEqual(self.backend.ensure(self.i3), 'create')
        self.assertEqual(self.browser.tabs, [])

    def test_sync_does_not_replace_tabs_created_in_the_same_invocation(self):
        self.browser.window_id = None
        self.i3.wait_for_title_token = lambda *args: self.live
        original = self.browser.call

        def loading_or_redirected(op, **data):
            result = original(op, **data)
            if op == 'ensure-tabs':
                for tab in self.browser.tabs:
                    tab['url'] = 'https://jira/redirected'
            return result

        self.browser.call = loading_or_redirected
        reconciler = Reconciler(self.w, self.i3, Runtime(Path('/unused')),
                               firefox_factory=lambda *args: self.backend)
        reconciler.firefox_window(self.n, sync=True)
        self.assertEqual(len(self.browser.tabs), 1)
        self.assertEqual(self.browser.tabs[0]['identity'], 'jira')
        self.assertNotIn('sync-tabs', [op for op, _ in self.browser.calls])
        reconciler.firefox_window(self.n)
        self.assertEqual(len(self.browser.tabs), 1)
        # A later explicit sync still compares the existing tab's current URL.
        reconciler.firefox_window(self.n, sync=True)
        self.assertEqual(len(self.browser.tabs), 2)
        self.assertEqual(self.browser.tabs[0]['identity'], 'unmanaged')
        self.assertEqual(self.browser.tabs[1]['identity'], 'jira')

    def test_duplicate_owners_and_tabs_fail_before_mutation(self):
        self.registry.companions = lambda: [self.browser, self.browser]
        with self.assertRaises(AmbiguousState):
            self.backend.ensure(self.i3)
        self.registry.companions = lambda: [self.browser]
        tab = dict(id=1, identity='jira', url='x', pinned=False, active=False, index=0)
        self.browser.tabs = [tab, {**tab, 'id': 2}]
        with self.assertRaises(AmbiguousState):
            self.backend.reconcile_tabs(self.i3, sync=True)
        self.assertTrue(all(op == 'inspect' for op, _ in self.browser.calls))

    def test_dry_run_never_probes_or_mutates(self):
        actions = list(self.backend.plan(self.i3.tree(), True))
        self.assertEqual(actions[0][0], 'recover')
        self.assertEqual([op for op, _ in self.browser.calls], ['inspect'])
        self.registry.companions = lambda: []
        self.assertEqual(list(self.backend.plan(self.i3.tree()))[0][0], 'create')

    def test_mark_without_companion_is_not_missing(self):
        self.i3.mark(self.live, self.w.mark_for(self.n))
        self.registry.companions = lambda: []
        with self.assertRaisesRegex(BackendError, 'no live companion'):
            self.backend.ensure(self.i3)

    def test_reconciler_dispatch(self):
        reconciler = Reconciler(self.w, self.i3, Runtime(Path('/unused')),
                               firefox_factory=lambda *args: self.backend)
        reconciler.run(preflight=False)
        self.assertEqual(len(self.browser.tabs), 1)
        self.assertEqual(reconciler.actions[0].kind, 'recover')


class TitleProbeTests(unittest.TestCase):
    def test_fresh_snapshots_ignore_unrelated_events(self):
        c = object.__new__(Compositor)
        c.timeout = .1
        native = {'id': 1, 'window': 1, 'name': '__probe__ page'}
        c.tree = Mock(side_effect=[{'id': 0, 'nodes': []}, {'id': 0, 'nodes': [native]}])
        events = Mock()
        self.assertEqual(c.wait_for_title_token(events, '__probe__', 'browser'), native)
        events.next.assert_called_once()

    def test_probe_ambiguity(self):
        c = object.__new__(Compositor)
        c.timeout = .1
        c.tree = lambda: {'id': 0, 'nodes': [{'id': n, 'window': n, 'name': 'token'} for n in (1, 2)]}
        with self.assertRaises(AmbiguousState):
            c.wait_for_title_token(Mock(), 'token', 'browser')


class FirefoxTransportTests(unittest.TestCase):
    def test_native_framing_and_bounds(self):
        stream = io.BytesIO()
        write_native(stream, {'unicode': '🌲'})
        stream.seek(0)
        self.assertEqual(read_native(stream), {'unicode': '🌲'})
        with self.assertRaises(ValueError):
            read_native(io.BytesIO(struct.pack('=I', 2**30)))
        with self.assertRaises(EOFError):
            read_native(io.BytesIO(b'\x05'))

    def test_bridge_roundtrip_and_shutdown_cleanup(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime = Runtime(Path(folder))
            native, host = socket.socketpair()
            incoming, outgoing = host.makefile('rb'), host.makefile('wb')
            failures = []
            def run():
                try:
                    serve(incoming, outgoing, runtime)
                except BaseException as exc:
                    failures.append(exc)
            thread = threading.Thread(target=run)
            thread.start()
            reader, writer = native.makefile('rb'), native.makefile('wb')
            instance = 'a' * 32
            write_native(writer, {'op': 'register', 'instanceId': instance, 'version': FIREFOX_PROTOCOL_VERSION})
            path = runtime.socket(instance)
            # A connection retry is transport readiness, not browser discovery evidence.
            import time
            deadline = time.monotonic() + 2
            while not path.exists() and time.monotonic() < deadline:
                threading.Event().wait(.01)
            result = []
            client = threading.Thread(target=lambda: result.append(Companion(path, 2).call('hello')))
            client.start()
            request = read_native(reader)
            write_native(writer, {'id': request['id'], 'version': FIREFOX_PROTOCOL_VERSION, 'ok': True, 'result': {'instanceId': instance}})
            client.join(3)
            self.assertEqual(result, [{'instanceId': instance}])
            native.shutdown(socket.SHUT_WR)
            thread.join(3)
            self.assertFalse(thread.is_alive())
            self.assertFalse(path.exists())
            self.assertEqual(failures, [])
            for stream in (incoming, outgoing, reader, writer):
                stream.close()
            host.close(); native.close()

    def test_response_version_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'test.sock'
            with socket.socket(socket.AF_UNIX) as server:
                server.bind(str(path)); server.listen()
                def reply():
                    conn, _ = server.accept()
                    with conn, conn.makefile('rb') as stream:
                        req = json.loads(stream.readline())
                        conn.sendall(json.dumps({'id': req['id'], 'version': 1, 'ok': True}).encode() + b'\n')
                thread = threading.Thread(target=reply); thread.start()
                with self.assertRaisesRegex(BackendError, 'version 1.*version 2'):
                    Companion(path, 2).call('hello')
                thread.join()

    def test_bootstrap_selects_only_routed_companion_and_removes_only_token(self):
        w = workflow(args=['-P', 'Work'])
        registry = FirefoxRegistry(Runtime(Path('/unused')), 1)
        first, second = Mock(), Mock()
        url = []
        first.call.side_effect = lambda op, **kw: [] if op == 'find-bootstrap' else None
        second.call.side_effect = lambda op, **kw: [42] if op == 'find-bootstrap' else None
        registry.companions = lambda: [first, second]
        registry.inspect = lambda *args: FirefoxWindowState()
        runtime = Mock()
        runtime.spawn.side_effect = lambda argv, *args: url.append(argv)
        with registry.route(w, w.leaves[0], runtime) as owner:
            self.assertIs(owner, second)
        self.assertEqual(url[0][:3], ('firefox', '-P', 'Work'))
        self.assertTrue(url[0][3].startswith('http://127.0.0.1:'))
        self.assertEqual([call.args[0] for call in second.call.call_args_list],
                         ['find-bootstrap', 'claim-window', 'remove-bootstrap'])
        self.assertEqual(second.call.call_args.kwargs['tabId'], 42)
