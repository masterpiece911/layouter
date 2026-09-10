import base64
import copy
import json
import os
from pathlib import Path
import re
import shlex
import socket
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from layouter.config import resolve
from layouter.errors import AmbiguousState, BackendError
from layouter.i3 import Connection, Compositor, EVENT, Events, RequestKind, HEADER, MAGIC, command_layout, marked, matches, quote, walk
from layouter.kitty import Kitty, Snapshot
from layouter.runtime import Runtime


def supports_unix_sockets():
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM):
            return True
    except PermissionError:
        return False


UNIX_SOCKETS = supports_unix_sockets()
requires_sockets = unittest.skipUnless(UNIX_SOCKETS, "runner prohibits Unix sockets; run on Linux desktop")


def kitty_workflow(project):
    return resolve({"workflows": {"default": {"nodes": {
        "work": {"type": "workspace", "name": "test"},
        "term": {"type": "kitty", "parent": "work", "tabs": {"dev": {
            "layout": "splits", "panes": {"one": {}, "two": {"after": "one", "location": "hsplit"}}
        }}}
    }}}}, project)


def receive(connection):
    def exact(n):
        result = b""
        while len(result) < n:
            chunk = connection.recv(n - len(result))
            if not chunk:
                raise EOFError()
            result += chunk
        return result
    magic, size, kind = HEADER.unpack(exact(HEADER.size))
    return kind, exact(size)


def send(connection, kind, data, *, fragmented=False):
    payload = json.dumps(data).encode()
    frame = HEADER.pack(MAGIC, len(payload), kind) + payload
    if fragmented:
        for start in range(0, len(frame), 3):
            connection.sendall(frame[start:start + 3])
    else:
        connection.sendall(frame)


class IPCServer:
    def __init__(self, path, handler):
        self.path = path
        self.handler = handler
        self.error = None
        self.socket = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.socket.bind(str(path))
        self.socket.listen(1)
        self.socket.settimeout(2)
        self.thread = threading.Thread(target=self.run, daemon=True)

    def run(self):
        try:
            connection, _ = self.socket.accept()
            with connection:
                connection.settimeout(2)
                self.handler(connection)
        except BaseException as exc:
            self.error = exc

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *args):
        self.thread.join(3)
        self.socket.close()
        if self.thread.is_alive():
            raise AssertionError("IPC fixture did not complete")
        if self.error and not args[0]:
            raise self.error


