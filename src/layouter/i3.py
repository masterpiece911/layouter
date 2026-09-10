"""Inspect and arrange i3/Sway desktops through their shared IPC protocol.

Normal placement only mutates newly created elements. Explicit synchronization
may move existing managed windows to reconstruct the declared tree."""

from __future__ import annotations

import json
import os
import re
import secrets
import socket
import struct
import subprocess
import time
from collections import deque
from contextlib import AbstractContextManager

from .errors import AmbiguousState, BackendError
from .model import Node, Workflow

HEADER = struct.Struct("=6sII")
MAGIC = b"i3-ipc"
EVENT = 1 << 31
MAX_MESSAGE = 64 * 1024 * 1024


def walk(node: dict):
    """Traverse the compositor tree, including floating children."""
    yield node
    for child in node.get("nodes", []) + node.get("floating_nodes", []):
        yield from walk(child)


def find_id(tree: dict, con_id: int) -> dict | None:
    """Find a live container by numeric IPC ID, returning None if it disappeared."""
    return next((node for node in walk(tree) if node.get("id") == con_id), None)


def path_to(tree: dict, con_id: int) -> list[dict]:
    """Return the root-to-container path, or an empty list when the ID is absent."""
    def visit(node: dict, path: list[dict]):
        current = [*path, node]
        if node.get("id") == con_id:
            return current
        for child in node.get("nodes", []) + node.get("floating_nodes", []):
            result = visit(child, current)
            if result:
                return result
        return None

    return visit(tree, []) or []


def descendant(tree: dict, child_id: int, parent_id: int) -> bool:
    path = path_to(tree, child_id)
    return any(node.get("id") == parent_id for node in path[:-1])


def parent_of(tree: dict, con_id: int) -> dict | None:
    path = path_to(tree, con_id)
    return path[-2] if len(path) > 1 else None


def common_parent(tree: dict, first_id: int, second_id: int) -> dict | None:
    """Find the deepest shared ancestor of two live containers."""
    first, second = path_to(tree, first_id), path_to(tree, second_id)
    result = None
    for left, right in zip(first, second):
        if left.get("id") != right.get("id"):
            break
        result = left
    return result


def unique(items: list[dict], description: str) -> dict | None:
    """Accept zero or one match and reject ambiguity instead of choosing arbitrarily."""
    if len(items) > 1:
        raise AmbiguousState(f"Multiple live containers match {description}; refusing to guess")
    return items[0] if items else None


def marked(tree: dict, mark: str) -> dict | None:
    """Find the unique live container carrying a managed identity mark."""
    return unique([n for n in walk(tree) if mark in n.get("marks", [])], f"mark {mark}")


def matches(node: dict, patterns: dict[str, str]) -> bool:
    """Match real windows against portable X11 or Wayland property patterns."""
    if not is_window(node):
        return False
    props = node.get("window_properties") or {}
    for key, pattern in patterns.items():
        if key == "title":
            candidates = (str(node.get("name") or ""),)
        elif key == "app_id":
            candidates = (str(node.get("app_id") or ""),)
        elif key == "class":
            candidates = (str(props.get("class") or ""), str(node.get("app_id") or ""))
        else:
            candidates = (str(props.get(key) or ""),)
        if not any(re.search(pattern, candidate) is not None for candidate in candidates):
            return False
    return True


def is_window(node: dict) -> bool:
    return node.get("window") is not None or node.get("app_id") is not None


def managed(node: dict) -> bool:
    return any(mark.startswith("layouter_") for mark in node.get("marks", []))


def quote(value: str) -> str:
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise BackendError("Control characters are not allowed in compositor command arguments")
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def command_layout(value: str) -> str:
    # GET_TREE/layout JSON says "stacked"; the runtime command says "stacking".
    return "stacking" if value == "stacked" else value


