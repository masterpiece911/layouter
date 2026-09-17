"""File/session identity must be identical in resolution and every backend key."""
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from layouter.cli import main
from layouter.config import load, resolve
from layouter.i3 import Compositor
from layouter.kitty import Kitty
from layouter.runtime import Runtime


class IdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.first = self.root / "first"
        self.second = self.root / "second"
        self.first.mkdir()
        self.second.mkdir()
        self.source = self.root / "simple.toml"
        self.source.write_text('session="simple"\nenv={SID="{session_id}"}\n'
                               '[[workspace]]\nname="dev"\n'
                               '[[workspace.window]]\nname="app"\ncommand=["editor", "{element_id}"]\n'
                               '[[workspace.kitty]]\nname="term"\n'
                               '[[workspace.kitty.pane]]\ntitle="shell"\n')

    def resolve(self, project, source=None):
        config, sources = load(project, str(source or self.source))
        return resolve(config, project, sources=sources)

    def test_same_file_across_projects_and_interpolated_ids(self):
        left, right = self.resolve(self.first), self.resolve(self.second)
        self.assertEqual(left.session_id, right.session_id)
        self.assertNotEqual(left.leaves[0].cwd, right.leaves[0].cwd)
        for node in left.nodes:
            self.assertEqual(left.mark(node.id), right.mark(node.id))
            self.assertEqual(node.env["SID"], left.session_id)
        self.assertEqual(left.by_id["app"].command[1], left.element_id("app"))
        self.assertEqual(left.pane_key("term", "dev", "shell"), right.pane_key("term", "dev", "shell"))
        self.assertEqual(left.tab_key("term", "dev"), right.tab_key("term", "dev"))
        runtime = Runtime(self.root / "runtime")
        self.assertEqual(Kitty(left, left.by_id["term"], runtime).path,
                         Kitty(right, right.by_id["term"], runtime).path)

    def test_global_discovery_has_same_identity_from_either_directory(self):
        config_dir = self.root / "config"
        global_dir = config_dir / "layouter"
        global_dir.mkdir(parents=True)
        source = global_dir / "simple.toml"
        source.write_bytes(self.source.read_bytes())
        with patch.dict("os.environ", {"XDG_CONFIG_HOME": str(config_dir)}):
            first_config, first_sources = load(self.first, workflow="simple")
            second_config, second_sources = load(self.second, workflow="simple")
        left = resolve(first_config, self.first, "simple", sources=first_sources)
        right = resolve(second_config, self.second, "simple", sources=second_sources)
        self.assertEqual(left.sources, (source.resolve(),))
        self.assertEqual(left.session_id, right.session_id)
        self.assertEqual(left.mark("app"), right.mark("app"))

    def test_explicit_project_session_separates_instances(self):
        self.source.write_text(self.source.read_text().replace('session="simple"', 'session="simple-{project}"'))
        self.assertNotEqual(self.resolve(self.first).session_id, self.resolve(self.second).session_id)

    def test_source_symlinks_equivalent_and_distinct_files_isolated(self):
        alias = self.root / "alias.toml"
        alias.symlink_to(self.source)
        copied = self.root / "copied.toml"
        copied.write_bytes(self.source.read_bytes())
        original = self.resolve(self.first)
        self.assertEqual(original.session_id, self.resolve(self.second, alias).session_id)
        self.assertNotEqual(original.session_id, self.resolve(self.first, copied).session_id)
        self.source.write_text(self.source.read_text() + '\n# An edit must not change identity.\n')
        self.assertEqual(original.session_id, self.resolve(self.first).session_id)

    def test_cli_directory_option_and_current_directory_are_equivalent(self):
        args = ["--file", str(self.source), "--check"]
        with patch("sys.stdout", new_callable=io.StringIO) as explicit:
            self.assertEqual(main(["-C", str(self.first), *args]), 0)
        with patch("layouter.cli.Path.cwd", return_value=self.first), patch("sys.stdout", new_callable=io.StringIO) as implicit:
            self.assertEqual(main(args), 0)
        self.assertEqual(explicit.getvalue(), implicit.getvalue())

    def test_saving_from_another_directory_finds_global_session(self):
        # Use a single app so this fixture only needs compositor read access.
        self.source.write_text('session="simple"\n[[workspace]]\nname="dev"\n'
                               '[[workspace.window]]\nname="app"\ncommand=["editor"]\n')
        launched = self.resolve(self.first)
        tree = {"id": 1, "type": "root", "nodes": [{"id": 2, "type": "output", "name": "DP-1", "nodes": [
            {"id": 3, "type": "workspace", "name": "dev", "num": -1, "layout": "splith", "nodes": [
                {"id": 4, "type": "con", "app_id": "editor", "marks": [launched.mark("app")], "nodes": []}]}]}]}
        mock = MagicMock()
        mock.__enter__.return_value = mock
        mock.tree.return_value = tree
        compositor = Compositor.__new__(Compositor)
        mock.resolve_node.side_effect = compositor.resolve_node
        with patch("layouter.cli.Compositor", return_value=mock), patch("sys.stdout", new_callable=io.StringIO), patch("sys.stderr", new_callable=io.StringIO) as errors:
            self.assertEqual(main(["-C", str(self.second), "--file", str(self.source), "--save-layout"]), 0, errors.getvalue())
        self.assertEqual(launched.session_id, self.resolve(self.second).session_id)
        self.assertTrue(self.source.with_suffix(".toml.bak").exists())
        mock.command.assert_not_called()
