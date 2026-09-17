"""Read-only desktop capture and identity-preserving workflow layout updates."""
from __future__ import annotations

import copy
import json
import math
import os
from pathlib import Path
import re
import stat
import tempfile

from .config import resolve
from .errors import BackendError, ConfigError
from .i3 import command_layout, is_window, path_to, walk
from .kitty import Kitty
from .model import Node, Workflow, digest
from .schema import CHILDREN, element_name, normalize_document, workspace_id


def literal(value: str) -> str:
    """Protect captured strings from workflow interpolation."""
    return value.replace("{", "{{").replace("}", "}}")


def dumps(document: dict, warnings: list[str] = ()) -> str:
    """Serialize the workflow subset of TOML, without a runtime dependency."""
    def value(item):
        if isinstance(item, str):
            return json.dumps(item, ensure_ascii=False)
        if type(item) is bool:
            return "true" if item else "false"
        if type(item) in {int, float}:
            if not math.isfinite(item):
                raise ConfigError("Cannot write non-finite TOML numbers")
            return str(item)
        if isinstance(item, list):
            return "[" + ", ".join(value(v) for v in item) + "]"
        if isinstance(item, dict):
            return "{ " + ", ".join(value(k) + " = " + value(v) for k, v in item.items()) + " }"
        raise ConfigError(f"Cannot write TOML value {type(item).__name__}")

    lines = ["# Saved by Layouter; review captured commands before launching."]
    lines.extend("# " + warning.replace("\n", " ").replace("\r", " ") for warning in warnings)

    def table(data, path):
        arrays = {}
        for key, item in data.items():
            if isinstance(item, list) and item and all(isinstance(v, dict) for v in item):
                arrays[key] = item
            else:
                lines.append(value(key) + " = " + value(item))
        for key, items in arrays.items():
            child_path = [*path, key]
            for item in items:
                lines.extend(["", "[[" + ".".join(value(k) for k in child_path) + "]]"])
                table(item, child_path)
    table(document, [])
    return "\n".join(lines) + "\n"


