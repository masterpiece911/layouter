"""Invocation-driven Firefox-family ownership, routing, and declared tab reconciliation."""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import socket
import stat
import threading
import time

from .errors import AmbiguousState, BackendError
from .firefox_host import FIREFOX_PROTOCOL_VERSION, MAX_MESSAGE
from .i3 import is_window, marked, walk
from .model import FirefoxTab, Node, Workflow
from .runtime import Runtime


@dataclass(frozen=True)
class Companion:
    path: Path
    timeout: float

    def call(self, op: str, **values):
        request = {"id": secrets.token_hex(16), "version": FIREFOX_PROTOCOL_VERSION, "op": op, **values}
        try:
            with socket.socket(socket.AF_UNIX) as sock:
                sock.settimeout(self.timeout)
                sock.connect(str(self.path))
                sock.sendall(json.dumps(request).encode() + b"\n")
                with sock.makefile("rb") as stream:
                    data = stream.readline(MAX_MESSAGE + 1)
            if len(data) > MAX_MESSAGE or not data.endswith(b"\n"):
                raise ValueError("invalid response framing")
            result = json.loads(data)
            if not isinstance(result, dict):
                raise ValueError("response is not an object")
            if type(result.get("version")) is not int or result["version"] != FIREFOX_PROTOCOL_VERSION:
                raise BackendError(f"Companion protocol version {result.get('version')} does not match "
                                   f"Layouter protocol version {FIREFOX_PROTOCOL_VERSION}")
            if result.get("id") != request["id"] or type(result.get("ok")) is not bool:
                raise ValueError("invalid response envelope")
            if not result["ok"]:
                error = AmbiguousState if result.get("code") == "AmbiguousState" else BackendError
                raise error(str(result.get("message", "Firefox companion failed")))
            return result.get("result")
        except (OSError, ValueError) as exc:
            raise BackendError(f"Cannot inspect Firefox companion {self.path}: {exc}. "
                               "Check the native host and extension connection; invoke again.") from exc


@dataclass(frozen=True)
class FirefoxTabState:
    """Structural wire metadata only; never add browser-derived state here."""
    id: int
    identity: str


@dataclass(frozen=True)
class FirefoxWindowState:
    owner: Companion | None = None
    browser_window_id: int | None = None
    compositor_window: dict | None = None
    tabs: tuple[FirefoxTabState, ...] = ()

    def tab(self, declaration: FirefoxTab) -> FirefoxTabState | None:
        found = [tab for tab in self.tabs if tab.identity == declaration.id]
        if len(found) > 1:
            raise AmbiguousState(f"Multiple scoped FirefoxTabs claim {declaration.id!r}; refusing to guess")
        return found[0] if found else None


class FirefoxRegistry:
    """Discover private live endpoints anew. A failed connection is never absence."""
    def __init__(self, runtime: Runtime, timeout: float):
        self.path, self.timeout = runtime.path / "firefox", timeout

    def companions(self) -> list[Companion]:
        if not self.path.exists():
            return []
        for path in (self.path.parent, self.path):
            st = path.lstat()
            if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise BackendError(f"Firefox runtime directory must be owned and private: {path}")
        result = []
        for path in sorted(self.path.glob("*.sock")):
            st = path.lstat()
            if not stat.S_ISSOCK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
                raise BackendError(f"Unsafe Firefox companion socket: {path}")
            companion = Companion(path, self.timeout)
            hello = companion.call("hello")
            if not isinstance(hello, dict) or hello.get("instanceId") != path.stem:
                raise BackendError(f"Firefox companion instance identity mismatch: {path}")
            result.append(companion)
        return result

    def inspect(self, key: str, name: str) -> FirefoxWindowState:
        owners, live = [], []
        for companion in self.companions():
            data = companion.call("inspect", windowElementId=key)
            try:
                if type(data["owned"]) is not bool:
                    raise ValueError("invalid ownership")
                window_id = data["windowId"]
                if window_id is not None and type(window_id) is not int:
                    raise ValueError("invalid window ID")
                tabs = tuple(FirefoxTabState(**tab) for tab in data["tabs"])
                if any(type(tab.id) is not int or not isinstance(tab.identity, str)
                       or not tab.identity for tab in tabs):
                    raise ValueError("invalid tab state")
                if window_id is None and tabs:
                    raise ValueError("tabs without a live window")
                if len({tab.identity for tab in tabs}) != len(tabs):
                    raise AmbiguousState(f"Multiple scoped tabs in FirefoxWindow {name!r}; refusing to guess")
            except (KeyError, TypeError, ValueError) as exc:
                raise BackendError(f"Invalid Firefox companion state: {exc}") from exc
            if data["owned"]:
                owners.append(companion)
            if window_id is not None:
                live.append(FirefoxWindowState(companion, window_id, tabs=tabs))
        if len(owners) > 1:
            raise AmbiguousState(f"Multiple companion instances claim FirefoxWindow {name!r}; refusing to guess")
        if len(live) > 1:
            raise AmbiguousState(f"Multiple live browser windows carry FirefoxWindow identity {name!r}; refusing to guess")
        if live:
            if owners != [live[0].owner]:
                raise BackendError(f"FirefoxWindow {name!r} has inconsistent companion ownership; refusing to guess")
            return live[0]
        return FirefoxWindowState(owners[0] if owners else None)

    @contextmanager
    def route(self, workflow: Workflow, node: Node, runtime: Runtime):
        """Route opaque argv through one unguessable local URL; remove only that tab."""
        nonce = secrets.token_hex(32)
        expected_path = "/layouter/" + nonce
        wake = threading.Event()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                body = b"<!doctype html><title>Layouter routing</title>Layouter is connecting this browser."
                self.send_response(200 if self.path == expected_path else 404)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                wake.set()

            def log_message(self, *args):
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        url = f"http://127.0.0.1:{server.server_port}{expected_path}"
        selected = None
        try:
            runtime.spawn((node.executable, *node.args, url), node.cwd, node.env, workflow.element_key(node))
            deadline = time.monotonic() + workflow.timeout
            while True:
                found = [(companion, tab_id) for companion in self.companions()
                         for tab_id in companion.call("find-bootstrap", url=url)]
                if len(found) > 1:
                    raise AmbiguousState("Multiple Firefox bootstrap matches; refusing to guess")
                if found:
                    selected = found[0]
                    # Recheck all owners after launching: normal session restoration may have finished.
                    state = self.inspect(workflow.element_key(node), node.id)
                    if state.owner is not None and state.owner != selected[0]:
                        raise AmbiguousState("Bootstrap companion conflicts with restored ownership; refusing to guess")
                    selected[0].call("claim-window", windowElementId=workflow.element_key(node))
                    yield selected[0]
                    return
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BackendError("Firefox-family companion extension is not available for the browser "
                                       "selected by executable + args. Install/connect the extension and native host.")
                # Poll fresh companion state, including hosts that connect after the HTTP request.
                wake.wait(min(0.1, remaining))
                wake.clear()
        finally:
            try:
                if selected:
                    selected[0].call("remove-bootstrap", url=url, tabId=selected[1])
            finally:
                server.shutdown()
                server.server_close()
                thread.join()


