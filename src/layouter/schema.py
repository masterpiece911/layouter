"""Normalize one readable TOML workflow into the canonical desired-state form.

Arrays of named elements become identity-keyed tables. Neither this module nor
the backends use array positions as element identities, except for deliberately
unnamed structural containers.
"""
from __future__ import annotations

import copy
import re

from .errors import ConfigError
from .model import digest

ID = re.compile(r"[A-Za-z0-9_-]+\Z")
WORKFLOW_FIELDS = {"session", "args", "cwd", "env", "focus", "description", "workspace"}
CHILDREN = {"window", "kitty", "container"}
IMPLICIT_TAB = "dev"


def mapping(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a table")
    return value


def allowed(data: dict, fields: set[str], where: str):
    extra = data.keys() - fields
    if extra:
        raise ConfigError(f"{where}: unknown field(s): {', '.join(sorted(extra))}")


def identity(value, where: str) -> str:
    if not isinstance(value, str) or not ID.fullmatch(value):
        raise ConfigError(f"{where}: id must contain letters, digits, underscores or hyphens")
    return value


def element_name(value: dict, where: str, *fields: str) -> tuple[str, str]:
    """Return a readable identity and its canonical internal identifier."""
    fields = fields or ("name",)
    name = next((value.get(field) for field in fields
                 if isinstance(value.get(field), str) and value.get(field)), None)
    if name is None:
        raise ConfigError(f"{where}: {fields[0]} must be a nonempty string")
    return name, name if ID.fullmatch(name) else digest(where, name)


def entries(value, where: str):
    if not isinstance(value, list):
        raise ConfigError(f"{where}: expected an array of tables ([[{where}]])")
    for index, item in enumerate(value):
        yield mapping(item, f"{where}[{index}]")


def workspace_id(name: str) -> str:
    # Namespaces permit the sample's workspace 'browser' and window 'browser'.
    # Complex/template names are hashed before expansion, not derived from order.
    return "workspace-" + (name if ID.fullmatch(name) else digest("workspace", name))


def panes(value, where: str) -> dict:
    result = {}
    for pane in entries(value, where):
        allowed(pane, {"name", "enabled", "command", "cwd", "env", "title",
                       "hold", "after", "location"}, where)
        name, pid = element_name(pane, where, "name", "title")
        if pid in result:
            raise ConfigError(f"{where}: duplicate pane id {pid!r}")
        normalized = {k: copy.deepcopy(v) for k, v in pane.items() if k != "name"}
        if "title" not in normalized:
            normalized["title"] = name
        result[pid] = normalized
    return result


def kitty(value: dict, where: str) -> dict:
    allowed(value, {"name", "enabled", "cwd", "env", "executable", "config", "options",
                    "class", "size", "session", "layout", "title", "pane", "tab"}, where)
    result = {k: copy.deepcopy(v) for k, v in value.items()
              if k not in {"name", "pane", "tab", "layout", "title", "session"}}
    if "session" in value:
        result["session_file"] = copy.deepcopy(value["session"])
    modes = sum(key in value for key in ("pane", "tab")) + ("session_file" in result)
    if modes > 1:
        raise ConfigError(f"{where}: session, pane and tab are mutually exclusive")
    if "tab" in value:
        if "layout" in value or "title" in value:
            raise ConfigError(f"{where}: put layout/title inside each explicit [[{where}.tab]]")
        result["tabs"] = {}
        for tab in entries(value["tab"], where + ".tab"):
            allowed(tab, {"name", "enabled", "title", "layout", "cwd", "env", "pane"}, where + ".tab")
            tab_name, tid = element_name(tab, where + ".tab")
            if tid in result["tabs"]:
                raise ConfigError(f"{where}: duplicate tab id {tid!r}")
            normalized = {k: copy.deepcopy(v) for k, v in tab.items() if k not in {"name", "pane"}}
            if "title" not in normalized and "name" in tab:
                normalized["title"] = tab_name
            if "pane" in tab:
                normalized["panes"] = panes(tab["pane"], where + ".tab.pane")
            result["tabs"][tid] = normalized
    elif any(key in value for key in ("pane", "layout", "title")):
        if "session_file" in result:
            raise ConfigError(f"{where}: session cannot have layout/title shorthand")
        tab = {key: value[key] for key in ("title", "layout") if key in value}
        if "pane" in value:
            tab["panes"] = panes(value["pane"], where + ".pane")
        result["tabs"] = {IMPLICIT_TAB: tab}
    return result


def normalize_workflow(value: dict, where: str = "workflow") -> dict:
    value = mapping(value, where)
    allowed(value, WORKFLOW_FIELDS, where)
    result = {k: copy.deepcopy(v) for k, v in value.items() if k != "workspace"}
    if "workspace" not in value:
        return result
    nodes = {}

    def add(nid: str, node: dict):
        if nid in nodes:
            raise ConfigError(f"{where}: duplicate element id {nid!r}; window/kitty/container IDs must be unique")
        nodes[nid] = node

    def children(owner: dict, parent: str, location: str):
        # TOML groups repeated arrays by key. Preserve order within each group.
        for kind, items in owner.items():
            if kind not in CHILDREN:
                continue
            for index, item in enumerate(entries(items, location + "." + kind)):
                iw = location + "." + kind
                if kind == "container" and "name" not in item:
                    nid = "container-" + digest(parent, str(index))
                else:
                    _, nid = element_name(item, iw)
                if kind == "kitty":
                    node = {"type": "kitty", "parent": parent, **kitty(item, iw)}
                else:
                    fields = {"name", "enabled", "command", "match", "cwd", "env", "adopt", "size"} if kind == "window" else {"name", "enabled", "layout", "size", *CHILDREN}
                    allowed(item, fields, iw)
                    node = {"type": "app" if kind == "window" else "container", "parent": parent,
                            **{k: copy.deepcopy(v) for k, v in item.items() if k not in CHILDREN | {"name"}}}
                add(nid, node)
                if kind == "container":
                    children(item, nid, iw)

    for workspace in entries(value["workspace"], "workspace"):
        allowed(workspace, {"name", "number", "layout", "enabled", *CHILDREN}, "workspace")
        name, _ = element_name(workspace, "workspace")
        nid = workspace_id(name)
        add(nid, {"type": "workspace", "ref": name,
                  **{k: copy.deepcopy(v) for k, v in workspace.items() if k not in CHILDREN}})
        children(workspace, nid, "workspace")
    result["nodes"] = nodes
    return result


def normalize_document(value: dict, root_name: str = "default") -> dict:
    value = mapping(value, "configuration")
    allowed(value, {"settings", *WORKFLOW_FIELDS}, "configuration")
    result = {"workflows": {}}
    if "settings" in value:
        result["settings"] = copy.deepcopy(value["settings"])
    root = {k: v for k, v in value.items() if k in WORKFLOW_FIELDS}
    if root:
        result["workflows"][identity(root_name, "workflow name")] = normalize_workflow(root)
    return result