def write_document(path: Path, content: str, original: bytes | None = None):
    """Create exclusively, or back up and atomically replace an unchanged source."""
    path = path.expanduser().absolute()
    if path.is_symlink():
        raise ConfigError(f"Refusing to replace a symlink: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if original is None:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as file:
            file.write(content)
        return None
    if path.read_bytes() != original:
        raise ConfigError(f"Workflow changed while inspecting the desktop: {path}; retry")
    backup = path.with_name(path.name + ".bak")
    # Keep every prior backup; never silently replace one.
    index = 1
    while backup.exists():
        backup = path.with_name(path.name + f".bak.{index}")
        index += 1
    fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as file:
        file.write(original)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as file:
            file.write(content)
            file.flush()
            os.fsync(file.fileno())
        os.chmod(temporary, stat.S_IMODE(path.stat().st_mode))
        if path.read_bytes() != original:
            raise ConfigError(f"Workflow changed while saving: {path}; retry")
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return backup


def presentation(raw: dict, live: dict, *, workspace=False):
    if workspace or not is_window(live):
        layout = command_layout(live.get("layout", "splith"))
        if layout not in {"splith", "splitv", "tabbed", "stacking"}:
            raise ConfigError(f"Unsupported live layout {layout!r}")
        raw["layout"] = layout
    if not workspace:
        percent = live.get("percent")
        if type(percent) in {int, float} and math.isfinite(percent) and 0 < percent <= 1:
            raw["size"] = percent * 100
        else:
            raw.pop("size", None)


def output_for(tree, live):
    return next((n.get("name") for n in reversed(path_to(tree, live["id"]))
                 if n.get("type") == "output" and n.get("name") != "__i3"), None)


def process_command(live: dict):
    """Best-effort argv/cwd hints; process state is not an application launch recipe."""
    pid = live.get("pid")
    if type(pid) is not int or pid <= 0:
        return None, None
    try:
        base = Path("/proc") / str(pid)
        command = [v.decode("utf-8") for v in (base / "cmdline").read_bytes().split(b"\0") if v]
        cwd = str((base / "cwd").resolve(strict=True))
        return command or None, cwd
    except (OSError, UnicodeError):
        return None, None


def capture(tree: dict, name: str, project: Path, runtime, timeout=30):
    warnings = []
    document = {"session": name, "workspace": []}
    used = set()

    def new_name(prefix):
        slug = re.sub(r"[^A-Za-z0-9_-]+", "-", str(prefix)).strip("-") or "window"
        candidate, index = slug, 2
        while candidate in used:
            candidate = f"{slug}-{index}"
            index += 1
        used.add(candidate)
        return candidate

    def kitty_window(live, node_name):
        # Only known private Layouter endpoints can be discovered without guessing.
        marks = [m.removeprefix("layouter_") for m in live.get("marks", [])
                 if re.fullmatch(r"layouter_[0-9a-f]{24}", m)]
        if len(marks) != 1:
            return None
        endpoint = runtime.socket(marks[0])
        if not endpoint.exists():
            return None
        st = runtime.path.lstat()
        if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
            return None
        node = Node(node_name, "kitty", project, {})
        kitty = Kitty(Workflow(project, name, name, {}, (), None, timeout), node, runtime)
        kitty.path = endpoint
        try:
            snapshot = kitty.inspect(tree)
        except BackendError as exc:
            warnings.append(f"{node_name}: kitty inspection unavailable ({exc}); exported as a launch-command draft.")
            return None
        if len(snapshot.os_windows) != 1:
            warnings.append(f"{node_name}: multiple kitty OS windows; exported as a launch-command draft.")
            return None
        tabs = []
        for ti, tab in enumerate(snapshot.os_windows[0]["tabs"], 1):
            panes = []
            for pi, pane in enumerate(tab["windows"], 1):
                raw = {"name": f"pane-{pi}", "title": literal(pane.get("title") or f"pane-{pi}")}
                cwd = pane.get("cwd")
                if cwd:
                    raw["cwd"] = literal(cwd)
                # Foreground argv is useful but may be a transient child process.
                processes = pane.get("foreground_processes") or []
                argv = processes[0].get("cmdline") if len(processes) == 1 else pane.get("cmdline")
                if isinstance(argv, list) and argv and all(isinstance(v, str) for v in argv):
                    raw["command"] = [literal(v) for v in argv]
                else:
                    warnings.append(f"{node_name}/pane-{pi}: command unavailable; starts the default shell.")
                panes.append(raw)
            if panes:
                tabs.append({"name": f"tab-{ti}", "title": literal(tab.get("title") or f"tab-{ti}"),
                             "layout": tab.get("layout", "tall"), "pane": panes})
        if not tabs:
            return None
        warnings.append(f"{node_name}: review inferred pane commands; environment and exact split geometry are not captured.")
        return {"name": node_name, "tab": tabs}

    def children(owner, live):
        if live.get("floating_nodes"):
            warnings.append(f"{live.get('name') or live['id']}: floating windows omitted (unsupported geometry).")
        for index, child in enumerate(live.get("nodes", [])):
            if is_window(child):
                props = child.get("window_properties") or {}
                label = child.get("app_id") or props.get("class") or "window"
                node_name = new_name(label)
                raw = kitty_window(child, node_name)
                kind = "kitty" if raw else "window"
                if raw is None:
                    raw = {"name": node_name}
                    matcher = ({"app_id": "^" + re.escape(child["app_id"]) + "$"} if child.get("app_id") else
                               {k: "^" + re.escape(props[k]) + "$" for k in ("class", "instance", "window_role") if props.get(k)})
                    if matcher:
                        raw["match"] = {k: literal(v) for k, v in matcher.items()}
                    command, cwd = process_command(child)
                    raw["command"] = [literal(v) for v in command] if command else ["REPLACE_WITH_LAUNCH_COMMAND"]
                    if cwd:
                        raw["cwd"] = literal(cwd)
                    # A process argv can be a helper, or relaunch an entire shared app.
                    raw["enabled"] = False
                    warnings.append(f"{node_name}: disabled; {'review inferred' if command else 'supply'} launch command, then set enabled = true.")
                if child.get("focused") and kind == "kitty":
                    document["focus"] = node_name
            elif child.get("nodes"):
                kind, raw = "container", {"name": new_name("group")}
                children(raw, child)
            else:
                continue
            presentation(raw, child)
            raw["order"] = index
            owner.setdefault(kind, []).append(raw)

    for live in walk(tree):
        if live.get("type") != "workspace" or str(live.get("name", "")).startswith("__"):
            continue
        raw = {"name": literal(live["name"])}
        output = output_for(tree, live)
        if output:
            raw["output"] = literal(output)
        presentation(raw, live, workspace=True)
        children(raw, live)
        document["workspace"].append(raw)
    if not document["workspace"]:
        raise ConfigError("No regular desktop workspaces found")
    return document, warnings


def raw_index(document):
    """Index original declarations without expanding commands, arguments, or names."""
    result, parents, kinds = {}, {}, {}

    def children(owner, parent, location):
        for kind, items in owner.items():
            if kind not in CHILDREN:
                continue
            for index, item in enumerate(items):
                where = location + "." + kind
                nid = ("container-" + digest(parent, str(index)) if kind == "container" and "name" not in item
                       else element_name(item, where)[1])
                result[nid], parents[nid], kinds[nid] = item, owner, kind
                if kind == "container":
                    children(item, nid, where)
    for item in document.get("workspace", []):
        nid = workspace_id(item["name"])
        result[nid], kinds[nid] = item, "workspace"
        children(item, nid, "workspace")
    return result, parents, kinds


def save_layout(document, workflow, tree, compositor, runtime):
    document = copy.deepcopy(document)
    canonical = normalize_document(document, workflow.name)["workflows"][workflow.name].get("nodes", {})
    original_ids = set(canonical)
    raw, parents, kinds = raw_index(document)
    warnings = []
    live_raw, live_nodes = {}, {}
    for node in workflow.nodes:
        live = compositor.resolve_node(workflow, node, tree)
        if live is None:
            if node.kind in {"app", "kitty"}:
                warnings.append(f"{node.id}: absent; kept its declaration and placement.")
            continue
        ancestors = path_to(tree, live["id"])
        if any(a.get("type") == "floating_con" or a.get("name") == "__i3_scratch"
               or any(c.get("id") == live["id"] for c in a.get("floating_nodes", [])) for a in ancestors):
            warnings.append(f"{node.id}: floating/scratchpad placement unsupported; kept its declaration.")
            continue
        if live["id"] in live_raw:
            raise ConfigError("Multiple declarations resolve to the same live container; refusing to guess")
        live_raw[live["id"]] = raw[node.id]
        live_nodes[live["id"]] = node

    # Only reconstruct paths that lead to a known, live application.
    needed = {a["id"] for live_id, node in live_nodes.items() if node.kind in {"app", "kitty"}
              for a in path_to(tree, live_id)}
    needed.update(live_id for live_id, node in live_nodes.items() if node.kind == "workspace")
    for live in walk(tree):
        if live["id"] not in needed or live.get("type") not in {"workspace", "con"}:
            continue
        # i3 has a compositor-only "content" container above workspaces.
        if live.get("type") == "con" and not any(a.get("type") == "workspace" for a in path_to(tree, live["id"])):
            continue
        if live["id"] not in live_raw:
            if is_window(live):
                continue
            if live.get("type") == "workspace":
                item = {"name": literal(live["name"])}
                nid = workspace_id(item["name"])
                if nid in raw:
                    raise ConfigError(f"Captured workspace name collides with an existing declaration: {live['name']}")
                document.setdefault("workspace", []).append(item)
                kinds[nid] = "workspace"
            else:
                nid = "saved-group-" + str(live["id"])
                if nid in raw and kinds[nid] == "container" and not any(raw[nid] is v for v in live_raw.values()):
                    item = raw[nid]
                else:
                    while nid in raw:
                        nid += "-new"
                    item = {"name": nid}
                    kinds[nid] = "container"
            raw[nid] = item
            live_raw[live["id"]] = item
        item = live_raw[live["id"]]
        is_workspace = live.get("type") == "workspace"
        presentation(item, live, workspace=is_workspace)
        if is_workspace:
            output = output_for(tree, live)
            if output:
                item["output"] = literal(output)
        else:
            ancestors = path_to(tree, live["id"])
            parent_live = ancestors[-2]
            parent = live_raw.get(parent_live["id"])
            if parent is None:
                raise ConfigError("Cannot represent the live compositor parent")
            nid = next(key for key, val in raw.items() if val is item)
            old_parent = parents.get(nid)
            kind = kinds[nid]
            if old_parent is not None:
                old_parent[kind] = [v for v in old_parent[kind] if v is not item]
            parent.setdefault(kind, []).append(item)
            parents[nid] = parent
            item["order"] = next(i for i, c in enumerate(parent_live.get("nodes", [])) if c["id"] == live["id"])
            if kind == "container" and "name" not in item:
                item["name"] = nid

    for live_id, node in live_nodes.items():
        if node.kind != "kitty" or node.session_file:
            continue
        snapshot = Kitty(workflow, node, runtime).inspect(tree)
        item = raw[node.id]
        if "tab" in item:
            declared_tabs = list(zip(item["tab"], canonical[node.id]["tabs"]))
        else:
            declared_tabs = [(item, "dev")]
        for tab_raw, tid in declared_tabs:
            tab = next((t for t in node.tabs if t.id == tid), None)
            if tab is None:
                continue
            live_tab = snapshot.tab(workflow, node, tab)
            if live_tab is None:
                warnings.append(f"{node.id}.{tid}: tab absent; retained its settings.")
                continue
            tab_raw["layout"] = live_tab.get("layout", tab.layout)
            tab_raw["title"] = literal(live_tab.get("title") or tab.title)
            positions = {p["id"]: i for i, p in enumerate(live_tab["windows"])}
            order = {}
            for pane in tab.panes:
                current = snapshot.pane(workflow.pane_key(node.id, tab.id, pane.id))
                if current and current.tab["id"] != live_tab["id"]:
                    warnings.append(f"{node.id}.{tid}: pane moved between tabs; membership retained to preserve identity.")
                elif current:
                    order[pane.id] = positions[current.window["id"]]
            panes = tab_raw.get("pane", [])
            # A partial snapshot must not rewrite dependency order for absent panes.
            ids = {id(p): pid for p, pid in zip(panes, canonical[node.id]["tabs"][tid].get("panes", {}))}
            if panes and all(ids[id(p)] in order for p in panes):
                panes.sort(key=lambda p: order[ids[id(p)]])
                previous = None
                for pane in panes:
                    pane.pop("after", None)
                    if previous:
                        pane["after"] = previous
                    previous = ids[id(pane)]
            if tab_raw["layout"] == "splits":
                warnings.append(f"{node.id}.{tid}: exact kitty split geometry is not representable.")

    normalized = normalize_document(document, workflow.name)
    new_ids = set(normalized["workflows"][workflow.name].get("nodes", {}))
    if not original_ids <= new_ids:
        raise ConfigError("This move changes a location-derived identity. Give moved elements simple names before saving.")
    resolve(normalized, workflow.project, workflow.name, list(workflow.arguments.values()))
    return document, warnings