class IPCAndRuntimeTests(unittest.TestCase):
    def test_stacking_uses_i3_command_vocabulary(self):
        self.assertEqual(command_layout("stacked"), "stacking")
        self.assertEqual(command_layout("tabbed"), "tabbed")

    def test_window_match_is_portable_to_sway_app_id(self):
        native = {"id": 4, "window": None, "app_id": "firefox", "window_properties": None}
        self.assertTrue(matches(native, {"class": "^firefox$"}))
        self.assertTrue(matches(native, {"app_id": "^firefox$"}))
        self.assertFalse(matches(native, {"class": "^zed$"}))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)

    @requires_sockets
    def test_fragmented_i3_frame_roundtrip(self):
        def handler(connection):
            self.assertEqual(receive(connection), (4, b""))
            send(connection, 4, {"id": 1, "name": "çalışma"}, fragmented=True)
        with IPCServer(self.path / "i3", handler):
            with Connection(str(self.path / "i3"), 1) as client:
                self.assertEqual(client.request(RequestKind.GET_TREE), {"id": 1, "name": "çalışma"})

    @requires_sockets
    def test_subscription_ack_can_be_interleaved_with_immediate_event(self):
        def handler(connection):
            kind, payload = receive(connection)
            self.assertEqual(kind, 2)
            self.assertEqual(json.loads(payload), ["window"])
            send(connection, EVENT | 3, {"change": "new", "container": {"id": 99}})
            send(connection, 2, {"success": True})
        with IPCServer(self.path / "i3", handler):
            with Events(str(self.path / "i3"), 1) as events:
                self.assertEqual(events.next(1)["container"]["id"], 99)

    @requires_sockets
    def test_malformed_frame_and_eof_are_errors(self):
        for content in [b"bad-ip" + b"\0" * 8, b""]:
            def handler(connection):
                receive(connection)
                connection.sendall(content)
            endpoint = self.path / ("bad" + str(len(content)))
            with IPCServer(endpoint, handler):
                with Connection(str(endpoint), 1) as client, self.assertRaises(BackendError):
                    client.request(RequestKind.GET_TREE)

    def test_initial_post_launch_snapshot_catches_window_without_waiting(self):
        backend = Compositor.__new__(Compositor)
        backend.timeout = 0.1
        backend.tree = lambda: {"id": 0, "nodes": [{"id": 2, "window": 22, "window_properties": {"class": "Zed"}}]}
        events = Mock()
        self.assertEqual(backend.wait_new(events, {0, 1}, {"class": "^Zed$"}, "zed")["id"], 2)
        events.next.assert_not_called()

    def test_wait_new_never_claims_old_window_even_if_title_changes(self):
        backend = Compositor.__new__(Compositor)
        backend.timeout = 0.1
        backend.tree = lambda: {"id": 0, "nodes": [{"id": 1, "window": 22, "name": "changed"}]}
        events = Mock()
        events.next.side_effect = TimeoutError()
        with self.assertRaisesRegex(BackendError, "Timed out"):
            backend.wait_new(events, {0, 1}, {"title": "changed"}, "old")

    def test_command_failure_is_not_silently_ignored(self):
        backend = Compositor.__new__(Compositor)
        backend.connection = Mock()
        backend.connection.request.return_value = [{"success": False, "error": "failed"}]
        with self.assertRaises(BackendError):
            backend.command("nop")

    def test_marks_include_floating_and_detect_duplicates(self):
        tree = {"id": 0, "nodes": [], "floating_nodes": [{"id": 1, "marks": ["key"]}]}
        self.assertEqual(marked(tree, "key")["id"], 1)
        tree["nodes"].append({"id": 2, "marks": ["key"]})
        with self.assertRaises(AmbiguousState):
            marked(tree, "key")

    def test_i3_quote_escapes_command_delimiters_inside_quotes(self):
        self.assertEqual(quote('a"; kill; "b'), '"a\\"; kill; \\"b"')
        with self.assertRaises(BackendError):
            quote("a\nb")

    def test_runtime_lock_is_exclusive_and_survives_file_reuse(self):
        runtime = Runtime(self.path / "runtime")
        with runtime.lock("session"):
            with self.assertRaises(BackendError):
                with runtime.lock("session"):
                    pass
        with runtime.lock("session"):
            self.assertTrue((runtime.path / "session.lock").is_file())
        self.assertEqual(runtime.path.stat().st_mode & 0o777, 0o700)

    def test_runtime_rejects_nonprivate_directory(self):
        path = self.path / "public"
        path.mkdir(mode=0o755)
        with self.assertRaises(BackendError):
            Runtime(path).ensure()

    def test_placement_refuses_preexisting_window(self):
        backend = Compositor.__new__(Compositor)
        w = kitty_workflow(self.path)
        with self.assertRaises(BackendError):
            backend.place_new(w, w.by_id["term"], {"id": 1}, {1})


class KittyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.runtime = Runtime(self.path / "runtime")
        self.workflow = kitty_workflow(self.path)
        self.node = self.workflow.by_id["term"]
        self.kitty = Kitty(self.workflow, self.node, self.runtime)

    def tree(self, present=False):
        return {"id": 0, "nodes": ([{"id": 1, "window": 22,
            "marks": [self.workflow.mark("term")], "window_properties": {"class": self.kitty.wm_class}}] if present else [])}

    def state(self):
        tab = self.node.tabs[0]
        return Snapshot(True, ({"id": 1, "tabs": [{"id": 10, "title": "renamed", "layout": "stack", "windows": [
            {"id": 3, "user_vars": self.kitty.variables(tab, tab.panes[0])}]}]},))

    def test_missing_socket_plus_existing_i3_window_is_error(self):
        with self.assertRaisesRegex(BackendError, "socket is missing"):
            self.kitty.inspect(self.tree(True))
        self.assertFalse(self.kitty.inspect(self.tree()).exists)

    @requires_sockets
    def test_dead_socket_is_absent_only_if_i3_window_also_absent(self):
        self.runtime.ensure()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(self.kitty.path))
        state = self.kitty.inspect(self.tree())
        self.assertFalse(state.exists)
        self.assertTrue(state.stale_socket)
        self.assertTrue(self.kitty.path.exists(), "inspection must not unlink even a stale socket")
        with self.assertRaisesRegex(BackendError, "unreachable"):
            self.kitty.inspect(self.tree(True))

    @requires_sockets
    def test_protocol_failure_is_never_absence(self):
        self.runtime.ensure()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.bind(str(self.kitty.path))
            sock.listen(1)
            with patch.object(self.kitty, "remote", return_value="invalid JSON"):
                with self.assertRaisesRegex(BackendError, "Invalid kitty state"):
                    self.kitty.inspect(self.tree())

    def test_duplicate_identity_rejected(self):
        state = self.state()
        state.os_windows[0]["tabs"][0]["windows"].append(copy.deepcopy(state.os_windows[0]["tabs"][0]["windows"][0]))
        with self.assertRaises(AmbiguousState):
            state.pane(self.workflow.pane_key("term", "dev", "one"))

    def test_remote_missing_pane_does_not_change_existing_tab_layout(self):
        state = self.state()
        tab = self.node.tabs[0]
        with patch.object(self.kitty, "remote", return_value="42") as remote:
            self.kitty.create_pane(tab, tab.panes[1], state)
            remote.assert_called_once()
            args = remote.call_args.args
            self.assertEqual(args[0], "launch")
            self.assertIn("--keep-focus", args)
            self.assertIn("id:10", args)
            self.assertIn("id:3", args)
            self.assertIn("hsplit", args)
            self.assertIn("layouter_pane=" + self.workflow.pane_key("term", "dev", "two"), args)
        self.assertEqual(state.os_windows[0]["tabs"][0]["layout"], "stack")

    def test_new_tab_layout_only_targets_new_tab(self):
        state = Snapshot(True, ({"id": 1, "tabs": [{"id": 20, "windows": [{"id": 50, "user_vars": {}}]}]},))
        tab = self.node.tabs[0]
        new = self.state()
        with patch.object(self.kitty, "remote", return_value="3") as remote, patch.object(self.kitty, "_ls_after_launch", return_value=new):
            self.kitty.create_pane(tab, tab.panes[0], state)
            calls = [call.args for call in remote.call_args_list]
        self.assertEqual(calls[0][0:3], ("launch", "--type", "tab"))
        self.assertIn("id:20", calls[0])
        self.assertEqual(calls[-1], ("goto-layout", "--match", "id:10", "splits"))

    def test_remote_argv_includes_explicit_unix_socket(self):
        with patch("layouter.kitty.subprocess.run", return_value=subprocess.CompletedProcess([], 0, "[]", "")) as run:
            self.kitty.remote("ls")
            args = run.call_args.args[0]
            self.assertEqual(args[:4], ["kitty", "@", "--to", "unix:" + str(self.kitty.path)])
            self.assertEqual(args[4:], ["--use-password", "never", "ls"])
            self.assertIs(run.call_args.kwargs["stdin"], subprocess.DEVNULL)

    def test_pane_detached_into_old_tab_during_creation_does_not_change_its_layout(self):
        state = Snapshot(True, ({"id": 1, "tabs": [{"id": 10, "windows": [{"id": 50, "user_vars": {}}]}]},))
        with patch.object(self.kitty, "remote", return_value="3") as remote, patch.object(self.kitty, "_ls_after_launch", return_value=self.state()):
            self.kitty.create_pane(self.node.tabs[0], self.node.tabs[0].panes[0], state)
            remote.assert_called_once()

    def test_bootstrap_encodes_arbitrary_command_and_env_safely(self):
        from dataclasses import replace
        original = self.node.tabs[0].panes[0]
        pane = replace(original, command=("printf", "a\nb", "$(touch /tmp/nope)", "'x'"),
                       env={"VALUE": "line1\nline2 $HOME"})
        path = self.kitty.startup_file(self.node.tabs[0], pane)
        lines = path.read_text().splitlines()
        self.assertEqual(len(lines), 4)
        launch = shlex.split(lines[3])
        decoded = json.loads(base64.b64decode(launch[-1]))
        self.assertEqual(decoded["command"], list(pane.command))
        self.assertEqual(decoded["env"]["VALUE"], pane.env["VALUE"])
        self.assertEqual(decoded["env"]["LAYOUTER_SESSION"], self.workflow.session_id)
        self.assertEqual(decoded["env"]["LAYOUTER_ELEMENT"], pane.id)
        self.assertEqual(decoded["env"]["LAYOUTER_PANE_ID"],
                         self.workflow.pane_key(self.node.id, self.node.tabs[0].id, pane.id))
        self.assertNotIn("$(touch", lines[3])

    def test_sync_corrects_existing_tab_title_and_layout(self):
        tab = self.node.tabs[0]
        panes = [{"id": index + 3, "user_vars": self.kitty.variables(tab, pane)}
                 for index, pane in enumerate(tab.panes)]
        state = Snapshot(True, ({"id": 1, "tabs": [{"id": 10, "title": "renamed",
            "layout": "stack", "windows": panes}]},))
        with patch.object(self.kitty, "inspect", return_value=state), \
             patch.object(self.kitty, "remote", return_value="") as remote:
            changes = self.kitty.sync(Mock(tree=lambda: {}), state)
        self.assertEqual(changes, [("term.dev", "kitty title, layout")])
        calls = [call.args for call in remote.call_args_list]
        self.assertTrue(any(call[0] == "set-enabled-layouts" for call in calls))
        self.assertIn(("goto-layout", "--match", "id:10", "splits"), calls)
        self.assertIn(("set-tab-title", "--match", "id:10", "--", "dev"), calls)
        self.assertFalse(any(call[0] == "detach-window" for call in calls))

    def test_sync_is_noop_for_matching_kitty_state(self):
        tab = self.node.tabs[0]
        panes = [{"id": index + 3, "user_vars": self.kitty.variables(tab, pane)}
                 for index, pane in enumerate(tab.panes)]
        state = Snapshot(True, ({"id": 1, "tabs": [{"id": 10, "title": tab.title,
            "layout": tab.layout, "windows": panes}]},))
        with patch.object(self.kitty, "inspect", return_value=state), \
             patch.object(self.kitty, "remote", return_value="") as remote:
            self.assertEqual(self.kitty.sync(Mock(tree=lambda: {}), state), [])
        remote.assert_not_called()

    def test_sync_reassembles_declared_panes_without_moving_unmanaged_panes(self):
        tab = self.node.tabs[0]
        one = {"id": 3, "user_vars": self.kitty.variables(tab, tab.panes[0])}
        two = {"id": 4, "user_vars": self.kitty.variables(tab, tab.panes[1])}
        unmanaged_one = {"id": 30, "user_vars": {}}
        unmanaged_two = {"id": 40, "user_vars": {}}
        os_window = {"id": 1, "tabs": [
            {"id": 10, "title": "one", "layout": "stack", "windows": [unmanaged_one, one]},
            {"id": 20, "title": "two", "layout": "grid", "windows": [two, unmanaged_two]},
        ]}
        next_tab = [20]

        def snapshot(_tree):
            return Snapshot(True, (copy.deepcopy(os_window),))

        def remote(*args):
            if args[0] == "detach-window":
                pane_id = int(args[args.index("--match") + 1].split(":")[1])
                target = args[args.index("--target-tab") + 1]
                source_tab = next(tab for tab in os_window["tabs"]
                                  if any(pane["id"] == pane_id for pane in tab["windows"]))
                pane = next(pane for pane in source_tab["windows"] if pane["id"] == pane_id)
                source_tab["windows"].remove(pane)
                if target == "new":
                    next_tab[0] += 10
                    target_tab = {"id": next_tab[0], "title": "new", "layout": "tall",
                                  "windows": []}
                    os_window["tabs"].append(target_tab)
                else:
                    target_id = int(target.split(":")[1])
                    target_tab = next(tab for tab in os_window["tabs"] if tab["id"] == target_id)
                target_tab["windows"].append(pane)
            elif args[0] == "goto-layout":
                target_id = int(args[2].split(":")[1])
                next(tab for tab in os_window["tabs"] if tab["id"] == target_id)["layout"] = args[3]
            elif args[0] == "set-tab-title":
                target_id = int(args[2].split(":")[1])
                next(tab for tab in os_window["tabs"] if tab["id"] == target_id)["title"] = args[-1]
            return ""

        initial = snapshot({})
        with patch.object(self.kitty, "inspect", side_effect=snapshot), \
             patch.object(self.kitty, "remote", side_effect=remote) as control:
            changes = self.kitty.sync(Mock(tree=lambda: {}), initial)
        final = snapshot({})
        live_one = final.pane(self.workflow.pane_key("term", "dev", "one"))
        live_two = final.pane(self.workflow.pane_key("term", "dev", "two"))
        self.assertEqual(live_one.tab["id"], live_two.tab["id"])
        self.assertEqual([pane["id"] for pane in live_one.tab["windows"]], [3, 4])
        self.assertTrue(any(pane["id"] == 30 for tab in os_window["tabs"] for pane in tab["windows"]))
        self.assertTrue(any(pane["id"] == 40 for tab in os_window["tabs"] for pane in tab["windows"]))
        self.assertTrue(changes)
        self.assertEqual(sum(call.args[0] == "detach-window" for call in control.call_args_list), 2)


