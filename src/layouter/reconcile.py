"""Orchestrate gap filling and optional synchronization across desktop backends.

Planning reads live state; execution inspects it again before creating elements
to account for changes made while the invocation is running."""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from .errors import AmbiguousState, BackendError
from .i3 import is_window, marked, walk
from .kitty import Kitty
from .model import Node, Workflow
from .runtime import Runtime


@dataclass(frozen=True)
class Action:
    """A reportable planning or execution result for a declared element."""
    kind: str
    target: str
    detail: str = ""


def check_executable(command: tuple[str, ...] | list[str], cwd: Path, env: dict):
    """Check a launch directory and executable using the intended process environment."""
    if not cwd.is_dir():
        raise BackendError(f"Working directory does not exist: {cwd}")
    executable = command[0]
    if "/" in executable:
        path = Path(executable)
        if not path.is_absolute():
            path = cwd / path
        found = path.is_file() and os.access(path, os.X_OK)
    else:
        found = shutil.which(executable, path=env.get("PATH", os.environ.get("PATH", os.defpath)))
    if not found:
        raise BackendError(f"Executable not found: {executable!r} (working directory: {cwd})")


class Reconciler:
    """Coordinate backend operations while preserving existing processes and unmanaged elements."""
    def __init__(self, workflow: Workflow, i3, runtime: Runtime,
                 kitty_factory=Kitty, emit=None):
        self.workflow, self.i3, self.runtime = workflow, i3, runtime
        self.kitties = {n.id: kitty_factory(workflow, n, runtime) for n in workflow.leaves if n.kind == "kitty"}
        self.emit = emit or (lambda action: None)
        self.actions: list[Action] = []

    def record(self, kind: str, target: str, detail: str = ""):
        """Append an executed action and forward it to the progress callback."""
        action = Action(kind, target, detail)
        self.actions.append(action)
        self.emit(action)

    def existing_app(self, node: Node):
        """Find an application by mark and reject marks attached to structural containers."""
        live = marked(self.i3.tree(), self.workflow.mark(node.id))
        if live and not is_window(live):
            raise BackendError(f"{node.id}: application identity belongs to a windowless container")
        return live

    def inspect(self):
        """Inspect all managed kitty endpoints before any launch can occur."""
        tree = self.i3.tree()
        # Surface inspection failures before any application launch.
        snapshots = {key: kitty.inspect(tree) for key, kitty in self.kitties.items()}
        return snapshots

    def plan(self, *, sync: bool = False) -> list[Action]:
        """Describe required creation, adoption, and optional corrections without mutating the desktop."""
        snapshots = self.inspect()
        result = []
        claimed = set()
        for node in self.workflow.leaves:
            if node.kind == "app":
                live = self.existing_app(node)
                adopt = self.i3.adoptable(node.match) if not live and node.adopt else None
                if adopt:
                    if adopt["id"] in claimed:
                        raise AmbiguousState("Two app declarations would adopt the same window")
                    claimed.add(adopt["id"])
                result.append(Action("keep" if live else "adopt" if adopt else "create", node.id, "app"))
            else:
                state = snapshots[node.id]
                result.append(Action("keep" if state.exists else "create", node.id, "kitty OS window"))
                for tab in node.tabs:
                    live_tab = state.tab(self.workflow, node, tab)
                    result.append(Action("keep" if live_tab else "create", f"{node.id}.{tab.id}", "tab"))
                    for pane in tab.panes:
                        present = state.pane(self.workflow.pane_key(node.id, tab.id, pane.id))
                        result.append(Action("keep" if present else "create", f"{node.id}.{tab.id}.{pane.id}", "pane"))
        if sync:
            for target, detail in self.i3.sync_plan(self.workflow):
                result.append(Action("sync", target, detail))
            for key, kitty in self.kitties.items():
                for target, detail in kitty.sync_plan(snapshots[key]):
                    result.append(Action("sync", target, detail))
        return result

    def preflight(self, plan: list[Action]):
        """Validate commands and referenced files only for elements the plan would create."""
        missing = {a.target for a in plan if a.kind == "create"}
        for node in self.workflow.leaves:
            if node.id in missing:
                argv = node.command if node.kind == "app" else (node.executable,)
                check_executable(argv, node.cwd, node.env)
                for path in (node.config, node.session_file):
                    if path and not path.is_file():
                        raise BackendError(f"Referenced file does not exist: {path}")
            for tab in node.tabs:
                for pane in tab.panes:
                    if f"{node.id}.{tab.id}.{pane.id}" in missing:
                        check_executable(Kitty.pane_command(pane), pane.cwd, pane.env)

    def app(self, node: Node):
        """Keep, adopt, or launch one application, placing only newly discovered windows."""
        w = self.workflow
        if self.existing_app(node):
            self.record("keep", node.id, "app")
            return
        if node.adopt:
            candidate = self.i3.adoptable(node.match)
            if candidate:
                self.i3.mark(candidate, w.mark(node.id))
                self.record("adopt", node.id, "identity added; placement preserved")
                return
        self.i3.prepare_launch(w, node)
        with self.i3.events() as events:
            baseline = {n["id"] for n in walk(self.i3.tree())}
            self.runtime.spawn(node.command, node.cwd,
                {**node.env, "LAYOUTER_SESSION": w.session_id, "LAYOUTER_ELEMENT": w.element_id(node.id)},
                w.element_id(node.id))
            created = self.i3.wait_new(events, baseline, node.match, node.id)
            self.i3.place_new(w, node, created, baseline)
        self.record("create", node.id, "app")

    def kitty(self, node: Node):
        """Ensure a kitty OS window exists and fill gaps in its inline pane declarations."""
        w, backend = self.workflow, self.kitties[node.id]
        state = backend.inspect(self.i3.tree())
        if state.exists:
            self.record("keep", node.id, "kitty OS window")
            # Recover an interrupted launch using the deterministic X11 class.
            if not marked(self.i3.tree(), w.mark(node.id)):
                candidates = backend.i3_windows(self.i3.tree())
                if len(candidates) == 1:
                    self.i3.mark(candidates[0], w.mark(node.id))
        else:
            self.i3.prepare_launch(w, node)
            state = backend.start(self.i3, state)
            self.record("create", node.id, "kitty OS window")
        if node.session_file:
            # Raw session contents are opaque: only the OS window is managed.
            return
        for tab in node.tabs:
            for pane in tab.panes:
                # Fresh discovery, not a cached creation list: preserve manual edits.
                state = backend.inspect(self.i3.tree())
                if not state.exists:
                    raise BackendError(f"kitty {node.id} closed during reconciliation; invoke again to recreate")
                target = f"{node.id}.{tab.id}.{pane.id}"
                if state.pane(w.pane_key(node.id, tab.id, pane.id)):
                    self.record("keep", target, "pane")
                else:
                    backend.create_pane(tab, pane, state)
                    self.record("create", target, "pane")

    def focus(self):
        """Resolve the configured focus target through the appropriate backend."""
        target = self.workflow.focus
        if not target:
            return
        root = target.split(".")[0]
        node = self.workflow.by_id[root]
        if node.kind == "kitty":
            backend = self.kitties[root]
            backend.focus(target, self.i3, backend.inspect(self.i3.tree()))
        else:
            live = self.i3.find(self.workflow.mark(root))
            if not live:
                raise BackendError(f"Focus destination {target} does not exist")
            self.i3.focus(live["id"])

    def run(self, *, no_focus: bool = False, preflight: bool = True,
            sync: bool = False) -> list[Action]:
        """Fill gaps, optionally synchronize, and restore original focus when required or on failure."""
        plan = self.plan(sync=sync)
        if preflight:
            self.preflight(plan)
        original = self.i3.focused()
        succeeded = False
        try:
            for node in self.workflow.leaves:
                self.app(node) if node.kind == "app" else self.kitty(node)
            if sync:
                for key, backend in self.kitties.items():
                    state = backend.inspect(self.i3.tree())
                    for target, detail in backend.sync(self.i3, state):
                        self.record("sync", target, detail)
                for target, detail in self.i3.sync(self.workflow):
                    self.record("sync", target, detail)
            else:
                # Ordinary reconciliation may assemble new groups, but must leave
                # missing nesting alone if rebuilding it would move existing windows.
                for container in self.i3.finalize(self.workflow) or []:
                    self.record("preserve", container.id,
                                "declared nesting skipped because rebuilding it would move an existing element")
            if self.workflow.focus and not no_focus:
                self.focus()
            succeeded = True
        finally:
            if original is not None and (not succeeded or no_focus or not self.workflow.focus):
                # Avoid even a redundant focus command on an already-converged run.
                try:
                    if self.i3.focused() != original:
                        self.i3.focus(original)
                except BackendError:
                    # Preserve the original failure if focus restoration also fails.
                    if succeeded:
                        raise
        return self.actions
