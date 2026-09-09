from __future__ import annotations

import base64
import json
import os
import re
import shlex
import socket
import stat
import subprocess
import sys
from dataclasses import dataclass

from .errors import AmbiguousState, BackendError
from .i3 import marked, matches, walk
from .model import Node, Pane, Tab, Workflow
from .runtime import Runtime


@dataclass(frozen=True)
class LivePane:
    os_window: dict
    tab: dict
    window: dict


@dataclass(frozen=True)
class Snapshot:
    exists: bool
    os_windows: tuple[dict, ...] = ()
    stale_socket: bool = False

    def panes(self) -> list[LivePane]:
        return [LivePane(osw, tab, pane) for osw in self.os_windows
                for tab in osw["tabs"] for pane in tab["windows"]]

    def pane(self, key: str) -> LivePane | None:
        found = [p for p in self.panes() if p.window.get("user_vars", {}).get("layouter_pane") == key]
        if len(found) > 1:
            raise AmbiguousState(f"Duplicate live kitty pane identity {key}; refusing to guess")
        return found[0] if found else None

    def tab(self, workflow: Workflow, node: Node, tab: Tab) -> dict | None:
        # Prefer the first surviving declared pane, even after manual detachment.
        for pane in tab.panes:
            live = self.pane(workflow.pane_key(node.id, tab.id, pane.id))
            if live:
                return live.tab
        key = workflow.tab_key(node.id, tab.id)
        return next((p.tab for p in self.panes()
                     if p.window.get("user_vars", {}).get("layouter_tab") == key), None)