class Connection(AbstractContextManager):
    """A bounded, framed Unix-socket transport for i3-compatible IPC."""
    def __init__(self, path: str, timeout: float):
        self.timeout = timeout
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(timeout)
        try:
            self.sock.connect(path)
        except OSError as exc:
            self.sock.close()
            raise BackendError(f"Cannot connect to compositor IPC at {path}: {exc}") from exc

    def __exit__(self, *args):
        self.sock.close()

    def send(self, kind: int, payload: str = ""):
        """Send a UTF-8 payload with its binary IPC header."""
        data = payload.encode("utf-8")
        self.sock.settimeout(self.timeout)
        try:
            self.sock.sendall(HEADER.pack(MAGIC, len(data), kind) + data)
        except OSError as exc:
            raise BackendError(f"Compositor IPC send failed: {exc}") from exc

    def receive(self, timeout: float | None = None) -> tuple[int, object]:
        """Read and decode one complete frame within a single shared deadline."""
        deadline = time.monotonic() + (self.timeout if timeout is None else timeout)

        def exact(size: int) -> bytes:
            result = bytearray()
            while len(result) < size:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("Compositor IPC response timed out")
                self.sock.settimeout(remaining)
                chunk = self.sock.recv(size - len(result))
                if not chunk:
                    raise BackendError("Compositor closed its IPC connection")
                result.extend(chunk)
            return bytes(result)

        try:
            magic, size, kind = HEADER.unpack(exact(HEADER.size))
            if magic != MAGIC or size > MAX_MESSAGE:
                raise BackendError("Invalid i3 IPC frame")
            return kind, json.loads(exact(size))
        except TimeoutError:
            raise
        except (OSError, ValueError, struct.error) as exc:
            raise BackendError(f"Invalid i3 response: {exc}") from exc

    def request(self, kind: int, payload: str = ""):
        """Send a request and require a response of the same IPC type."""
        self.send(kind, payload)
        try:
            response_kind, data = self.receive()
        except TimeoutError as exc:
            raise BackendError("Compositor request timed out; result may be unknown") from exc
        if response_kind != kind:
            raise BackendError("Unexpected compositor response type")
        return data


class Events(Connection):
    """A dedicated window subscription that buffers events received before acknowledgment."""
    def __init__(self, path: str, timeout: float):
        super().__init__(path, timeout)
        self.pending = deque()
        try:
            self.send(2, json.dumps(["window"]))
            deadline = time.monotonic() + timeout
            while True:
                kind, response = self.receive(max(0, deadline - time.monotonic()))
                if kind & EVENT:
                    # A window can appear before the subscription acknowledgment;
                    # retain the event while still waiting for subscription success.
                    self.pending.append(response)
                    continue
                if kind != 2 or not isinstance(response, dict) or not response.get("success"):
                    raise BackendError("Compositor rejected the window event subscription")
                break
        except BaseException:
            self.sock.close()
            raise

    def next(self, timeout: float):
        """Consume an already-buffered event before waiting for another frame."""
        if self.pending:
            return self.pending.popleft()
        _, event = self.receive(timeout)
        return event


