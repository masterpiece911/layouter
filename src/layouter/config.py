"""Discover TOML workflows, bind arguments, and validate resolved desired state.

Relative paths and stable identities are resolved here, before any desktop access."""

from __future__ import annotations

import copy
import math
import os
import re
import shlex
import string
import tomllib
from pathlib import Path
from urllib.parse import quote

from .errors import ConfigError
from .model import Node, Pane, Tab, Workflow, digest
from .schema import normalize_document

ID = re.compile(r"[A-Za-z0-9_-]+\Z")
ARG = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")
BUILTINS = {"project", "project_name", "workflow", "session", "session_id", "element_id"}
I3_LAYOUTS = {"splith": "splith", "splitv": "splitv", "tabbed": "tabbed",
              "stacking": "stacked"}
KITTY_LAYOUTS = {"splits", "tall", "fat", "grid", "horizontal", "vertical", "stack"}
LOCATIONS = {"default", "first", "last", "before", "after", "neighbor", "vsplit", "hsplit", "split"}


def table(value, where: str) -> dict:
    if not isinstance(value, dict):
        raise ConfigError(f"{where}: expected a table")
    return value


def keys(data: dict, allowed: set[str], where: str):
    extra = data.keys() - allowed
    if extra:
        raise ConfigError(f"{where}: unknown field(s): {', '.join(sorted(extra))}")


def text(value, where: str, *, empty: bool = False) -> str:
    if not isinstance(value, str) or (not empty and not value):
        raise ConfigError(f"{where}: expected {'a' if empty else 'a nonempty'} string")
    if "\x00" in value:
        raise ConfigError(f"{where}: NUL is not allowed")
    return value


def line(value, where: str) -> str:
    value = text(value, where)
    if any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise ConfigError(f"{where}: control characters are not allowed")
    return value


def boolean(value, where: str) -> bool:
    if type(value) is not bool:
        raise ConfigError(f"{where}: expected a boolean")
    return value


def identifier(value, where: str) -> str:
    value = text(value, where)
    if not ID.fullmatch(value):
        raise ConfigError(f"{where}: IDs use letters, digits, underscores and hyphens")
    return value


