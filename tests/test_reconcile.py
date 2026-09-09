import copy
from contextlib import contextmanager
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest

from layouter.config import resolve
from layouter.errors import AmbiguousState, BackendError
from layouter.i3 import marked, matches, unique, walk
from layouter.kitty import Snapshot
from layouter.reconcile import Reconciler


def specification():
    return {"workflows": {"default": {"nodes": {
        "code": {"type": "workspace", "name": "code"},
        "editor": {"type": "app", "parent": "code", "command": ["zed"], "match": {"class": "^zed$"}},
        "runners": {"type": "kitty", "parent": "code", "tabs": {"dev": {"layout": "splits", "panes": {
            "vite": {"command": ["vite"]}, "tests": {"command": ["tests"], "after": "vite", "location": "hsplit"},
            "typecheck": {"command": ["typecheck"]},
        }}}},
        "browser": {"type": "app", "parent": "code", "command": ["firefox", "--new-window", "url"],
                    "match": {"class": "^firefox$"}},
    }}}}


class FakeI3:
    def __init__(self, workflow):
        self.workflow = workflow
        self.value = {"id": 0, "nodes": [], "floating_nodes": []}
        self.mutations = []
        self.subscribed = False
        self.focus_id = 99
        self.counter = 100
        self.sync_changes = []

    def add(self, key=None, *, wm_class="kitty", destination="nodes"):
        self.counter += 1
        node = {"id": self.counter, "window": self.counter + 1000, "name": "user-renamed",
                "window_properties": {"class": wm_class},
                "marks": [self.workflow.mark(key), "user-mark"] if key else [],
                "rect": {"x": 200, "width": 321}, "nodes": []}
        self.value[destination].append(node)
        return node

    def tree(self):
        return copy.deepcopy(self.value)

    def find(self, mark):
        return marked(self.tree(), mark)

    def mark(self, node, mark):
        existing = next(n for n in walk(self.value) if n["id"] == node["id"])
        existing["marks"].append(mark)
        self.mutations.append(("mark", node["id"]))

    def focused(self):
        return self.focus_id

    def focus(self, con_id):
        self.focus_id = con_id
        self.mutations.append(("focus", con_id))

    def adoptable(self, patterns):
        return unique([n for n in walk(self.tree()) if matches(n, patterns) and not n["marks"]], "adoption")

    def prepare_launch(self, workflow, node):
        self.mutations.append(("prepare", node.id))

    def finalize(self, workflow):
        pass

    def sync_plan(self, workflow):
        return list(self.sync_changes)

    def sync(self, workflow):
        self.mutations.append(("sync", workflow.name))
        return list(self.sync_changes)

    @contextmanager
    def events(self):
        self.subscribed = True
        try:
            yield self
        finally:
            self.subscribed = False

    def wait_new(self, events, baseline, patterns, description):
        self.assert_subscribed()
        return unique([n for n in walk(self.tree()) if n["id"] not in baseline and matches(n, patterns)], description)

    def assert_subscribed(self):
        if not self.subscribed:
            raise AssertionError("launch without event subscription")

    def place_new(self, workflow, node, created, baseline):
        if created["id"] in baseline:
            raise AssertionError("attempted move of preexisting window")
        self.mark(created, workflow.mark(node.id))
        self.mutations.append(("place", created["id"]))


class FakeRuntime:
    def __init__(self, i3):
        self.i3 = i3
        self.launches = []

    def spawn(self, argv, cwd, env, element_id):
        self.i3.assert_subscribed()
        self.launches.append((argv, cwd, env))
        self.i3.add(wm_class="firefox" if argv[0] == "firefox" else "zed")