class FirefoxWindowBackend:
    """Keep browser identities distinct from compositor placement and creation URLs."""
    def __init__(self, workflow: Workflow, node: Node, runtime: Runtime, registry=None):
        self.workflow, self.node, self.runtime = workflow, node, runtime
        self.registry = registry or FirefoxRegistry(runtime, workflow.timeout)
        self.key = workflow.element_key(node)

    def inspect(self, tree: dict) -> FirefoxWindowState:
        state = self.registry.inspect(self.key, self.node.id)
        native = marked(tree, self.workflow.mark_for(self.node))
        if native and not is_window(native):
            raise BackendError(f"{self.node.id}: Firefox identity belongs to a windowless container")
        if native and state.browser_window_id is None:
            raise BackendError(f"{self.node.id}: marked Firefox window has no live companion identity; "
                               "check the extension and native host connection")
        return FirefoxWindowState(state.owner, state.browser_window_id, native, state.tabs)

    def call(self, owner, op, **values):
        return owner.call(op, windowElementId=self.key, **values)

    def correlate(self, i3, state):
        nonce = secrets.token_hex(32)
        with i3.events() as events:
            failure = None
            try:
                self.call(state.owner, "probe-window", windowId=state.browser_window_id, nonce=nonce)
                return i3.wait_for_title_token(events, f"__layouter_probe_{nonce}__", self.node.id)
            except BaseException as exc:
                failure = exc
                raise
            finally:
                try:
                    self.call(state.owner, "clear-probe", windowId=state.browser_window_id)
                except BackendError as cleanup:
                    if failure is None:
                        raise
                    if isinstance(failure, BackendError):
                        failure.args = (f"{failure}; also could not clear title probe: {cleanup}",)

    def ensure(self, i3):
        state = self.inspect(i3.tree())
        if state.browser_window_id is not None:
            if not state.compositor_window:
                native = self.correlate(i3, state)
                i3.claim_existing(self.workflow, self.node, native)
                return "recover"
            return "keep"
        if state.owner:
            return self.create(i3, state.owner)
        with self.registry.route(self.workflow, self.node, self.runtime) as owner:
            # Routing can start a browser whose own session restoration recovers this identity.
            state = self.inspect(i3.tree())
            if state.browser_window_id is not None:
                if not state.compositor_window:
                    i3.claim_existing(self.workflow, self.node, self.correlate(i3, state))
                return "recover"
            return self.create(i3, owner)

    def create(self, i3, owner):
        i3.prepare_launch(self.workflow, self.node)
        baseline = {n["id"] for n in walk(i3.tree())}
        initial = {"tab": asdict(self.node.firefox_tabs[0])} if self.node.firefox_tabs else {}
        self.call(owner, "create-window", **initial)
        # Assign tabs before probing, so an active declared page can supply its native title.
        self.reconcile_tabs(i3, sync=False)
        state = self.inspect(i3.tree())
        native = self.correlate(i3, state)
        i3.place_new(self.workflow, self.node, native, baseline)
        return "create"

    def plan(self, tree, sync=False):
        state = self.inspect(tree)
        yield ("keep" if state.compositor_window else "recover" if state.browser_window_id is not None
               else "create", self.node.id, "Firefox window")
        for tab in self.node.firefox_tabs:
            live = state.tab(tab)
            yield ("sync" if sync and live else "keep" if live else "create",
                   f"{self.node.id}.{tab.id}", "Firefox tab")

    def reconcile_tabs(self, i3, *, sync):
        state = self.inspect(i3.tree())
        if state.browser_window_id is None:
            raise BackendError(f"FirefoxWindow {self.node.id!r} closed during reconciliation; invoke again")
        # Browser-sensitive decisions and verification stay entirely inside Firefox.
        self.call(state.owner, "sync-tabs" if sync else "ensure-tabs",
                  tabs=[asdict(tab) for tab in self.node.firefox_tabs])
        fresh = self.inspect(i3.tree())
        for tab in self.node.firefox_tabs:
            if fresh.tab(tab) is None:
                raise BackendError(f"FirefoxTab {tab.id!r} creation was not observed; invoke again")