def load(project: Path, selected: str | None = None,
         global_file: Path | None = None, *, workflow: str = "default",
         discover: bool = False, force_global: bool = False) -> tuple[dict, tuple[Path, ...]]:
    """Select whole workflow files using explicit, local, or global precedence."""
    if global_file is None:
        global_dir = Path(os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")) / "layouter"
        global_file = global_dir / "default.toml"
    else:
        global_dir = global_file.parent
    identifier(workflow, "workflow name")
    if selected and force_global:
        raise ConfigError("--file and --global cannot be used together")
    local_dir = project / ".dev"
    local = local_dir / f"{workflow}.toml"
    global_path = global_dir / f"{workflow}.toml"
    if selected:
        explicit = Path(selected).expanduser()
        if not explicit.is_absolute():
            explicit = project / explicit
        if not explicit.is_file():
            raise ConfigError(f"Configuration file does not exist: {explicit}")
        candidates = [(explicit, workflow)]
    elif discover:
        # Replace whole files by name so listing follows the same local-over-global
        # precedence as launching; declarations from the two files never merge.
        chosen = ({path.stem: path for path in sorted(global_dir.glob("*.toml"))}
                  if global_dir.is_dir() else {})
        if not force_global and local_dir.is_dir():
            chosen.update({path.stem: path for path in sorted(local_dir.glob("*.toml"))})
        candidates = [(path, name) for name, path in sorted(chosen.items())]
    else:
        chosen = global_path if force_global or not local.is_file() else local
        if not chosen.is_file():
            scope = "global " if force_global else ""
            raise ConfigError(f"No {scope}workflow {workflow!r} found at {chosen}")
        candidates = [(chosen, workflow)]
    result: dict = {"workflows": {}}
    sources: list[Path] = []
    for path, root_name in candidates:
        try:
            with path.open("rb") as file:
                data = tomllib.load(file)
        except (OSError, tomllib.TOMLDecodeError) as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        try:
            data = normalize_document(data, root_name)
        except ConfigError as exc:
            raise ConfigError(f"{path}: {exc}") from exc
        if root_name not in data["workflows"]:
            raise ConfigError(f"{path}: workflow file contains no workflow fields")
        result["workflows"][root_name] = data["workflows"][root_name]
        if "settings" in data:
            result["settings"] = data["settings"]
        sources.append(path)
    if not sources:
        raise ConfigError(f"No workflows found in {'global configuration' if force_global else 'configuration paths'}")
    workflows = table(result.get("workflows", {}), "workflows")
    if not workflows:
        raise ConfigError("No workflows declared")
    for name in workflows:
        identifier(name, "workflow name")
    settings = table(result.get("settings", {}), "settings")
    keys(settings, {"timeout"}, "settings")
    return result, tuple(sources)


def workflow_data(config: dict, name: str) -> dict:
    """Return an independent workflow copy so resolution cannot mutate loaded configuration."""
    workflows = table(config.get("workflows"), "workflows")
    if name not in workflows:
        raise ConfigError(f"Unknown workflow {name!r}; available: {', '.join(workflows)}")
    return copy.deepcopy(table(workflows[name], f"workflows.{name}"))


def declarations(data: dict) -> list[dict]:
    """Validate argument declarations and return them in positional binding order."""
    values = data.get("args", [])
    if isinstance(values, dict):
        positioned = []
        for name, declaration in values.items():
            table(declaration, f"args.{name}")
            keys(declaration, {"position", "required", "default", "choices", "help"}, f"args.{name}")
            position = declaration.get("position")
            if type(position) is not int or position < 0:
                raise ConfigError(f"args.{name}.position: expected a nonnegative integer")
            required = boolean(declaration.get("required", "default" not in declaration), f"args.{name}.required")
            if required and "default" in declaration:
                raise ConfigError(f"args.{name}: a required argument cannot have a default")
            normalized = {"name": name, **{k: copy.deepcopy(v) for k, v in declaration.items()
                                            if k not in {"position", "required"}}}
            if not required and "default" not in normalized:
                normalized["default"] = ""
            positioned.append((position, normalized))
        positions = [position for position, _ in positioned]
        if len(set(positions)) != len(positions) or sorted(positions) != list(range(len(positions))):
            raise ConfigError("args positions must be unique and contiguous from zero")
        values = [declaration for _, declaration in sorted(positioned)]
    elif not isinstance(values, list):
        raise ConfigError("args: expected a named argument table")
    seen = set(BUILTINS)
    optional = False
    for arg in values:
        table(arg, "argument")
        keys(arg, {"name", "default", "choices", "help"}, "argument")
        name = text(arg.get("name"), "argument.name")
        if not ARG.fullmatch(name) or name in seen:
            raise ConfigError(f"Invalid, duplicate or reserved argument name: {name}")
        seen.add(name)
        if "default" in arg:
            text(arg["default"], f"argument {name} default", empty=True)
            optional = True
        elif optional:
            raise ConfigError("Required arguments cannot follow optional arguments")
        if "choices" in arg:
            if not isinstance(arg["choices"], list) or not arg["choices"]:
                raise ConfigError(f"argument {name}: choices must be a nonempty string array")
            for choice in arg["choices"]:
                text(choice, f"argument {name} choice", empty=True)
            if "default" in arg and arg["default"] not in arg["choices"]:
                raise ConfigError(f"argument {name}: default is not among choices")
        if "help" in arg:
            text(arg["help"], f"argument {name} help", empty=True)
    return values


def bind(data: dict, supplied: list[str]) -> dict[str, str]:
    """Bind supplied workflow arguments, applying defaults and checking allowed choices."""
    args = declarations(data)
    if len(supplied) > len(args):
        raise ConfigError(f"Expected at most {len(args)} workflow arguments, got {len(supplied)}")
    result = {}
    for i, arg in enumerate(args):
        name = arg["name"]
        if i < len(supplied):
            value = text(supplied[i], f"argument {name}", empty=True)
        elif "default" in arg:
            value = arg["default"]
        else:
            raise ConfigError(f"Missing workflow argument: {name}")
        if "choices" in arg and value not in arg["choices"]:
            raise ConfigError(f"{name}: choose one of {', '.join(arg['choices'])}")
        result[name] = value
    return result


def expand(value: str, context: dict[str, str], where: str) -> str:
    """Expand named placeholders with optional shell quoting or URL encoding."""
    parts = []
    try:
        for literal, field, spec, conversion in string.Formatter().parse(value):
            parts.append(literal)
            if field is None:
                continue
            if not ARG.fullmatch(field) or conversion or spec not in {"", "q", "url"}:
                # Disallow Python format attribute/index access and conversions;
                # workflow placeholders are only named values with explicit escaping.
                raise ConfigError(f"{where}: use plain {{name}}, {{name:q}}, or {{name:url}}")
            if field not in context:
                raise ConfigError(f"{where}: unknown placeholder {{{field}}}")
            replacement = context[field]
            parts.append(shlex.quote(replacement) if spec == "q" else
                         quote(replacement, safe="") if spec == "url" else replacement)
    except ValueError as exc:
        raise ConfigError(f"{where}: invalid interpolation: {exc}") from exc
    return "".join(parts)


def expanded(value, context: dict[str, str], where: str):
    """Recursively interpolate values while preserving dictionary keys and non-string types."""
    if isinstance(value, str):
        return expand(value, context, where)
    if isinstance(value, dict):
        return {k: expanded(v, context, f"{where}.{k}") for k, v in value.items()}
    if isinstance(value, list):
        return [expanded(v, context, f"{where}[{i}]") for i, v in enumerate(value)]
    return value


def cwd(value, project: Path, where: str) -> Path:
    """Resolve a working directory relative to the canonical project, not its parent node."""
    path = Path(text(value, where)).expanduser()
    return (path if path.is_absolute() else project / path).resolve()


def env(value, parent: dict[str, str], where: str) -> dict[str, str]:
    """Validate and overlay environment entries on a copy of the inherited environment."""
    result = dict(parent)
    for key, val in table(value, where).items():
        if not ARG.fullmatch(key):
            raise ConfigError(f"{where}: invalid environment name {key!r}")
        result[key] = text(val, f"{where}.{key}", empty=True)
    return result


def command(value, where: str, *, required: bool = False) -> tuple[str, ...]:
    """Validate an argv array without interpreting shell syntax."""
    if not isinstance(value, list) or (required and not value):
        raise ConfigError(f"{where}: expected an argv array; use ['sh', '-c', script] for a shell")
    values = tuple(text(v, where, empty=True) for v in value)
    if values and not values[0]:
        raise ConfigError(f"{where}: executable cannot be empty")
    return values


def enabled_tables(data, where: str) -> dict[str, dict]:
    """Return enabled declarations after validating their IDs and enabled flags."""
    result = {}
    for key, value in table(data, where).items():
        identifier(key, where)
        table(value, f"{where}.{key}")
        if boolean(value.get("enabled", True), f"{where}.{key}.enabled"):
            result[key] = value
    return result


def ordered_panes(panes: list[Pane], where: str) -> tuple[Pane, ...]:
    """Order panes after their dependencies, rejecting missing references and cycles."""
    by_id = {p.id: p for p in panes}
    done, visiting, result = set(), set(), []

    def visit(pane: Pane):
        if pane.id in done:
            return
        if pane.id in visiting:
            # A node on the active DFS path is a cycle; a completed node is merely
            # a dependency shared by several panes and can be reused safely.
            raise ConfigError(f"{where}: cyclic pane 'after' references")
        visiting.add(pane.id)
        if pane.after:
            if pane.after not in by_id:
                raise ConfigError(f"{where}.{pane.id}: unknown after pane {pane.after!r}")
            visit(by_id[pane.after])
        visiting.remove(pane.id)
        done.add(pane.id)
        result.append(pane)

    for pane in panes:
        visit(pane)
    return tuple(result)


def tabs(data: dict, project: Path, node_cwd: Path, node_env: dict, where: str) -> tuple[Tab, ...]:
    """Resolve tab and pane settings with inherited working directories and environments."""
    result = []
    for tid, tab in enabled_tables(data, where).items():
        tw = f"{where}.{tid}"
        keys(tab, {"title", "layout", "cwd", "env", "panes", "enabled"}, tw)
        title = line(tab.get("title", tid), tw + ".title")
        layout = line(tab.get("layout", "tall"), tw + ".layout")
        if layout.split(":", 1)[0] not in KITTY_LAYOUTS or not re.fullmatch(r"[A-Za-z0-9_:.;=+%/-]+", layout):
            raise ConfigError(f"{tw}: unknown kitty layout {layout!r}")
        tab_cwd = cwd(tab.get("cwd", str(node_cwd)), project, tw + ".cwd")
        tab_env = env(tab.get("env", {}), node_env, tw + ".env")
        panes = []
        for pid, pane in enabled_tables(tab.get("panes", {}), tw + ".panes").items():
            pw = f"{tw}.panes.{pid}"
            keys(pane, {"command", "cwd", "env", "title", "hold", "after", "location", "enabled"}, pw)
            location = text(pane.get("location", "default"), pw + ".location")
            if location not in LOCATIONS:
                raise ConfigError(f"{pw}: unsupported location {location!r}")
            panes.append(Pane(
                id=pid, command=command(pane.get("command", []), pw + ".command"),
                cwd=cwd(pane.get("cwd", str(tab_cwd)), project, pw + ".cwd"),
                env=env(pane.get("env", {}), tab_env, pw + ".env"),
                title=line(pane["title"], pw + ".title") if "title" in pane else None,
                hold=boolean(pane.get("hold", False), pw + ".hold"),
                after=identifier(pane["after"], pw + ".after") if "after" in pane else None,
                location=location,
            ))
        if not panes:
            raise ConfigError(f"{tw}: a tab needs at least one enabled pane")
        result.append(Tab(tid, title, layout, ordered_panes(panes, tw)))
    return tuple(result)


def resolve(config: dict, project: Path, name: str = "default",
            supplied: list[str] | None = None, sources: tuple[Path, ...] = ()) -> Workflow:
    """Build a validated Workflow with expanded values, stable IDs, and checked tree references."""
    project = project.expanduser().resolve()
    if not project.is_dir():
        raise ConfigError(f"Project directory does not exist: {project}")
    data = workflow_data(config, name)
    keys(data, {"session", "args", "cwd", "env", "focus", "nodes", "description"}, f"workflow {name}")
    arguments = bind(data, supplied or [])
    context = {**arguments, "project": str(project), "project_name": project.name, "workflow": name}
    session = line(expand(text(data.get("session", "{workflow}"), "session"), context, "session"), "session")
    sid = digest(str(project), session)
    context.update(session=session, session_id=sid)
    base_cwd = cwd(expanded(data.get("cwd", "{project}"), context, "cwd"), project, "cwd")
    base_env = env(expanded(data.get("env", {}), context, "env"), {}, "env")
    raw_nodes = table(data.get("nodes", {}), "nodes")
    active = enabled_tables(raw_nodes, "nodes")
    # Disable descendants before validating references to omitted parents.
    omitted = raw_nodes.keys() - active.keys()
    while True:
        descendants = {key for key, val in active.items() if val.get("parent") in omitted}
        if not descendants:
            break
        omitted |= descendants
        active = {key: val for key, val in active.items() if key not in descendants}
    nodes = []
    focus_aliases = {}
    for nid, raw in active.items():
        nw = f"nodes.{nid}"
        local_context = {**context, "element_id": digest(sid, nid)}
        n = expanded(raw, local_context, nw)
        kind = text(n.get("type"), nw + ".type")
        allowed = {"enabled", "type"}
        if kind == "workspace":
            allowed |= {"name", "number", "layout", "ref"}
        elif kind == "container":
            allowed |= {"parent", "layout", "size"}
            if "layout" not in n:
                raise ConfigError(f"{nw}: containers require a layout")
        elif kind == "app":
            allowed |= {"parent", "command", "match", "cwd", "env", "adopt", "size"}
        elif kind == "kitty":
            allowed |= {"parent", "cwd", "env", "tabs", "executable", "config", "options", "session_file",
                        "class", "size"}
        else:
            raise ConfigError(f"{nw}: unknown node type {kind!r}")
        keys(n, allowed, nw)
        parent = identifier(n.get("parent"), nw + ".parent") if kind != "workspace" else None
        declared_layout = text(n.get("layout", "splith"), nw + ".layout")
        if declared_layout not in I3_LAYOUTS:
            raise ConfigError(f"{nw}: unknown compositor layout {declared_layout!r}")
        layout = I3_LAYOUTS[declared_layout]
        ncwd = cwd(n.get("cwd", str(base_cwd)), project, nw + ".cwd")
        nenv = env(n.get("env", {}), base_env, nw + ".env")
        match = table(n.get("match", {}), nw + ".match")
        keys(match, {"class", "app_id", "instance", "title", "window_role"}, nw + ".match")
        for key, val in match.items():
            try:
                re.compile(text(val, nw + ".match." + key))
            except re.error as exc:
                raise ConfigError(f"{nw}.match.{key}: {exc}") from exc
        if kind == "app" and n.get("adopt", False) and not match:
            raise ConfigError(f"{nw}: adopt requires an explicit window matcher")
        size = n.get("size")
        if size is not None and (type(size) not in {int, float} or not math.isfinite(size) or size <= 0 or size > 100):
            raise ConfigError(f"{nw}.size: expected a percentage greater than 0 and at most 100")
        if kind == "workspace" and "number" in n and (type(n["number"]) is not int or n["number"] < 0):
            raise ConfigError(f"{nw}.number: expected a nonnegative integer")
        session_file = cwd(n["session_file"], project, nw + ".session") if "session_file" in n else None
        if session_file and "tabs" in n:
            raise ConfigError(f"{nw}: session and tabs are mutually exclusive")
        ntabs = tabs(n.get("tabs", {}), project, ncwd, nenv, nw + ".tabs") if kind == "kitty" else ()
        if kind == "kitty" and not session_file and not ntabs:
            raise ConfigError(f"{nw}: kitty needs panes, tabs, or session")
        options = table(n.get("options", {}), nw + ".options")
        reserved = {"allow_remote_control", "listen_on", "startup_session", "env", "linux_display_server"}
        if reserved & options.keys():
            raise ConfigError(f"{nw}: reserved kitty option(s): {', '.join(sorted(reserved & options.keys()))}")
        for key, val in options.items():
            if not ARG.fullmatch(key):
                raise ConfigError(f"{nw}: invalid kitty option {key!r}")
            line(val, nw + ".options." + key)
        executable = text(n.get("executable", "kitty"), nw + ".executable")
        if "/" in executable:
            executable = str(cwd(executable, project, nw + ".executable"))
        if kind == "workspace" and "ref" in n:
            focus_aliases[line(n["ref"], nw + ".ref")] = nid
        nodes.append(Node(
            id=nid, kind=kind, parent=parent, cwd=ncwd, env=nenv,
            name=(f"{n['number']}: {line(n.get('name'), nw + '.name')}" if "number" in n
                  else line(n.get("name"), nw + ".name")) if kind == "workspace" else None,
            layout=layout, command=command(n.get("command", []), nw + ".command", required=kind == "app"),
            match=match, adopt=boolean(n.get("adopt", False), nw + ".adopt"),
            tabs=ntabs, executable=executable,
            config=cwd(n["config"], project, nw + ".config") if "config" in n else None,
            options=options, session_file=session_file,
            wm_class=line(n["class"], nw + ".class") if "class" in n else None,
            size=float(size) if size is not None else None,
        ))
    by_id = {n.id: n for n in nodes}
    for node in nodes:
        seen = {node.id}
        parent = node.parent
        while parent:
            if parent in seen:
                raise ConfigError(f"{node.id}: parent cycle")
            seen.add(parent)
            if parent not in by_id or by_id[parent].kind not in {"workspace", "container"}:
                raise ConfigError(f"{node.id}: parent {parent!r} must be an enabled workspace/container")
            parent = by_id[parent].parent
    workspace_names = [n.name for n in nodes if n.kind == "workspace"]
    if len(set(workspace_names)) != len(workspace_names):
        raise ConfigError("Workspace names must be unique within a workflow")
    focus = expanded(data.get("focus"), context, "focus")
    if isinstance(focus, str):
        focus = focus_aliases.get(focus, focus)
    targets = set(by_id)
    for n in nodes:
        for tab in n.tabs:
            targets.add(f"{n.id}.{tab.id}")
            targets.update(f"{n.id}.{tab.id}.{p.id}" for p in tab.panes)
    if focus is not None and (not isinstance(focus, str) or focus not in targets):
        raise ConfigError(f"Unknown focus target: {focus!r}")
    timeout = config.get("settings", {}).get("timeout", 30)
    if type(timeout) not in {int, float} or not math.isfinite(timeout) or timeout <= 0:
        raise ConfigError("settings.timeout must be a finite positive number")
    return Workflow(project, name, session, arguments, tuple(nodes), focus, float(timeout), sources)