class FakeKitty:
    def __init__(self, workflow, node, runtime):
        self.workflow, self.node = workflow, node
        self.data = [{"id": 1, "tabs": [{"id": 10, "title": "user renamed", "layout": "stack", "windows": []}]}]
        self.exists = True
        self.error = None
        self.created = []
        self.starts = 0
        self.counter = 50
        self.sync_changes = []

    def add(self, name, *, tab=None):
        tab = tab or self.data[0]["tabs"][0]
        self.counter += 1
        window = {"id": self.counter, "title": "user title", "cwd": "/changed",
                  "user_vars": {"layouter_pane": self.workflow.pane_key(self.node.id, "dev", name),
                                "layouter_tab": self.workflow.tab_key(self.node.id, "dev")}}
        tab["windows"].append(window)
        return window

    def inspect(self, tree):
        if self.error:
            raise self.error
        return Snapshot(self.exists, tuple(copy.deepcopy(self.data)) if self.exists else ())

    def i3_windows(self, tree):
        return [n for n in walk(tree) if self.workflow.mark(self.node.id) in n.get("marks", [])]

    def create_pane(self, tab, pane, state):
        target = state.tab(self.workflow, self.node, tab)
        self.created.append((tab.id, pane.id, target["id"] if target else None))
        actual = next(t for o in self.data for t in o["tabs"] if t["id"] == target["id"])
        self.add(pane.id, tab=actual)

    def start(self, i3, snapshot):
        self.starts += 1
        self.exists = True
        i3.add(self.node.id)
        return self.inspect(i3.tree())

    def focus(self, target, i3, snapshot):
        i3.mutations.append(("kitty-focus", target))

    def sync_plan(self, snapshot):
        return list(self.sync_changes)

    def sync(self, i3, snapshot):
        return list(self.sync_changes)


class ReconcileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.data = specification()
        self.build()

    def build(self):
        self.workflow = resolve(self.data, Path(self.temp.name))
        self.i3 = FakeI3(self.workflow)
        self.runtime = FakeRuntime(self.i3)
        self.reconciler = Reconciler(self.workflow, self.i3, self.runtime, FakeKitty)
        self.kitty = self.reconciler.kitties["runners"]

    def baseline(self):
        self.i3.add("editor", wm_class="zed", destination="floating_nodes")
        self.i3.add("runners")
        self.i3.add(wm_class="unmanaged")
        self.kitty.add("vite")
        self.kitty.add("typecheck")
        self.kitty.data[0]["tabs"][0]["windows"].append({"id": 999, "user_vars": {}, "title": "unmanaged shell"})

    def test_only_tests_pane_and_firefox_created_then_second_run_does_nothing(self):
        self.baseline()
        before_i3 = self.i3.tree()
        before_panes = copy.deepcopy(self.kitty.data[0]["tabs"][0]["windows"])
        actions = self.reconciler.run(preflight=False)
        created = {a.target for a in actions if a.kind == "create"}
        self.assertEqual(created, {"runners.dev.tests", "browser"})
        self.assertEqual(self.kitty.created, [("dev", "tests", 10)])
        self.assertEqual(self.kitty.starts, 0)
        self.assertEqual(len(self.runtime.launches), 1)
        for old in walk(before_i3):
            if old["id"]:
                self.assertEqual(old, next(n for n in walk(self.i3.tree()) if n["id"] == old["id"]))
        self.assertEqual(self.kitty.data[0]["tabs"][0]["windows"][:3], before_panes)
        self.assertEqual(self.kitty.data[0]["tabs"][0]["layout"], "stack")
        mutations = copy.deepcopy(self.i3.mutations)
        self.reconciler.actions = []
        self.assertTrue(all(a.kind == "keep" for a in self.reconciler.run(preflight=False)))
        self.assertEqual(self.i3.mutations, mutations)
        self.assertEqual(len(self.runtime.launches), 1)
        self.assertEqual(len(self.kitty.created), 1)

    def test_dry_run_is_read_only_and_reports_missing_pane(self):
        self.baseline()
        plan = self.reconciler.plan()
        self.assertEqual({a.target for a in plan if a.kind == "create"}, {"browser", "runners.dev.tests"})
        self.assertEqual(self.runtime.launches, [])
        self.assertEqual(self.i3.mutations, [])
        self.assertEqual(self.kitty.created, [])

    def test_inspection_failure_blocks_creation_everywhere(self):
        self.kitty.error = BackendError("unreachable socket")
        with self.assertRaisesRegex(BackendError, "unreachable"):
            self.reconciler.run(preflight=False)
        self.assertEqual(self.runtime.launches, [])
        self.assertEqual(self.i3.mutations, [])
        self.assertEqual(self.kitty.starts, 0)

    def test_detached_panes_are_found_and_not_moved(self):
        self.baseline()
        old_tab = self.kitty.data[0]["tabs"][0]
        detached = {"id": 2, "tabs": [{"id": 22, "layout": "grid", "windows": [old_tab["windows"].pop(0)]}]}
        self.kitty.data.append(detached)
        self.reconciler.run(preflight=False)
        self.assertEqual(self.kitty.created, [("dev", "tests", 22)])
        self.assertEqual([w["id"] for w in old_tab["windows"]], [52, 999])
        self.assertEqual(detached["tabs"][0]["layout"], "grid")

    def test_adoption_only_marks_and_does_not_place(self):
        self.data["workflows"]["default"]["nodes"]["editor"]["adopt"] = True
        self.build()
        self.i3.add("runners")
        self.i3.add("browser", wm_class="firefox")
        existing = self.i3.add(wm_class="zed")
        for key in ("vite", "tests", "typecheck"):
            self.kitty.add(key)
        self.reconciler.run(preflight=False)
        self.assertEqual(self.runtime.launches, [])
        self.assertEqual(self.i3.mutations, [("mark", existing["id"])])

    def test_ambiguous_adoption_refuses_to_guess(self):
        self.data["workflows"]["default"]["nodes"]["editor"]["adopt"] = True
        self.build()
        self.i3.add(wm_class="zed")
        self.i3.add(wm_class="zed")
        with self.assertRaises(AmbiguousState):
            self.reconciler.run(preflight=False)
        self.assertEqual(self.i3.mutations, [])

    def test_changed_commands_do_not_relaunch_present_elements(self):
        self.baseline()
        self.i3.add("browser", wm_class="firefox")
        self.kitty.add("tests")
        # None of the deliberately nonexistent executables need preflight when present.
        self.assertTrue(all(a.kind == "keep" for a in self.reconciler.run()))
        self.assertEqual(self.i3.mutations, [])

    def test_session_file_existing_instance_is_opaque(self):
        node = self.data["workflows"]["default"]["nodes"]["runners"]
        node.pop("tabs")
        node["session_file"] = "does-not-need-to-exist-when-already-running"
        self.build()
        for key in ("editor", "runners", "browser"):
            self.i3.add(key)
        self.reconciler.run()
        self.assertEqual(self.kitty.created, [])
        self.assertEqual(self.kitty.starts, 0)

    def test_no_focus_suppresses_requested_destination(self):
        self.data["workflows"]["default"]["focus"] = "runners.dev.vite"
        self.build()
        self.baseline()
        self.reconciler.run(no_focus=True, preflight=False)
        self.assertFalse(any(m[0] == "kitty-focus" for m in self.i3.mutations))

    def test_preserved_nesting_difference_is_reported(self):
        self.baseline()
        self.i3.add("browser", wm_class="firefox")
        self.kitty.add("tests")
        self.i3.finalize = lambda workflow: [SimpleNamespace(id="tools")]
        actions = self.reconciler.run(preflight=False)
        warning = next(action for action in actions if action.kind == "preserve")
        self.assertEqual(warning.target, "tools")
        self.assertIn("would move an existing element", warning.detail)

    def test_sync_runs_corrective_backends_and_reports_changes(self):
        self.baseline()
        self.i3.add("browser", wm_class="firefox")
        self.kitty.add("tests")
        self.i3.sync_changes = [("compositor", "managed placement")]
        self.kitty.sync_changes = [("runners.dev", "kitty layout")]

        actions = self.reconciler.run(preflight=False, sync=True)

        self.assertIn(("sync", "default"), self.i3.mutations)
        self.assertEqual({(action.target, action.detail) for action in actions if action.kind == "sync"},
                         {("compositor", "managed placement"), ("runners.dev", "kitty layout")})
        self.assertFalse(any(action.kind == "preserve" for action in actions))

    def test_sync_dry_run_reports_corrections_without_mutation(self):
        self.baseline()
        self.i3.sync_changes = [("compositor", "managed placement")]
        self.kitty.sync_changes = [("runners.dev", "kitty layout")]

        actions = self.reconciler.plan(sync=True)

        self.assertEqual({action.target for action in actions if action.kind == "sync"},
                         {"compositor", "runners.dev"})
        self.assertEqual(self.i3.mutations, [])


if __name__ == "__main__":
    unittest.main()