class Kitty:
    def __init__(self, workflow: Workflow, node: Node, runtime: Runtime):
        self.workflow, self.node, self.runtime = workflow, node, runtime
        self.path = runtime.socket(workflow.element_id(node.id))

    @property
    def wm_class(self) -> str:
        return self.node.wm_class or "layouter-" + self.workflow.element_id(self.node.id)

    @property
    def wm_instance(self) -> str:
        return "layouter-" + self.workflow.element_id(self.node.id)

    def i3_windows(self, tree: dict, *, marked_only: bool = False) -> list[dict]:
        mark = self.workflow.mark(self.node.id)
        exact_class = "^" + re.escape(self.wm_class) + "$"
        exact_instance = "^" + re.escape(self.wm_instance) + "$"
        return [n for n in walk(tree) if mark in n.get("marks", []) or (not marked_only and (
                matches(n, {"class": exact_class, "instance": exact_instance}) or
                matches(n, {"app_id": exact_class})))]

    def remote(self, *args: str) -> str:
        argv = [self.node.executable, "@", "--to", "unix:" + str(self.path),
                "--use-password", "never", *args]
        try:
            result = subprocess.run(argv, stdin=subprocess.DEVNULL, capture_output=True,
                                    text=True, timeout=self.workflow.timeout)
        except subprocess.TimeoutExpired as exc:
            raise BackendError(f"kitty {args[0]} timed out; its result is unknown. Inspect before retrying") from exc
        except OSError as exc:
            raise BackendError(f"Cannot invoke kitty remote control: {exc}") from exc
        if result.returncode:
            raise BackendError(f"kitty {args[0]} failed for {self.node.id}: {result.stderr.strip() or result.stdout.strip()}")
        return result.stdout.strip()

    def inspect(self, tree: dict) -> Snapshot:
        # A configured Wayland app ID need not be globally unique. When the
        # socket is absent, only our stable compositor mark proves ownership.
        windows = self.i3_windows(tree, marked_only=True)
        try:
            st = self.path.lstat()
        except FileNotFoundError:
            if windows:
                raise BackendError(
                    f"{self.node.id} exists in the compositor but its kitty socket is missing: {self.path}"
                )
            return Snapshot(False)
        if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid():
            raise BackendError(f"Refusing an unowned or non-socket kitty endpoint: {self.path}")
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(self.workflow.timeout)
                probe.connect(str(self.path))
        except (ConnectionRefusedError, FileNotFoundError) as exc:
            if windows:
                raise BackendError(f"{self.node.id} exists but its kitty socket is unreachable; no replacement launched") from exc
            return Snapshot(False, stale_socket=True)
        except OSError as exc:
            raise BackendError(f"Cannot inspect kitty socket {self.path}: {exc}") from exc
        try:
            state = json.loads(self.remote("ls"))
            if not isinstance(state, list):
                raise ValueError("ls must return an array")
            for osw in state:
                if not isinstance(osw.get("id"), int) or not isinstance(osw["tabs"], list):
                    raise ValueError("invalid OS window")
                for tab in osw["tabs"]:
                    if not isinstance(tab.get("id"), int) or not isinstance(tab["windows"], list):
                        raise ValueError("invalid tab")
                    for pane in tab["windows"]:
                        if not isinstance(pane.get("id"), int) or not isinstance(pane.get("user_vars", {}), dict):
                            raise ValueError("invalid pane")
            if not state:
                raise ValueError("live kitty reported no OS windows")
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise BackendError(f"Invalid kitty state for {self.node.id}: {exc}") from exc
        result = Snapshot(True, tuple(state))
        for tab in self.node.tabs:
            for pane in tab.panes:
                result.pane(self.workflow.pane_key(self.node.id, tab.id, pane.id))
        return result

    def variables(self, tab: Tab, pane: Pane) -> dict[str, str]:
        w, n = self.workflow, self.node
        return {"layouter_session": w.session_id, "layouter_node": w.element_id(n.id),
                "layouter_tab": w.tab_key(n.id, tab.id),
                "layouter_pane": w.pane_key(n.id, tab.id, pane.id)}

    def pane_env(self, tab: Tab, pane: Pane) -> dict[str, str]:
        return {**pane.env,
                "LAYOUTER_SESSION": self.workflow.session_id,
                "LAYOUTER_ELEMENT": pane.id,
                "LAYOUTER_PANE_ID": self.workflow.pane_key(
                    self.node.id, tab.id, pane.id)}

    @staticmethod
    def pane_command(pane: Pane) -> tuple[str, ...]:
        return pane.command or (pane.env.get("SHELL", os.environ.get("SHELL") or "/bin/sh"),)

    def startup_file(self, tab: Tab, pane: Pane):
        """Only the first desired pane: all other panes are reconciled via RC.

        Encode arbitrary argv/env/cwd rather than inserting them into kitty's
        line-oriented, environment-expanding session language.
        """
        self.runtime.ensure()
        payload = base64.b64encode(json.dumps({"command": self.pane_command(pane),
            "cwd": str(pane.cwd), "env": self.pane_env(tab, pane)}).encode()).decode()
        bootstrap = ("import base64,json,os,sys;d=json.loads(base64.b64decode(sys.argv[1]));"
                     "os.chdir(d['cwd']);os.environ.update(d['env']);"
                     "os.execvpe(d['command'][0],d['command'],os.environ)")
        launch = ["launch"]
        for key, value in self.variables(tab, pane).items():
            launch.append(f"--var={key}={value}")
        if pane.hold:
            launch.append("--hold")
        launch += ["--", sys.executable, "-c", bootstrap, payload]
        # No user-supplied strings enter session syntax except the validated layout.
        if not re.fullmatch(r"[A-Za-z0-9_:.;=+%/-]+", tab.layout):
            raise BackendError(f"Unsupported characters in kitty layout: {tab.layout!r}")
        path = self.runtime.path / (self.workflow.element_id(self.node.id) + ".session")
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write("new_tab layouter\nenabled_layouts " + ",".join(self.enabled_layouts(tab)) +
                       "\nlayout " + tab.layout + "\n" + shlex.join(launch) + "\n")
        return path

    @staticmethod
    def enabled_layouts(tab: Tab) -> tuple[str, ...]:
        # Fresh tabs retain the ability to switch layouts manually, even if the
        # user's global kitty configuration enabled only a different layout.
        layouts = [tab.layout, "tall", "fat", "grid", "horizontal", "vertical", "stack", "splits"]
        return tuple(dict.fromkeys(layouts))

    def start(self, i3, snapshot: Snapshot) -> Snapshot:
        n, w = self.node, self.workflow
        if snapshot.exists:
            raise BackendError("Refusing to recreate an existing kitty instance")
        self.runtime.ensure()
        if snapshot.stale_socket:
            # Recheck immediately; never unlink a newly live endpoint.
            current = self.inspect(i3.tree())
            if current.exists:
                return current
            if current.stale_socket:
                self.path.unlink(missing_ok=True)
        first_tab = n.tabs[0] if n.tabs else None
        first_pane = first_tab.panes[0] if first_tab else None
        session_file = n.session_file or self.startup_file(first_tab, first_pane)
        argv = [n.executable]
        if n.config:
            argv += ["--config", str(n.config)]
        for key, value in n.options.items():
            argv += ["--override", key + "=" + value]
        argv += ["--override", "allow_remote_control=socket-only",
                 "--listen-on", "unix:" + str(self.path), "--class", self.wm_class,
                 "--name", self.wm_instance, "--directory", str(n.cwd), "--session", str(session_file)]
        if first_pane and first_pane.title:
            argv += ["--title", first_pane.title]
        with i3.events() as events:
            baseline = {c["id"] for c in walk(i3.tree())}
            self.runtime.spawn(argv, n.cwd, {**n.env, "LAYOUTER_SESSION": w.session_id,
                                           "LAYOUTER_ELEMENT": w.element_id(n.id)}, w.element_id(n.id))
            created = i3.wait_new(events, baseline, {"class": "^" + re.escape(self.wm_class) + "$"}, n.id)
            i3.place_new(w, n, created, baseline)
        result = self.inspect(i3.tree())
        if not result.exists:
            raise BackendError(f"kitty {n.id} exited during startup")
        if first_tab:
            live = result.pane(w.pane_key(n.id, first_tab.id, first_pane.id))
            if not live:
                raise BackendError(f"Initial pane {n.id}.{first_tab.id}.{first_pane.id} exited during startup; "
                                   "use hold = true to retain short-lived output")
            self.remote("set-tab-title", "--match", f"id:{live.tab['id']}", "--", first_tab.title)
        return result

    def create_pane(self, tab: Tab, pane: Pane, snapshot: Snapshot):
        w, n = self.workflow, self.node
        if snapshot.pane(w.pane_key(n.id, tab.id, pane.id)):
            return
        live_tab = snapshot.tab(w, n, tab)
        args = ["launch", "--type", "window" if live_tab else "tab", "--keep-focus",
                "--cwd", str(pane.cwd)]
        if live_tab:
            args += ["--match", f"id:{live_tab['id']}"]
        else:
            # Always anchor tab creation to an actual tab in this process.
            anchors = snapshot.panes()
            if not anchors:
                raise BackendError(f"kitty {n.id} has no usable tab to anchor creation")
            args += ["--match", f"id:{anchors[0].tab['id']}", "--tab-title", tab.title]
        if pane.title:
            args += ["--title", pane.title]
        if pane.hold:
            args += ["--hold"]
        if live_tab:
            if pane.location != "default":
                args += ["--location", pane.location]
            if pane.after:
                neighbor = snapshot.pane(w.pane_key(n.id, tab.id, pane.after))
                if neighbor and neighbor.tab["id"] == live_tab["id"]:
                    args += ["--next-to", f"id:{neighbor.window['id']}"]
        for key, value in self.pane_env(tab, pane).items():
            args += ["--env", key + "=" + value]
        for key, value in self.variables(tab, pane).items():
            args += ["--var", key + "=" + value]
        args += ["--", *self.pane_command(pane)]
        result = self.remote(*args)
        if not result.isdecimal():
            raise BackendError(f"kitty launch returned no pane ID for {n.id}.{tab.id}.{pane.id}; result unknown")
        if live_tab is None:
            # Only configure a tab made by this call. Never change existing layouts.
            new_state = self._ls_after_launch()
            new_pane = new_state.pane(w.pane_key(n.id, tab.id, pane.id))
            if not new_pane:
                raise BackendError(f"Pane {pane.id} exited during creation; consider hold = true")
            old_tabs = {t["id"] for osw in snapshot.os_windows for t in osw["tabs"]}
            if new_pane.tab["id"] not in old_tabs:
                self.remote("set-enabled-layouts", "--match", f"id:{new_pane.tab['id']}", *self.enabled_layouts(tab))
                self.remote("goto-layout", "--match", f"id:{new_pane.tab['id']}", tab.layout)

    def _ls_after_launch(self) -> Snapshot:
        # inspect() performs the shared, strict validation; the socket already exists.
        return self.inspect({"id": 0, "nodes": []})

    def _tab_shape(self, tab: Tab, snapshot: Snapshot) -> tuple[bool, dict | None]:
        desired = [self.workflow.pane_key(self.node.id, tab.id, pane.id) for pane in tab.panes]
        panes = [snapshot.pane(key) for key in desired]
        if any(pane is None for pane in panes):
            return False, None
        target = panes[0].tab
        all_declared = {self.workflow.pane_key(self.node.id, declared.id, pane.id)
                        for declared in self.node.tabs for pane in declared.panes}
        actual = [pane.get("user_vars", {}).get("layouter_pane")
                  for pane in target.get("windows", [])
                  if pane.get("user_vars", {}).get("layouter_pane") in all_declared]
        return all(pane.tab["id"] == target["id"] for pane in panes) and actual == desired, target

    def sync_plan(self, snapshot: Snapshot) -> list[tuple[str, str]]:
        if self.node.session_file:
            return []
        result = []
        for tab in self.node.tabs:
            shape, live = self._tab_shape(tab, snapshot)
            differences = []
            if not shape:
                differences.append("pane membership/order")
            if live is not None and live.get("title") != tab.title:
                differences.append("title")
            if live is not None and live.get("layout") != tab.layout:
                differences.append("layout")
            if differences:
                result.append((f"{self.node.id}.{tab.id}", "kitty " + ", ".join(differences)))
        return result

    def sync(self, i3, snapshot: Snapshot) -> list[tuple[str, str]]:
        changes = self.sync_plan(snapshot)
        if self.node.session_file:
            return changes
        w, n = self.workflow, self.node
        for tab in n.tabs:
            state = self.inspect(i3.tree())
            shape, live_tab = self._tab_shape(tab, state)
            if live_tab is None:
                raise BackendError(f"Cannot synchronize incomplete kitty tab {n.id}.{tab.id}")
            rebuilt = not shape
            if not shape:
                first = state.pane(w.pane_key(n.id, tab.id, tab.panes[0].id))
                if first is None:
                    raise BackendError(f"Cannot synchronize missing kitty pane {n.id}.{tab.id}.{tab.panes[0].id}")
                self.remote("detach-window", "--match", f"id:{first.window['id']}",
                            "--target-tab", "new", "--stay-in-tab")
                state = self.inspect(i3.tree())
                first = state.pane(w.pane_key(n.id, tab.id, tab.panes[0].id))
                if first is None:
                    raise BackendError(f"Kitty lost pane {n.id}.{tab.id}.{tab.panes[0].id} during sync")
                target_id = int(first.tab["id"])
                for pane in tab.panes[1:]:
                    state = self.inspect(i3.tree())
                    current = state.pane(w.pane_key(n.id, tab.id, pane.id))
                    if current is None:
                        raise BackendError(f"Cannot synchronize missing kitty pane {n.id}.{tab.id}.{pane.id}")
                    if current.tab["id"] != target_id:
                        self.remote("detach-window", "--match", f"id:{current.window['id']}",
                                    "--target-tab", f"id:{target_id}", "--stay-in-tab")
                state = self.inspect(i3.tree())
                shape, live_tab = self._tab_shape(tab, state)
                if not shape or live_tab is None:
                    raise BackendError(f"Could not reconstruct kitty tab {n.id}.{tab.id}")
            target = f"id:{int(live_tab['id'])}"
            if rebuilt or live_tab.get("layout") != tab.layout:
                self.remote("set-enabled-layouts", "--match", target, *self.enabled_layouts(tab))
                self.remote("goto-layout", "--match", target, tab.layout)
            if live_tab.get("title") != tab.title:
                self.remote("set-tab-title", "--match", target, "--", tab.title)
        return changes

    def focus(self, target: str, i3, snapshot: Snapshot):
        parts = target.split(".")
        if len(parts) == 1:
            node = marked(i3.tree(), self.workflow.mark(self.node.id))
            if node:
                i3.focus(node["id"])
                return
            panes = snapshot.panes()
            live = panes[0] if panes else None
        else:
            tab = next(t for t in self.node.tabs if t.id == parts[1])
            if len(parts) == 3:
                live = snapshot.pane(self.workflow.pane_key(self.node.id, tab.id, parts[2]))
            else:
                live_tab = snapshot.tab(self.workflow, self.node, tab)
                live = next((p for p in snapshot.panes() if live_tab and p.tab["id"] == live_tab["id"]), None)
        if live is None:
            raise BackendError(f"Focus destination {target} no longer exists")
        self.remote("focus-window", "--match", f"id:{live.window['id']}")
        # Recent kitty versions expose the native X11 ID; follow detached panes.
        native_id = live.os_window.get("platform_window_id")
        container = next((c for c in walk(i3.tree()) if native_id and c.get("window") == native_id), None)
        if container:
            i3.focus(container["id"])
