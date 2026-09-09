import copy
import io
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from layouter.cli import main, parser
from layouter.config import expand, load, resolve
from layouter.errors import ConfigError


def basic():
    return {"workflows": {"default": {"nodes": {
        "code": {"type": "workspace", "name": "code"},
        "editor": {"type": "app", "parent": "code", "command": ["zed", "{project}"],
                   "match": {"class": "^zed$"}},
        "term": {"type": "kitty", "parent": "code", "tabs": {"dev": {"panes": {
            "one": {"command": ["printf", "%s", "hello"]}, "two": {"after": "one"}}}}},
    }}}}


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.project = Path(self.temp.name)

    def test_local_replaces_global_and_global_is_fallback(self):
        global_file = self.project / "global" / "default.toml"
        global_file.parent.mkdir()
        global_file.write_text('[[workspace]]\nname="global"\n'
                               '[[workspace.window]]\nname="t"\n'
                               'command=["old", "arg"]\nmatch={class="A"}\n')
        local = self.project / ".dev" / "default.toml"
        local.parent.mkdir()
        local.write_text('[[workspace]]\nname="global"\n'
                         '[[workspace.window]]\nname="t"\ncommand=["new"]\n')
        data, sources = load(self.project, global_file=global_file)
        workflow = resolve(data, self.project, sources=sources)
        self.assertEqual(workflow.by_id["t"].command, ("new",))
        self.assertEqual(workflow.by_id["t"].match, {})
        self.assertEqual(workflow.sources, (local,))
        local.unlink()
        self.assertEqual(resolve(load(self.project, global_file=global_file)[0], self.project).by_id["t"].command[0], "old")

    def test_alternate_file_is_relative_to_project(self):
        (self.project / "alternate.toml").write_text('session="alt"\n')
        data, _ = load(self.project, "alternate.toml", self.project / "missing")
        self.assertEqual(resolve(data, self.project).session, "alt")
        with self.assertRaises(ConfigError):
            load(self.project, "missing", self.project / "also-missing")

    def test_named_argument_interpolation_and_identity(self):
        data = basic()
        debug = copy.deepcopy(data["workflows"]["default"])
        debug.update(session="debug-{microfrontend}", args=[{"name": "microfrontend"}],
                     env={"MF": "{microfrontend}"})
        debug["nodes"]["editor"]["command"] = ["zed", "{project}/{microfrontend}"]
        data["workflows"]["debug"] = debug
        w = resolve(data, self.project, "debug", ["checkout"])
        self.assertEqual(w.session, "debug-checkout")
        self.assertEqual(w.by_id["term"].tabs[0].panes[0].env["MF"], "checkout")
        self.assertNotEqual(w.session_id, resolve(data, self.project, "debug", ["account"]).session_id)
        changed = copy.deepcopy(data)
        changed["workflows"]["debug"]["nodes"]["editor"]["command"] = ["different"]
        self.assertEqual(w.session_id, resolve(changed, self.project, "debug", ["checkout"]).session_id)
        link = self.project / "alias"
        link.symlink_to(self.project, target_is_directory=True)
        self.assertEqual(w.session_id, resolve(data, link, "debug", ["checkout"]).session_id)

    def test_readable_named_schema(self):
        (self.project / "debug.toml").write_text('''
session = "debug-{microfrontend}"
[args]
microfrontend = { position = 0, required = true }
[[workspace]]
number = 3
name = "dev"
layout = "splith"
  [[workspace.kitty]]
  name = "frontend"
  class = "dev-frontend"
  layout = "tall"
  size = 65
    [[workspace.kitty.pane]]
    name = "vite"
    command = ["npm", "run", "dev", "--", "--mf", "{microfrontend}"]
    [[workspace.kitty.pane]]
    name = "tests"
    title = "watch tests"
    command = ["npm", "test", "--", "--watch"]
[[workspace]]
number = 4
name = "browser"
  [[workspace.window]]
  name = "browser"
  command = ["firefox", "--new-window", "http://{microfrontend}.localhost:5173"]
''')
        data, sources = load(self.project, "debug.toml", self.project / "missing", workflow="debug")
        workflow = resolve(data, self.project, "debug", ["checkout"], sources)
        self.assertEqual(workflow.session, "debug-checkout")
        self.assertEqual(workflow.by_id["workspace-dev"].name, "3: dev")
        frontend = workflow.by_id["frontend"]
        self.assertEqual((frontend.wm_class, frontend.size), ("dev-frontend", 65.0))
        self.assertEqual([pane.id for pane in frontend.tabs[0].panes], ["vite", "tests"])
        self.assertEqual([pane.title for pane in frontend.tabs[0].panes], ["vite", "watch tests"])
        self.assertEqual(workflow.by_id["browser"].match, {})

    def test_complete_design_document_workflow_verbatim(self):
        source = self.project / "design.toml"
        source.write_text('''
session = "debug-{microfrontend}"
focus = "code"
[args]
microfrontend = { position = 0, required = true }
[[workspace]]
number = 2
name = "code"
  [[workspace.window]]
  name = "zed"
  command = ["zed", "-n", "."]
[[workspace]]
number = 3
name = "dev"
layout = "splith"
  [[workspace.kitty]]
  name = "frontend"
  class = "dev-frontend"
  layout = "tall"
  size = 65
    [[workspace.kitty.pane]]
    title = "vite"
    command = ["npm", "run", "dev", "--", "--mf", "{microfrontend}"]
    [[workspace.kitty.pane]]
    title = "tests"
    command = ["npm", "run", "test", "--", "--watch"]
    [[workspace.kitty.pane]]
    title = "types"
    command = ["npm", "run", "typecheck", "--", "--watch"]
  [[workspace.kitty]]
  name = "backend"
  class = "dev-backend"
  layout = "fat"
  size = 35
    [[workspace.kitty.pane]]
    title = "server"
    command = ["npm", "run", "backend"]
    [[workspace.kitty.pane]]
    title = "logs"
    command = ["npm", "run", "logs"]
[[workspace]]
number = 4
name = "browser"
  [[workspace.window]]
  name = "browser"
  command = ["firefox", "--new-window", "http://{microfrontend}.localhost:5173"]
''')
        config, sources = load(self.project, str(source), self.project / "missing", workflow="debug")
        workflow = resolve(config, self.project, "debug", ["checkout"], sources)
        self.assertEqual(workflow.session, "debug-checkout")
        self.assertEqual(workflow.focus, "workspace-code")
        self.assertEqual(workflow.by_id["workspace-code"].name, "2: code")
        self.assertEqual(workflow.by_id["frontend"].tabs[0].layout, "tall")
        self.assertEqual([p.id for p in workflow.by_id["frontend"].tabs[0].panes],
                         ["vite", "tests", "types"])
        self.assertEqual(workflow.by_id["browser"].command[-1], "http://checkout.localhost:5173")

    def test_unnamed_recursive_container_stacking_and_kitty_session(self):
        source = self.project / "tree.toml"
        source.write_text('''
[[workspace]]
number = 3
name = "dev"
layout = "stacking"
  [[workspace.container]]
  layout = "splitv"
  size = 35
    [[workspace.container.window]]
    name = "logs"
    command = ["logs"]
[[workspace]]
number = 4
name = "special"
  [[workspace.kitty]]
  name = "weird-one"
  session = ".dev/special.kitty"
''')
        config, _ = load(self.project, str(source), self.project / "missing")
        workflow = resolve(config, self.project)
        workspace = workflow.by_id["workspace-dev"]
        self.assertEqual(workspace.layout, "stacked")
        container = next(node for node in workflow.nodes if node.kind == "container")
        self.assertEqual((container.layout, container.size), ("splitv", 35.0))
        self.assertEqual(workflow.by_id["logs"].parent, container.id)
        self.assertEqual(workflow.by_id["weird-one"].session_file,
                         self.project / ".dev/special.kitty")

    def test_named_workflow_uses_local_or_global_as_whole_file(self):
        global_file = self.project / "global" / "default.toml"
        global_file.parent.mkdir()
        global_file.write_text('session="default"\n')
        global_named = global_file.parent / "debug.toml"
        global_named.write_text('session="global-{flavor}"\n'
                                '[args]\nflavor={position=0,required=true}\n'
                                '[env]\nSOURCE="global"\n')
        named = self.project / ".dev" / "debug.toml"
        named.parent.mkdir()
        named.write_text('session="local-{flavor}"\n'
                         '[args]\nflavor={position=0,required=true}\n'
                         '[env]\nSOURCE="local"\n')
        data, sources = load(self.project, global_file=global_file, workflow="debug")
        workflow = resolve(data, self.project, "debug", ["blue"], sources)
        self.assertEqual(workflow.session, "local-blue")
        self.assertEqual(workflow.by_id if workflow.nodes else {}, {})
        self.assertEqual(workflow.sources, (named,))
        self.assertEqual(workflow.arguments, {"flavor": "blue"})
        named.unlink()
        fallback_data, fallback_sources = load(self.project, global_file=global_file, workflow="debug")
        fallback = resolve(fallback_data, self.project, "debug", ["blue"], fallback_sources)
        self.assertEqual(fallback.session, "global-blue")
        self.assertEqual(fallback.sources, (global_named,))

    def test_force_global_and_explicit_file_bypass_local(self):
        global_file = self.project / "global" / "default.toml"
        global_file.parent.mkdir()
        global_file.write_text('session="global"\n')
        local = self.project / ".dev" / "default.toml"
        local.parent.mkdir()
        local.write_text('session="local"\n')
        explicit = self.project / "chosen.toml"
        explicit.write_text('session="explicit"\n')
        global_data, global_sources = load(self.project, global_file=global_file, force_global=True)
        self.assertEqual(resolve(global_data, self.project, sources=global_sources).session, "global")
        self.assertEqual(global_sources, (global_file,))
        file_data, file_sources = load(self.project, str(explicit), global_file=global_file)
        self.assertEqual(resolve(file_data, self.project, sources=file_sources).session, "explicit")
        self.assertEqual(file_sources, (explicit,))

    def test_expansion_is_not_recursive_and_shell_is_opt_in(self):
        value = "$(touch /tmp/not-run); {project} ' spaced"
        data = basic()
        data["workflows"]["default"].update(args=[{"name": "arg"}])
        data["workflows"]["default"]["nodes"]["editor"]["command"] = ["tool", "{arg}"]
        w = resolve(data, self.project, supplied=[value])
        self.assertEqual(w.by_id["editor"].command, ("tool", value))
        self.assertEqual(expand("{{literal}}/{arg:url}", {"arg": "a/b c"}, "x"), "{literal}/a%2Fb%20c")
        self.assertEqual(expand("{arg:q}", {"arg": "a'b"}, "x"), "'a'\"'\"'b'")

    def test_invalid_interpolation_rejected(self):
        for value in ["{missing}", "{arg.__class__}", "{arg!r}", "{arg:04}", "{"]:
            with self.subTest(value=value), self.assertRaises(ConfigError):
                expand(value, {"arg": "x"}, "field")

    def test_args_defaults_choices_and_errors(self):
        data = basic()
        wf = data["workflows"]["default"]
        wf["args"] = [{"name": "mode", "choices": ["a", "b"], "default": "a"}]
        self.assertEqual(resolve(data, self.project).arguments, {"mode": "a"})
        for supplied in [["c"], ["a", "b"]]:
            with self.assertRaises(ConfigError):
                resolve(data, self.project, supplied=supplied)
        wf["args"] = [{"name": "mode"}]
        with self.assertRaises(ConfigError):
            resolve(data, self.project)
        wf["args"] = [{"name": "session"}]
        with self.assertRaises(ConfigError):
            resolve(data, self.project, supplied=["x"])
        wf["args"] = [{"name": "a", "default": "a"}, {"name": "b"}]
        with self.assertRaises(ConfigError):
            resolve(data, self.project)

    def test_env_and_cwd_cascade(self):
        data = basic()
        wf = data["workflows"]["default"]
        wf["env"] = {"A": "workflow", "B": "workflow"}
        term = wf["nodes"]["term"]
        term["env"] = {"A": "node"}
        term["tabs"]["dev"]["env"] = {"A": "tab"}
        term["tabs"]["dev"]["panes"]["one"].update(env={"A": "pane"}, cwd="subdir")
        pane = resolve(data, self.project).by_id["term"].tabs[0].panes[0]
        self.assertEqual(pane.env, {"A": "pane", "B": "workflow"})
        self.assertEqual(pane.cwd, self.project / "subdir")

    def test_disabled_parent_omits_subtree(self):
        data = basic()
        data["workflows"]["default"]["nodes"]["code"]["enabled"] = False
        self.assertEqual(resolve(data, self.project).nodes, ())

    def test_invalid_graphs_and_unknown_keys(self):
        for change in [
            {"editor": {"parent": "unknown"}},
            {"editor": {"parent": "term"}},
            {"editor": {"tyop": "oops"}},
            {"cycle": {"type": "container", "parent": "cycle", "layout": "splith"}},
            {"editor": {"match": {"class": "["}}},
            {"term": {"session_file": "raw"}},
        ]:
            data = basic()
            for key, values in change.items():
                data["workflows"]["default"]["nodes"].setdefault(key, {}).update(values)
            with self.subTest(change=change), self.assertRaises(ConfigError):
                resolve(data, self.project)

    def test_pane_dependency_order_and_cycle(self):
        data = basic()
        panes = data["workflows"]["default"]["nodes"]["term"]["tabs"]["dev"]["panes"]
        panes["one"]["after"] = "two"
        panes["two"].pop("after")
        self.assertEqual([p.id for p in resolve(data, self.project).by_id["term"].tabs[0].panes], ["two", "one"])
        panes["two"]["after"] = "one"
        with self.assertRaises(ConfigError):
            resolve(data, self.project)

    def test_removed_public_syntax_is_rejected(self):
        source = self.project / "removed.toml"
        snippets = [
            'version = 1\n',
            'extends = "default"\n',
            '[workflows.default]\nsession = "x"\n',
            '[workflow.default]\nsession = "x"\n',
            '[nodes.code]\ntype = "workspace"\nname = "code"\n',
            '[[workspace]]\nname = "dev"\nlayout = "stacked"\n',
            '[[workspace]]\nname = "dev"\n[[workspace.kitty]]\nname = "term"\nraw_session = "x"\n',
        ]
        for content in snippets:
            source.write_text(content)
            with self.subTest(content=content), self.assertRaises(ConfigError):
                config, _ = load(self.project, str(source), self.project / "missing")
                resolve(config, self.project)

    def test_cli_preserves_workflow_flags(self):
        args = parser().parse_args(["-C", str(self.project), "-f", "x.toml", "debug", "checkout", "--help", "-C", "literal"])
        self.assertEqual(args.workflow, "debug")
        self.assertEqual(args.directory, str(self.project))
        self.assertEqual(args.workflow_args, ["checkout", "--help", "-C", "literal"])
        self.assertTrue(parser().parse_args(["--sync", "debug", "checkout"]).sync)

    def test_cli_directory_is_a_single_value(self):
        self.assertIsNone(parser().parse_args([]).directory)
        self.assertEqual(parser().parse_args(["-C", "first", "-C", "second"]).directory, "second")

    def test_file_and_global_are_mutually_exclusive(self):
        with patch("sys.stderr", new_callable=io.StringIO), self.assertRaises(SystemExit):
            parser().parse_args(["--file", "x.toml", "--global"])

    def test_cli_check_and_list_without_desktop(self):
        local = self.project / ".dev" / "default.toml"
        local.parent.mkdir()
        local.write_text('session="hello"\n')
        global_dir = self.project / "global" / "layouter"
        global_dir.mkdir(parents=True)
        (global_dir / "default.toml").write_text('session="global"\n')
        with patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.project / "global")}), patch("layouter.cli.Compositor") as backend:
            with patch("sys.stdout", new_callable=io.StringIO) as output:
                self.assertEqual(main(["-C", str(self.project), "--check"]), 0)
                self.assertIn("session='hello'", output.getvalue())
                self.assertEqual(main(["-C", str(self.project), "--global", "--check"]), 0)
                self.assertIn("session='global'", output.getvalue())
                self.assertEqual(main(["-C", str(self.project), "--list"]), 0)
            backend.assert_not_called()

    def test_cli_forwards_sync_to_reconciler(self):
        local = self.project / ".dev" / "default.toml"
        local.parent.mkdir()
        local.write_text('session="sync-test"\n')
        with patch("layouter.cli.Compositor") as compositor, \
             patch("layouter.cli.Reconciler") as reconciler, \
             patch("layouter.cli.Runtime") as runtime:
            live = compositor.return_value.__enter__.return_value
            live.path = "test-socket"
            reconciler.return_value.run.return_value = []
            self.assertEqual(main(["-C", str(self.project), "--sync"]), 0)
        reconciler.return_value.run.assert_called_once_with(no_focus=False, sync=True)


if __name__ == "__main__":
    unittest.main()