class ByteSocket:
    """A byte stream, not an i3 mock: production framing consumes these bytes."""
    def __init__(self, incoming):
        self.incoming = bytearray(incoming)
        self.sent = bytearray()
        self.closed = False

    def settimeout(self, timeout):
        pass

    def connect(self, path):
        pass

    def sendall(self, value):
        self.sent.extend(value)

    def recv(self, size):
        size = min(size, 3, len(self.incoming))
        result = bytes(self.incoming[:size])
        del self.incoming[:size]
        return result

    def close(self):
        self.closed = True


def frame(kind, payload):
    data = json.dumps(payload).encode()
    return HEADER.pack(MAGIC, len(data), kind) + data


class InMemoryTransportTests(unittest.TestCase):
    def test_named_requests_preserve_protocol_wire_values(self):
        for kind, wire_value in ((RequestKind.RUN_COMMAND, 0),
                                 (RequestKind.SUBSCRIBE, 2),
                                 (RequestKind.GET_TREE, 4),
                                 (RequestKind.GET_VERSION, 7)):
            with self.subTest(kind=kind):
                stream = ByteSocket(frame(wire_value, {"success": True}))
                with patch("layouter.i3.socket.socket", return_value=stream):
                    with Connection("unused", 1) as client:
                        self.assertEqual(client.request(kind, "é"), {"success": True})
                self.assertEqual(stream.sent, HEADER.pack(MAGIC, 2, wire_value) + "é".encode())

    def test_named_request_rejects_mismatched_reply(self):
        stream = ByteSocket(frame(7, {}))
        with patch("layouter.i3.socket.socket", return_value=stream):
            with Connection("unused", 1) as client:
                with self.assertRaisesRegex(BackendError, "Unexpected compositor response type"):
                    client.request(RequestKind.GET_TREE)

    def test_fragmented_header_and_unicode_payload(self):
        stream = ByteSocket(frame(4, {"id": 1, "name": "çalışma"}))
        with patch("layouter.i3.socket.socket", return_value=stream):
            with Connection("unused", 1) as client:
                self.assertEqual(client.request(RequestKind.GET_TREE), {"id": 1, "name": "çalışma"})
        self.assertEqual(stream.sent, HEADER.pack(MAGIC, 0, 4))
        self.assertTrue(stream.closed)

    def test_subscription_consumes_ack_before_launch_and_keeps_earlier_event(self):
        stream = ByteSocket(frame(EVENT | 3, {"change": "new"}) + frame(2, {"success": True}))
        with patch("layouter.i3.socket.socket", return_value=stream):
            with Events("unused", 1) as events:
                self.assertEqual(events.next(1), {"change": "new"})
        self.assertEqual(json.loads(stream.sent[HEADER.size:]), ["window"])

    def test_eof_is_not_empty_state(self):
        with patch("layouter.i3.socket.socket", return_value=ByteSocket(b"")):
            with Connection("unused", 1) as client, self.assertRaises(BackendError):
                client.request(RequestKind.GET_TREE)

    def test_oversized_frame_rejected_without_reading_body(self):
        stream = ByteSocket(HEADER.pack(MAGIC, 128 * 1024 * 1024, 4))
        with patch("layouter.i3.socket.socket", return_value=stream):
            with Connection("unused", 1) as client, self.assertRaisesRegex(BackendError, "Invalid i3 IPC"):
                client.request(RequestKind.GET_TREE)


