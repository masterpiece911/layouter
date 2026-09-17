import copy
import io
from pathlib import Path
import tempfile
import tomllib
import unittest
from unittest.mock import patch, MagicMock

from layouter.capture import capture, dumps, save_layout, write_document
from layouter.cli import main
from layouter.config import resolve
from layouter.errors import ConfigError
from layouter.i3 import Compositor
from layouter.kitty import Snapshot
from layouter.runtime import Runtime
from layouter.schema import normalize_document
from test_backends import TreeHarness


def desktop(children=None):
    return {"id": 1, "type": "root", "nodes": [
        {"id": 2, "type": "output", "name": "DP-1", "nodes": [
            {"id": 3, "type": "workspace", "name": "dev", "num": -1,
             "layout": "splith", "nodes": children or []}]}]}


def window(cid, workflow=None, nid=None, **extra):
    return {"id": cid, "type": "con", "app_id": "editor", "name": "Editor", "nodes": [],
            "marks": [workflow.mark(nid)] if workflow else [], "percent": .5, **extra}


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)
        self.runtime = Runtime(self.project / "runtime")
        self.compositor = Compositor.__new__(Compositor)

    def resolve(self, document):
        return resolve(normalize_document(document), self.project)

    def test_capture_unknown_apps_are_disabled_and_geometry_roundtrips(self):
        group = {"id": 11, "type": "con", "layout": "splitv", "percent": .3,
                 "nodes": [window(12)]}
        tree = desktop([window(10), group, window(13)])
        with patch("layouter.capture.process_command", return_value=(["editor", "{literal} 😀"], "/tmp")):
            result, warnings = capture(tree, "default", self.project, self.runtime)
        text = dumps(result, warnings)
        parsed = tomllib.loads(text)
        workflow = self.resolve(parsed)
        self.assertEqual([n.kind for n in workflow.nodes], ["workspace", "container"])
        # Enable the reviewed launch drafts to verify mixed ordering and interpolation.
        ws = parsed["workspace"][0]
        for item in ws["window"] + ws["container"][0]["window"]:
            item["enabled"] = True
        workflow = self.resolve(parsed)
        self.assertEqual([n.kind for n in workflow.nodes], ["workspace", "app", "container", "app", "app"])
        self.assertEqual(workflow.leaves[0].command, ("editor", "{literal} 😀"))
        self.assertEqual(workflow.by_id["group"].size, 30)
        self.assertEqual(workflow.by_id["group"].layout, "splitv")
        self.assertTrue(warnings)

    def test_capture_controlled_kitty(self):
        self.runtime.path.mkdir(mode=0o700)
        endpoint = self.runtime.socket("a" * 24)
        endpoint.touch()
        live = window(5, marks=["layouter_" + "a" * 24])
        state = Snapshot(True, ({"id": 1, "tabs": [{"id": 2, "title": "Tools", "layout": "grid", "windows": [
            {"id": 3, "title": "Shell", "cwd": "/tmp", "cmdline": ["sh"], "user_vars": {}}]}]},))
        with patch("layouter.capture.Kitty.inspect", return_value=state):
            result, _ = capture(desktop([live]), "default", self.project, self.runtime)
        node = self.resolve(result).leaves[0]
        self.assertEqual(node.kind, "kitty")
        self.assertEqual(node.tabs[0].layout, "grid")
        self.assertEqual(node.tabs[0].panes[0].command, ("sh",))

    def test_save_handles_i3_content_container_above_workspace(self):
        doc = {"workspace": [{"name": "dev", "window": [{"name": "one", "command": ["one"]}]}]}
        workflow = self.resolve(doc)
        tree = desktop([window(5, workflow, "one")])
        output = tree["nodes"][0]
        output["nodes"] = [{"id": 99, "type": "con", "name": "content", "nodes": output["nodes"]}]
        result, _ = save_layout(doc, workflow, tree, self.compositor, self.runtime)
        after = self.resolve(result)
        self.assertEqual(after.by_id["one"].parent, "workspace-dev")
        self.assertEqual(after.by_id["workspace-dev"].output, ("DP-1",))
        self.assertNotIn("saved-group-99", after.by_id)

    def test_capture_omits_scratchpad_and_floating(self):
        tree = desktop()
        tree["nodes"][0]["nodes"][0]["floating_nodes"] = [window(5)]
        tree["nodes"][0]["nodes"].append({"id": 7, "type": "workspace", "name": "__i3_scratch", "nodes": [window(8)]})
        result, warnings = capture(tree, "default", self.project, self.runtime)
        self.assertEqual(len(result["workspace"]), 1)
        self.assertNotIn("window", result["workspace"][0])
        self.assertIn("floating", warnings[0])

    def test_save_preserves_templates_missing_and_disabled_declarations(self):
        doc = {"session": "dev-{project_name}", "env": {"TEST": "{project}"}, "workspace": [
            {"name": "dev", "window": [
                {"name": "first", "command": ["editor", "{project}"]},
                {"name": "missing", "command": ["other"]},
                {"name": "disabled", "enabled": False, "command": ["off"]}]}]}
        before = copy.deepcopy(doc)
        workflow = self.resolve(doc)
        group = {"id": 11, "type": "con", "layout": "splitv", "percent": .75,
                 "nodes": [window(12, workflow, "first")]}
        result, warnings = save_layout(doc, workflow, desktop([group]), self.compositor, self.runtime)
        after = self.resolve(tomllib.loads(dumps(result)))
        self.assertEqual(doc, before)
        self.assertEqual(after.by_id["first"].parent, "saved-group-11")
        self.assertEqual(after.by_id["missing"].parent, "workspace-dev")
        self.assertEqual(after.by_id["first"].command, workflow.by_id["first"].command)
        self.assertEqual(result["session"], "dev-{project_name}")
        self.assertEqual(result["env"], doc["env"])
        self.assertIn("{project}", result["workspace"][0]["container"][0]["window"][0]["command"])
        self.assertIn("disabled", {w["name"] for w in result["workspace"][0]["window"]})
        self.assertTrue(any("missing" in w for w in warnings))

    def test_repeated_save_reuses_new_unmarked_groups(self):
        doc = {"workspace": [{"name": "dev", "window": [{"name": "one", "command": ["one"]}]}]}
        workflow = self.resolve(doc)
        tree = desktop([{"id": 11, "type": "con", "layout": "splitv", "nodes": [window(12, workflow, "one")]}])
        first, _ = save_layout(doc, workflow, tree, self.compositor, self.runtime)
        second, _ = save_layout(first, self.resolve(first), tree, self.compositor, self.runtime)
        self.assertEqual(first, second)

    def test_save_updates_workspace_destination_and_order(self):
        doc = {"workspace": [{"name": "dev", "window": [
            {"name": "one", "command": ["one"]}, {"name": "two", "command": ["two"]}]}]}
        workflow = self.resolve(doc)
        tree = desktop([window(5, workflow, "two"), window(6, workflow, "one")])
        ws = tree["nodes"][0]["nodes"][0]
        ws.update(name="other", layout="tabbed")
        result, _ = save_layout(doc, workflow, tree, self.compositor, self.runtime)
        after = self.resolve(tomllib.loads(dumps(result)))
        self.assertEqual([n.id for n in after.leaves], ["two", "one"])
        self.assertEqual(after.by_id["one"].parent, "workspace-other")
        self.assertEqual(after.by_id["workspace-other"].layout, "tabbed")
        self.assertEqual(after.by_id["workspace-other"].output, ("DP-1",))
        self.assertIn("workspace-dev", after.by_id)

    def test_saved_empty_group_does_not_trigger_sync_reconstruction(self):
        doc = {"workspace": [{"name": "dev", "container": [{
            "name": "group", "layout": "splitv", "window": [
                {"name": "one", "command": ["one"]},
                {"name": "two", "command": ["two"]}]}]}]}
        workflow = self.resolve(doc)
        tree = desktop([window(5, workflow, "one"), window(6, workflow, "two")])
        saved, _ = save_layout(doc, workflow, tree, self.compositor, self.runtime)
        after = self.resolve(tomllib.loads(dumps(saved)))
        self.assertIn("group", after.by_id)
        self.assertFalse(any(n.parent == "group" for n in after.nodes))
        harness = TreeHarness(after, tree)
        self.assertEqual(harness.backend.sync_plan(after), [])
        self.assertEqual(harness.backend.sync(after), [])
        self.assertEqual(harness.commands, [])

    def test_save_refuses_changed_location_derived_identity(self):
        doc = {"workspace": [{"name": "dev", "window": [{"name": "My editor", "command": ["editor"]}]}]}
        workflow = self.resolve(doc)
        nid = workflow.leaves[0].id
        tree = desktop([{"id": 5, "type": "con", "layout": "splitv", "nodes": [window(6, workflow, nid)]}])
        with self.assertRaisesRegex(ConfigError, "identity"):
            save_layout(doc, workflow, tree, self.compositor, self.runtime)

    def test_save_kitty_order_layout_and_command_preservation(self):
        doc = {"workspace": [{"name": "dev", "kitty": [{"name": "term", "pane": [
            {"name": "one", "command": ["tool", "{project}"]},
            {"name": "two", "after": "one", "env": {"TEST": "value"}}]}]}]}
        workflow = self.resolve(doc)
        live = []
        for cid, pid in [(20, "two"), (21, "one")]:
            live.append({"id": cid, "user_vars": {"layouter_pane": workflow.pane_key("term", "dev", pid)}})
        snapshot = Snapshot(True, ({"id": 1, "tabs": [{"id": 2, "title": "{literal}", "layout": "grid", "windows": live}]},))
        with patch("layouter.capture.Kitty.inspect", return_value=snapshot):
            result, _ = save_layout(doc, workflow, desktop([window(5, workflow, "term")]), self.compositor, self.runtime)
        after = self.resolve(result)
        tab = after.by_id["term"].tabs[0]
        self.assertEqual([p.id for p in tab.panes], ["two", "one"])
        self.assertEqual(tab.layout, "grid")
        self.assertEqual(tab.title, "{literal}")
        self.assertEqual(tab.panes[1].command, workflow.by_id["term"].tabs[0].panes[0].command)
        self.assertEqual(tab.panes[0].env, {"TEST": "value"})

    def test_save_kitty_cross_tab_move_keeps_identity(self):
        doc = {"workspace": [{"name": "dev", "kitty": [{"name": "term", "pane": [
            {"name": "one"}, {"name": "two"}]}]}]}
        workflow = self.resolve(doc)
        tabs = [{"id": i, "title": "tab", "layout": "tall", "windows": [
            {"id": i, "user_vars": {"layouter_pane": workflow.pane_key("term", "dev", pid)}}]}
            for i, pid in enumerate(["one", "two"], 1)]
        with patch("layouter.capture.Kitty.inspect", return_value=Snapshot(True, ({"id": 1, "tabs": tabs},))):
            result, warnings = save_layout(doc, workflow, desktop([window(5, workflow, "term")]), self.compositor, self.runtime)
        self.assertEqual([p.id for p in self.resolve(result).by_id["term"].tabs[0].panes], ["one", "two"])
        self.assertTrue(any("membership retained" in w for w in warnings))

    def test_writer_preserves_backup_and_refuses_stale_or_existing_source(self):
        path = self.project / "workflow.toml"
        write_document(path, 'session="old"\n')
        original = path.read_bytes()
        with self.assertRaises(FileExistsError):
            write_document(path, "new")
        backup = write_document(path, 'session="new"\n', original)
        self.assertEqual(backup.read_bytes(), original)
        with self.assertRaises(ConfigError):
            write_document(path, "stale", original)
        backup2 = write_document(path, 'session="next"\n', path.read_bytes())
        self.assertNotEqual(backup, backup2)
        self.assertEqual(backup.read_bytes(), original)

    def test_cli_capture_and_update_are_read_only_for_desktop(self):
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.tree.return_value = desktop()
        mock.resolve_node.side_effect = self.compositor.resolve_node
        with patch("layouter.cli.Compositor", return_value=mock), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO):
            self.assertEqual(main(["-C", str(self.project), "--capture", "test"]), 0)
            self.assertEqual(main(["-C", str(self.project), "--capture", "test"]), 2)
            self.assertEqual(main(["-C", str(self.project), "--save-layout", "test"]), 0)
            self.assertEqual(main(["-C", str(self.project), "--capture", "--sync"]), 2)
        mock.command.assert_not_called()
        self.assertTrue((self.project / ".dev/test.toml.bak").exists())

    def test_save_with_no_matching_session_keeps_source_bytes_and_no_backup(self):
        source = self.project / "global.toml"
        original = ('# Preserve this comment and formatting.\nsession = "simple"\n'
                    '[[workspace]]\nname = "dev"\n'
                    '[[workspace.kitty]]\nname = "term"\n'
                    '[[workspace.kitty.pane]]\ntitle = "main"\n')
        source.write_text(original)
        document = tomllib.loads(original)
        other_source = self.project / "other.toml"
        other_source.write_text(original)
        launched = resolve(normalize_document(document), self.project, sources=(other_source,))
        other_project = self.project / "other"
        other_project.mkdir()
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.tree.return_value = desktop([window(5, launched, "term")])
        mock.resolve_node.side_effect = self.compositor.resolve_node
        with patch("layouter.cli.Compositor", return_value=mock), patch("sys.stderr", new_callable=io.StringIO) as errors:
            result = main(["-C", str(other_project), "--file", str(source), "--save-layout"])
        self.assertEqual(result, 2)
        self.assertIn("No managed application windows matched", errors.getvalue())
        self.assertIn(str(other_project), errors.getvalue())
        self.assertIn("same workflow file", errors.getvalue())
        self.assertEqual(source.read_text(), original)
        self.assertFalse(list(self.project.glob("*.bak*")))
        mock.command.assert_not_called()

    def test_closed_kitty_pane_reports_absence_without_losing_declaration(self):
        doc = {"workspace": [{"name": "dev", "kitty": [{"name": "term", "pane": [
            {"title": "main", "command": ["sh"]},
            {"title": "side-a", "command": ["tool-a"]},
            {"title": "side-b", "command": ["tool-b"]}]}]}]}
        workflow = self.resolve(doc)
        for closed in ("side-a", "side-b"):
            with self.subTest(closed=closed):
                panes = [{"id": i, "user_vars": {"layouter_pane": workflow.pane_key("term", "dev", pid)}}
                         for i, pid in enumerate(("main", "side-a", "side-b")) if pid != closed]
                snapshot = Snapshot(True, ({"id": 1, "tabs": [
                    {"id": 2, "title": "dev", "layout": "tall", "windows": panes}]},))
                with patch("layouter.capture.Kitty.inspect", return_value=snapshot):
                    result, warnings = save_layout(doc, workflow, desktop([window(5, workflow, "term")]), self.compositor, self.runtime)
                self.assertEqual(self.resolve(result).by_id["term"].tabs[0].panes,
                                 workflow.by_id["term"].tabs[0].panes)
                self.assertTrue(any(f"term.dev.{closed}: pane absent" in w for w in warnings))
                self.assertFalse(any("term: absent" in w for w in warnings))

    def test_order_validation(self):
        for invalid in [-1, True, "first", 1.5]:
            with self.subTest(invalid=invalid), self.assertRaises(ConfigError):
                self.resolve({"workspace": [{"name": "dev", "window": [{"name": "one", "command": ["one"], "order": invalid}]}]})


if __name__ == "__main__":
    unittest.main()