class Compositor(AbstractContextManager):
    """Expose live-tree discovery and guarded placement for both i3 and Sway."""
    def __init__(self, timeout: float = 30, path: str | None = None):
        if path is None:
            path = os.environ.get("I3SOCK") or os.environ.get("SWAYSOCK")
        if not path:
            for compositor in ("i3", "sway"):
                try:
                    path = subprocess.check_output([compositor, "--get-socketpath"], text=True,
                                                   timeout=timeout, stderr=subprocess.DEVNULL).strip()
                except (OSError, subprocess.SubprocessError):
                    continue
                if path:
                    break
            if not path:
                raise BackendError("Cannot find an i3/Sway IPC socket; run Layouter inside the compositor session")
        self.path, self.timeout = path, timeout
        self.connection = Connection(path, timeout)
        version = self.connection.request(7)
        if not isinstance(version, dict):
            self.connection.__exit__()
            raise BackendError("Compositor returned invalid version data")
        self.mutable_ids: set[int] = set()
        self.safe_workspaces: set[int] = set()

    def __exit__(self, *args):
        self.connection.__exit__(*args)

    def tree(self) -> dict:
        """Fetch the current compositor hierarchy."""
        result = self.connection.request(4)
        if not isinstance(result, dict) or "id" not in result:
            raise BackendError("Compositor returned an invalid tree")
        return result

    def command(self, value: str):
        """Execute compositor commands and reject any unsuccessful result."""
        result = self.connection.request(0, value)
        if not isinstance(result, list) or not result or any(not r.get("success") for r in result):
            raise BackendError(f"Compositor command failed: {value}: {result}")

    def events(self) -> Events:
        return Events(self.path, self.timeout)

    def find(self, mark: str) -> dict | None:
        return marked(self.tree(), mark)

    def mark(self, node: dict, mark: str):
        # --add preserves user marks. Never transfer an existing managed mark.
        """Attach identity without replacing user marks or transferring another element’s identity."""
        existing = self.find(mark)
        if existing is not None:
            if existing["id"] != node["id"]:
                raise AmbiguousState(f"Identity {mark} is already attached to another container")
            return
        self.command(f"[con_id={int(node['id'])}] mark --add {quote(mark)}")

    def focused(self) -> int | None:
        return next((n["id"] for n in walk(self.tree()) if n.get("focused")), None)

    def focus(self, con_id: int):
        if any(n["id"] == con_id for n in walk(self.tree())):
            self.command(f"[con_id={int(con_id)}] focus")

    def adoptable(self, patterns: dict[str, str]) -> dict | None:
        """Find exactly one matching unmanaged window, or report ambiguity."""
        return unique([n for n in walk(self.tree()) if matches(n, patterns) and not managed(n)],
                      "unmanaged application matcher")

    def wait_new(self, events: Events, baseline: set[int], patterns: dict[str, str],
                 description: str) -> dict:
        """Wait for a matching window absent from the pre-launch baseline."""
        deadline = time.monotonic() + self.timeout
        while True:
            # Snapshot after subscription/launch catches even an immediate map event.
            candidates = [n for n in walk(self.tree()) if n["id"] not in baseline
                          and not managed(n) and matches(n, patterns)]
            result = unique(candidates, description)
            if result is not None:
                return result
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                events.next(remaining)
            except TimeoutError:
                # One final snapshot handles an event arriving on the timeout boundary.
                candidates = [n for n in walk(self.tree()) if n["id"] not in baseline
                              and not managed(n) and matches(n, patterns)]
                result = unique(candidates, description)
                if result is not None:
                    return result
                break
        raise BackendError(f"Timed out discovering {description}. The process was left running; "
                           "check its matcher and runtime log before retrying")

    def _sets(self):
        # Test doubles constructed without __init__ also use these methods.
        if not hasattr(self, "mutable_ids"):
            self.mutable_ids = set()
        if not hasattr(self, "safe_workspaces"):
            self.safe_workspaces = set()

    @staticmethod
    def _workspace(workflow: Workflow, node: Node) -> Node:
        current = node
        while current.kind != "workspace":
            current = workflow.by_id[current.parent]
        return current

    def _ensure_workspace(self, workflow: Workflow, node: Node) -> dict:
        """Find or create the workspace and remember whether its empty layout is safe to initialize."""
        self._sets()
        workspace = self._workspace(workflow, node)
        tree = self.tree()
        live = marked(tree, workflow.mark(workspace.id))
        if live is None:
            live = unique([item for item in walk(tree) if item.get("type") == "workspace"
                           and item.get("name") == workspace.name], f"workspace {workspace.name}")
            if live is None:
                self.command(f"workspace --no-auto-back-and-forth {quote(workspace.name)}")
                live = unique([item for item in walk(self.tree()) if item.get("type") == "workspace"
                               and item.get("name") == workspace.name], f"workspace {workspace.name}")
                if live is None:
                    raise BackendError(f"Could not create workspace {workspace.name}")
            if not live.get("nodes") and not live.get("floating_nodes"):
                self.safe_workspaces.add(int(live["id"]))
            self.mark(live, workflow.mark(workspace.id))
            live = self.find(workflow.mark(workspace.id)) or live
        if is_window(live):
            raise BackendError(f"{workspace.id}: its workspace mark belongs to a real window")
        if not live.get("nodes") and not live.get("floating_nodes"):
            # An interrupted earlier invocation may have left only the mark.
            self.safe_workspaces.add(int(live["id"]))
        return live

    def _nearest_parent(self, workflow: Workflow, node: Node, tree: dict) -> tuple[Node, dict]:
        """Find the nearest surviving marked ancestor when declared containers are absent."""
        current = workflow.by_id[node.parent]
        while True:
            live = marked(tree, workflow.mark(current.id))
            if live is not None:
                if current.kind == "container" and is_window(live):
                    raise BackendError(f"{current.id}: its structural mark belongs to a real window")
                return current, live
            if current.kind == "workspace":
                raise BackendError(f"Workspace {current.id} lost its Layouter mark")
            current = workflow.by_id[current.parent]

    def prepare_launch(self, workflow: Workflow, leaf: Node) -> dict:
        """Ensure a destination workspace and focus the nearest surviving parent."""
        self._ensure_workspace(workflow, leaf)
        _, destination = self._nearest_parent(workflow, leaf, self.tree())
        self.command(f"[con_id={int(destination['id'])}] focus")
        return destination

    def _attach(self, workflow: Workflow, node: Node, con_id: int):
        """Move an eligible element beneath its nearest surviving parent and verify placement."""
        tree = self.tree()
        parent, destination = self._nearest_parent(workflow, node, tree)
        if descendant(tree, con_id, int(destination["id"])):
            return
        if parent.kind == "workspace":
            self.command(f"[con_id={con_id}] move container to workspace {quote(parent.name)}")
        else:
            self.command(f"[con_id={con_id}] move container to mark {quote(workflow.mark(parent.id))}")
        if not descendant(self.tree(), con_id, int(destination["id"])):
            raise BackendError(f"Could not attach {node.id} to {parent.id}")

    def place_new(self, workflow: Workflow, leaf: Node, created: dict, baseline: set[int]):
        """Mark and place a discovered window only if it was absent before launch."""
        if created["id"] in baseline:
            raise BackendError("Refusing to place a preexisting window")
        self._sets()
        self.mark(created, workflow.mark(leaf.id))
        self.mutable_ids.add(int(created["id"]))
        self._attach(workflow, leaf, int(created["id"]))

    @staticmethod
    def _children(workflow: Workflow, parent: str) -> list[Node]:
        return [node for node in workflow.nodes if node.parent == parent]

    def _root(self, workflow: Workflow, node: Node, tree: dict) -> tuple[dict, str] | None:
        """Resolve a declared subtree to its live root, tolerating flattened singleton containers."""
        live = marked(tree, workflow.mark(node.id))
        if live is not None:
            return live, workflow.mark(node.id)
        if node.kind != "container":
            return None
        children = self._children(workflow, node.id)
        if len(children) == 1:
            return self._root(workflow, children[0], tree)
        return None

    def _materialize(self, workflow: Workflow, container: Node) -> bool:
        """Build a missing multi-child container only from roots eligible for mutation."""
        self._sets()
        tree = self.tree()
        if marked(tree, workflow.mark(container.id)) is not None:
            return False
        roots = [self._root(workflow, child, tree)
                 for child in self._children(workflow, container.id)]
        if len(roots) < 2 or any(root is None for root in roots):
            return False
        resolved = [root for root in roots if root is not None]
        ids = [int(root[0]["id"]) for root in resolved]
        if len(set(ids)) != len(ids) or any(con_id not in self.mutable_ids for con_id in ids):
            # Outside sync, mutable IDs belong only to this invocation. Reusing an
            # older child here would silently rearrange the user's existing layout.
            return False
        first, second = resolved[0], resolved[1]
        direction = "vertical" if container.layout == "splitv" else "horizontal"
        self.command(f"[con_id={ids[0]}] focus; split {direction}")
        self.command(f"[con_id={ids[1]}] move container to mark {quote(first[1])}")
        tree = self.tree()
        parent = common_parent(tree, ids[0], ids[1])
        if parent is None or parent.get("type") in {"root", "output", "workspace"}:
            # Redundant split containers may be flattened by the compositor.
            return False
        self.mark(parent, workflow.mark(container.id))
        parent = self.find(workflow.mark(container.id)) or parent
        parent_id = int(parent["id"])
        self.mutable_ids.add(parent_id)
        previous = ids[1]
        for con_id in ids[2:]:
            if not descendant(self.tree(), con_id, parent_id):
                self.command(f"[con_id={previous}] focus; "
                             f"[con_id={con_id}] move container to mark {quote(workflow.mark(container.id))}")
            previous = con_id
        self.command(f"[con_id={parent_id}] layout {command_layout(container.layout)}")
        self._attach(workflow, container, parent_id)
        return True

    def _initial_layout_and_sizes(self, workflow: Workflow, *, force: bool = False):
        """Apply layout and ratios to new groups, or to existing managed groups when forced by sync."""
        self._sets()
        tree = self.tree()
        for parent in (node for node in workflow.nodes if node.kind in {"workspace", "container"}):
            live = marked(tree, workflow.mark(parent.id))
            if live is None:
                continue
            parent_id = int(live["id"])
            safe = force or (parent_id in self.mutable_ids if parent.kind == "container"
                             else parent_id in self.safe_workspaces)
            if not safe:
                continue
            children = self._children(workflow, parent.id)
            roots = [self._root(workflow, child, tree) for child in children]
            if not roots or any(root is None for root in roots):
                continue
            child_ids = [int(root[0]["id"]) for root in roots if root is not None]
            if not force and any(con_id not in self.mutable_ids for con_id in child_ids):
                # Resizing a new child also resizes its siblings, so the entire
                # group must be new before ordinary reconciliation applies ratios.
                continue
            if not force or live.get("layout") != parent.layout:
                self.command(f"[con_id={parent_id}] layout {command_layout(parent.layout)}")
            if parent.layout not in {"splith", "splitv"} or len(children) < 2:
                continue
            dimension = "width" if parent.layout == "splith" else "height"
            for child, con_id in zip(children, child_ids):
                if child.size is not None:
                    actual = find_id(tree, con_id)
                    percent = actual.get("percent") if actual else None
                    if not force or not isinstance(percent, (int, float)) or abs(percent * 100 - child.size) > .5:
                        self.command(f"[con_id={con_id}] resize set {dimension} {child.size:g} ppt")

    def _structural_parents(self, workflow: Workflow) -> list[Node]:
        """Select active workspaces and containers whose multiple children require real structure."""
        workspaces = {self._workspace(workflow, leaf).id for leaf in workflow.leaves}
        return [node for node in workflow.nodes
                if (node.kind == "workspace" and node.id in workspaces) or
                (node.kind == "container" and len(self._children(workflow, node.id)) > 1)]

    def _managed_workspaces(self, workflow: Workflow) -> list[Node]:
        active = {self._workspace(workflow, leaf).id for leaf in workflow.leaves}
        return [node for node in workflow.nodes if node.kind == "workspace" and node.id in active]

    def _structure_matches(self, workflow: Workflow, tree: dict) -> bool:
        """Check managed tiling, parentage, workspace names, and relative child order."""
        for leaf in workflow.leaves:
            live = marked(tree, workflow.mark(leaf.id))
            if live is None or not is_window(live):
                return False
        for parent in self._structural_parents(workflow):
            live = marked(tree, workflow.mark(parent.id))
            if live is None or is_window(live):
                return False
            if parent.kind == "workspace" and live.get("name") != parent.name:
                return False
            if parent.kind == "container":
                desired_parent = workflow.by_id[parent.parent]
                while desired_parent.kind == "container" and len(self._children(workflow, desired_parent.id)) == 1:
                    desired_parent = workflow.by_id[desired_parent.parent]
                expected = marked(tree, workflow.mark(desired_parent.id))
                actual_parent = parent_of(tree, int(live["id"]))
                if expected is None or actual_parent is None or actual_parent.get("id") != expected.get("id"):
                    return False
            desired = self._children(workflow, parent.id)
            roots = [self._root(workflow, child, tree) for child in desired]
            if any(root is None for root in roots):
                return False
            desired_ids = [int(root[0]["id"]) for root in roots if root is not None]
            tiled_ids = [int(child["id"]) for child in live.get("nodes", [])]
            if [con_id for con_id in tiled_ids if con_id in desired_ids] != desired_ids:
                # Compare only managed relative order; extra user windows may stay
                # interleaved without making the declared structure incorrect.
                return False
            if any((parent_of(tree, con_id) or {}).get("id") != live.get("id") for con_id in desired_ids):
                return False
        return True

    def _sync_workspace(self, workflow: Workflow, workspace: Node) -> dict:
        """Restore a workspace name or transfer its identity to the existing target workspace."""
        tree = self.tree()
        mark = workflow.mark(workspace.id)
        live = marked(tree, mark)
        target = unique([node for node in walk(tree)
                         if node.get("type") == "workspace" and node.get("name") == workspace.name],
                        f"workspace {workspace.name}")
        if live is not None and live.get("name") != workspace.name:
            if target is not None and target.get("id") != live.get("id"):
                # The desired workspace name is already occupied. Use that workspace
                # for managed leaves without renaming or moving its unmanaged contents.
                self.command(f"[con_id={int(live['id'])}] unmark {quote(mark)}")
                self.mark(target, mark)
                live = self.find(mark) or target
            else:
                self.command(f"rename workspace {quote(str(live.get('name') or ''))} "
                             f"to {quote(workspace.name)}")
                live = self.find(mark)
        if live is None:
            live = self._ensure_workspace(workflow, workspace)
        if live is None or live.get("name") != workspace.name:
            raise BackendError(f"Could not synchronize workspace {workspace.name}")
        return live

    def _layout_differences(self, workflow: Workflow, tree: dict) -> list[str]:
        """Report declared layouts and split percentages that differ from live state."""
        differences = []
        for parent in self._structural_parents(workflow):
            live = marked(tree, workflow.mark(parent.id))
            if live is None:
                continue
            if live.get("layout") != parent.layout:
                differences.append(f"{parent.id} layout")
            children = self._children(workflow, parent.id)
            if parent.layout not in {"splith", "splitv"} or len(children) < 2:
                continue
            for child in children:
                if child.size is None:
                    continue
                root = self._root(workflow, child, tree)
                percent = root[0].get("percent") if root else None
                if not isinstance(percent, (int, float)) or abs(percent * 100 - child.size) > .5:
                    differences.append(f"{child.id} size")
        return differences

    def sync_plan(self, workflow: Workflow) -> list[tuple[str, str]]:
        """Report workspace, structure, and layout corrections without issuing mutations."""
        tree = self.tree()
        result = []
        for workspace in self._managed_workspaces(workflow):
            live = marked(tree, workflow.mark(workspace.id))
            if live is None or live.get("name") != workspace.name:
                result.append((workspace.id, "workspace identity/name"))
        if not self._structure_matches(workflow, tree):
            result.append(("compositor", "managed placement, nesting, or order"))
        for difference in self._layout_differences(workflow, tree):
            target, detail = difference.split(maxsplit=1)
            result.append((target, "declared " + detail))
        return result

    def sync(self, workflow: Workflow) -> list[tuple[str, str]]:
        """Rebuild managed structure when necessary, then restore declared layouts and sizes."""
        changes = self.sync_plan(workflow)
        for workspace in self._managed_workspaces(workflow):
            self._sync_workspace(workflow, workspace)
        tree = self.tree()
        if not self._structure_matches(workflow, tree):
            leaves = []
            for leaf in workflow.leaves:
                live = marked(tree, workflow.mark(leaf.id))
                if live is None or not is_window(live):
                    raise BackendError(f"Cannot synchronize missing compositor element {leaf.id}")
                leaves.append((leaf, int(live["id"])))
            if leaves:
                # Staging removes managed leaves from their old nesting while leaving
                # unmanaged windows in place. Their processes continue running as the
                # declared structure is rebuilt from the leaves upward.
                staging = f"__layouter_sync_{workflow.session_id}_{secrets.token_hex(4)}"
                self.command(f"workspace --no-auto-back-and-forth {quote(staging)}")
                for _, con_id in leaves:
                    self.command(f"[con_id={con_id}] floating disable")
                    self.command(f"[con_id={con_id}] move container to workspace {quote(staging)}")
                for container in (node for node in workflow.nodes if node.kind == "container"):
                    live = marked(self.tree(), workflow.mark(container.id))
                    if live is not None:
                        self.command(f"[con_id={int(live['id'])}] unmark {quote(workflow.mark(container.id))}")
                self.mutable_ids = {con_id for _, con_id in leaves}
                self.safe_workspaces = set()
                for workspace in self._managed_workspaces(workflow):
                    members = [(leaf, con_id) for leaf, con_id in leaves
                               if self._workspace(workflow, leaf).id == workspace.id]
                    if not members:
                        continue
                    first_leaf, first_id = members[0]
                    self.command(f"[con_id={first_id}] move container to workspace {quote(workspace.name)}")
                    live_workspace = self._ensure_workspace(workflow, first_leaf)
                    self.safe_workspaces.add(int(live_workspace["id"]))
                    for _, con_id in members[1:]:
                        self.command(f"[con_id={con_id}] move container to workspace {quote(workspace.name)}")
                containers = [node for node in workflow.nodes if node.kind == "container"]
                progress = True
                while progress:
                    progress = False
                    for container in reversed(containers):
                        progress = self._materialize(workflow, container) or progress
                if not self._structure_matches(workflow, self.tree()):
                    raise BackendError("Could not reconstruct the declared managed compositor tree")
        self._initial_layout_and_sizes(workflow, force=True)
        return changes

    def finalize(self, workflow: Workflow) -> list[Node]:
        """Assemble newly created groups and return nesting that must remain uncorrected."""
        self._sets()
        containers = [node for node in workflow.nodes if node.kind == "container"]
        if self.mutable_ids:
            progress = True
            while progress:
                progress = False
                for container in reversed(containers):
                    progress = self._materialize(workflow, container) or progress
            self._initial_layout_and_sizes(workflow)
        tree = self.tree()
        return [container for container in containers
                if len(self._children(workflow, container.id)) > 1
                and marked(tree, workflow.mark(container.id)) is None
                and all(self._root(workflow, child, tree) is not None
                        for child in self._children(workflow, container.id))]