class TreeHarness:
    def __init__(self, workflow, state, *, sway=False):
        self.workflow, self.state = workflow, state
        self.backend = Compositor.__new__(Compositor)
        self.compositor = "sway" if sway else "i3"
        self.backend.mutable_ids = set()
        self.backend.safe_workspaces = set()
        self.backend.tree = lambda: copy.deepcopy(self.state)
        self.backend.command = self.command
        self.commands = []
        self.focused = None
        self.next_id = 1000

    def locate_parent(self, con_id):
        for parent in walk(self.state):
            for collection in (parent.get("nodes", []), parent.get("floating_nodes", [])):
                for index, child in enumerate(collection):
                    if child.get("id") == con_id:
                        return parent, collection, index
        raise AssertionError(f"container {con_id} has no parent")

    def mark_target(self, mark):
        return next(node for node in walk(self.state) if mark in node.get("marks", []))

    def command(self, value):
        self.commands.append(value)
        for part in value.split("; "):
            match = re.match(r"\[con_id=(\d+)\]\s+(.*)", part)
            con_id, action = (int(match.group(1)), match.group(2)) if match else (self.focused, part)
            node = next((item for item in walk(self.state) if item.get("id") == con_id), None)
            if action.startswith("workspace --no-auto-back-and-forth "):
                name = shlex.split(action[len("workspace --no-auto-back-and-forth "):])[0]
                target = next((item for item in walk(self.state)
                               if item.get("type") == "workspace" and item.get("name") == name), None)
                if target is None:
                    self.next_id += 1
                    target = {"id": self.next_id, "type": "workspace", "name": name,
                              "layout": "splith", "marks": [], "nodes": [], "floating_nodes": []}
                    self.state["nodes"].append(target)
                self.focused = target["id"]
            elif action.startswith("rename workspace "):
                old, _, new = shlex.split(action[len("rename workspace "):])
                next(item for item in walk(self.state)
                     if item.get("type") == "workspace" and item.get("name") == old)["name"] = new
            elif action == "focus":
                self.focused = con_id
            elif action.startswith("mark --add "):
                node.setdefault("marks", []).append(shlex.split(action[len("mark --add "):])[0])
            elif action.startswith("unmark "):
                node.setdefault("marks", []).remove(shlex.split(action[len("unmark "):])[0])
            elif action == "floating disable":
                pass
            elif action.startswith("split "):
                parent, collection, index = self.locate_parent(con_id)
                self.next_id += 1
                layout = "splitv" if action.endswith("vertical") else "splith"
                wrapper = {"id": self.next_id, "type": "con", "layout": layout,
                           "marks": [], "nodes": [node], "floating_nodes": []}
                collection[index] = wrapper
            elif action.startswith("move container to mark "):
                mark = shlex.split(action[len("move container to mark "):])[0]
                target = self.mark_target(mark)
                _, source_collection, source_index = self.locate_parent(con_id)
                source = source_collection.pop(source_index)
                if target.get("window") is not None or target.get("app_id") is not None:
                    _, target_collection, target_index = self.locate_parent(target["id"])
                    target_collection.insert(target_index + 1, source)
                else:
                    target.setdefault("nodes", []).append(source)
            elif action.startswith("move container to workspace "):
                name = shlex.split(action[len("move container to workspace "):])[0]
                target = next(item for item in walk(self.state)
                              if item.get("type") == "workspace" and item.get("name") == name)
                _, source_collection, source_index = self.locate_parent(con_id)
                target.setdefault("nodes", []).append(source_collection.pop(source_index))
            elif action.startswith("layout "):
                layout = action.split()[1]
                node["layout"] = "stacked" if layout == "stacking" else layout
            elif action.startswith("resize set "):
                node["percent"] = float(action.split()[-2]) / 100
            else:
                raise AssertionError(f"Unsupported test command: {part}")


class PlacementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workflow = resolve({"workflows": {"default": {"nodes": {
            "work": {"type": "workspace", "name": "declared", "layout": "splith"},
            "outer": {"type": "container", "parent": "work", "layout": "splith"},
            "frontend": {"type": "app", "parent": "outer", "command": ["frontend"], "size": 65},
            "inner": {"type": "container", "parent": "outer", "layout": "splitv", "size": 35},
            "backend": {"type": "app", "parent": "inner", "command": ["backend"], "size": 60},
            "logs": {"type": "app", "parent": "inner", "command": ["logs"], "size": 40},
        }}}}, Path(self.temp.name))

    def state(self):
        w = self.workflow
        workspace = {"id": 1, "type": "workspace", "name": "declared", "layout": "splith",
                     "marks": [w.mark("work")], "nodes": [], "floating_nodes": []}
        return {"id": 0, "type": "root", "marks": [], "nodes": [workspace], "floating_nodes": []}

    def add_leaf(self, harness, key, con_id):
        node = {"id": con_id, "type": "con", "window": con_id + 100,
                "marks": [self.workflow.mark(key)], "nodes": [], "floating_nodes": []}
        next(item for item in walk(harness.state) if item.get("id") == 1)["nodes"].append(node)
        harness.backend.mutable_ids.add(con_id)
        return node

    def test_nested_tree_is_built_from_real_windows_on_i3_and_sway(self):
        for sway in (False, True):
            with self.subTest(sway=sway):
                harness = TreeHarness(self.workflow, self.state(), sway=sway)
                harness.backend.safe_workspaces.add(1)
                for key, con_id in (("frontend", 10), ("backend", 11), ("logs", 12)):
                    self.add_leaf(harness, key, con_id)
                harness.backend.finalize(self.workflow)
                inner = marked(harness.state, self.workflow.mark("inner"))
                outer = marked(harness.state, self.workflow.mark("outer"))
                self.assertEqual(inner["layout"], "splitv")
                self.assertEqual(outer["layout"], "splith")
                self.assertTrue(all(any(child["id"] == value for child in walk(outer))
                                    for value in (10, 11, 12)))
                self.assertTrue(any("resize set width 65 ppt" in command for command in harness.commands))
                self.assertTrue(any("resize set height 60 ppt" in command for command in harness.commands))

    def test_existing_descendant_is_not_reparented_to_recreate_missing_parent(self):
        harness = TreeHarness(self.workflow, self.state(), sway=True)
        frontend = self.add_leaf(harness, "frontend", 10)
        harness.backend.mutable_ids.clear()
        self.add_leaf(harness, "backend", 11)
        self.add_leaf(harness, "logs", 12)
        warnings = harness.backend.finalize(self.workflow)
        self.assertIsNone(marked(harness.state, self.workflow.mark("outer")))
        self.assertIsNotNone(marked(harness.state, self.workflow.mark("inner")))
        self.assertEqual([node.id for node in warnings], ["outer"])
        self.assertEqual(frontend, next(item for item in walk(harness.state) if item.get("id") == 10))
        self.assertFalse(any(command.startswith("[con_id=10] focus; split") or
                             command.startswith("[con_id=10] move container")
                             for command in harness.commands))

    def test_new_window_attaches_to_existing_moved_parent_without_changing_layout(self):
        state = self.state()
        workspace = state["nodes"][0]
        moved = {"id": 9, "type": "con", "layout": "tabbed",
                 "marks": [self.workflow.mark("inner")], "nodes": [], "floating_nodes": []}
        workspace["nodes"].append(moved)
        created = {"id": 12, "type": "con", "window": 112, "marks": [],
                   "nodes": [], "floating_nodes": []}
        workspace["nodes"].append(created)
        harness = TreeHarness(self.workflow, state, sway=True)
        harness.backend.place_new(self.workflow, self.workflow.by_id["logs"], created, {9})
        self.assertEqual(marked(harness.state, self.workflow.mark("inner"))["layout"], "tabbed")
        self.assertTrue(any(item.get("id") == 12 for item in walk(moved)))
        self.assertFalse(any("layout " in command or "resize " in command for command in harness.commands))

    def test_marked_empty_workspace_is_safe_after_interrupted_run(self):
        harness = TreeHarness(self.workflow, self.state())
        workspace = harness.backend._ensure_workspace(self.workflow, self.workflow.by_id["frontend"])
        self.assertEqual(workspace["id"], 1)
        self.assertEqual(harness.backend.safe_workspaces, {1})

    def test_single_child_container_is_deliberately_virtual(self):
        workflow = resolve({"workflows": {"default": {"nodes": {
            "work": {"type": "workspace", "name": "declared", "layout": "splith"},
            "tools": {"type": "container", "parent": "work", "layout": "splitv"},
            "terminal": {"type": "app", "parent": "tools", "command": ["terminal"]},
            "browser": {"type": "app", "parent": "work", "command": ["browser"]},
        }}}}, Path(self.temp.name))
        state = {"id": 0, "type": "root", "marks": [], "floating_nodes": [], "nodes": [{
            "id": 1, "type": "workspace", "name": "declared", "layout": "splith",
            "marks": [workflow.mark("work")], "floating_nodes": [], "nodes": []}]}
        harness = TreeHarness(workflow, state)
        harness.backend.safe_workspaces.add(1)
        for key, con_id in (("terminal", 10), ("browser", 11)):
            node = {"id": con_id, "type": "con", "window": con_id + 100,
                    "marks": [workflow.mark(key)], "nodes": [], "floating_nodes": []}
            state["nodes"][0]["nodes"].append(node)
            harness.backend.mutable_ids.add(con_id)
        self.assertEqual(harness.backend.finalize(workflow), [])
        self.assertIsNone(marked(harness.state, workflow.mark("tools")))
        self.assertEqual([node["id"] for node in state["nodes"][0]["nodes"]], [10, 11])

    def test_sync_rebuilds_existing_managed_tree_and_preserves_unmanaged_windows(self):
        state = self.state()
        workspace = state["nodes"][0]
        other = {"id": 2, "type": "workspace", "name": "other", "layout": "splith",
                 "marks": [], "nodes": [], "floating_nodes": []}
        state["nodes"].append(other)
        harness = TreeHarness(self.workflow, state, sway=True)
        frontend = self.add_leaf(harness, "frontend", 10)
        backend = self.add_leaf(harness, "backend", 11)
        logs = self.add_leaf(harness, "logs", 12)
        harness.backend.mutable_ids.clear()
        workspace["nodes"].remove(frontend)
        other["nodes"].append(frontend)
        unmanaged = {"id": 20, "type": "con", "window": 120, "marks": [],
                     "nodes": [], "floating_nodes": []}
        workspace["nodes"].insert(0, unmanaged)

        changes = harness.backend.sync(self.workflow)

        self.assertTrue(changes)
        outer = marked(harness.state, self.workflow.mark("outer"))
        inner = marked(harness.state, self.workflow.mark("inner"))
        self.assertEqual((outer["layout"], inner["layout"]), ("splith", "splitv"))
        self.assertTrue(all(any(node.get("id") == con_id for node in walk(outer))
                            for con_id in (10, 11, 12)))
        self.assertIsNotNone(next(node for node in walk(harness.state) if node.get("id") == 20))
        self.assertTrue(any("floating disable" in command for command in harness.commands))

        harness.commands.clear()
        self.assertEqual(harness.backend.sync(self.workflow), [])
        self.assertFalse(any("move container" in command for command in harness.commands))

    def test_sync_corrects_layout_and_size_without_rebuilding_correct_structure(self):
        harness = TreeHarness(self.workflow, self.state())
        harness.backend.safe_workspaces.add(1)
        for key, con_id in (("frontend", 10), ("backend", 11), ("logs", 12)):
            self.add_leaf(harness, key, con_id)
        harness.backend.finalize(self.workflow)
        inner = marked(harness.state, self.workflow.mark("inner"))
        inner["layout"] = "tabbed"
        marked(harness.state, self.workflow.mark("backend"))["percent"] = .5
        harness.backend.mutable_ids.clear()
        harness.commands.clear()

        changes = harness.backend.sync(self.workflow)

        self.assertIn(("inner", "declared layout"), changes)
        self.assertIn(("backend", "declared size"), changes)
        self.assertFalse(any("move container" in command or "floating disable" in command
                             for command in harness.commands))
        self.assertTrue(any("layout splitv" in command for command in harness.commands))
        self.assertTrue(any("resize set height 60 ppt" in command for command in harness.commands))

    def test_sync_restores_managed_workspace_name_without_rebuilding_tree(self):
        harness = TreeHarness(self.workflow, self.state())
        harness.backend.safe_workspaces.add(1)
        for key, con_id in (("frontend", 10), ("backend", 11), ("logs", 12)):
            self.add_leaf(harness, key, con_id)
        harness.backend.finalize(self.workflow)
        harness.state["nodes"][0]["name"] = "user-renamed"
        harness.backend.mutable_ids.clear()
        harness.commands.clear()

        changes = harness.backend.sync(self.workflow)

        self.assertIn(("work", "workspace identity/name"), changes)
        self.assertEqual(marked(harness.state, self.workflow.mark("work"))["name"], "declared")
        self.assertTrue(any(command.startswith("rename workspace") for command in harness.commands))
        self.assertFalse(any("move container" in command for command in harness.commands))


if __name__ == "__main__":
    unittest.main()
