"""JSON-compatible workflow metadata for launchers and other integrations.

Discovery never executes TSX. Inspecting one selected TSX workflow imports its
module to read declarations, but never renders it or queries the desktop.
"""
from __future__ import annotations

from pathlib import Path

from .config import declarations, keys, load, workflow_data, workflow_description

SCHEMA_VERSION = 1


def _arguments(data: dict) -> list[dict]:
    return [dict(arg, position=position, required="default" not in arg)
            for position, arg in enumerate(declarations(data))]


def _describe(config: dict, name: str, path: Path, *, evaluate: bool) -> dict:
    source = config.get("_react_sources", {}).get(name)
    data = workflow_data(config, name)
    arguments = None
    if source is None:
        arguments = _arguments(data)
    elif evaluate:
        with source:
            metadata = source.metadata()
        keys(metadata, {"args", "description"}, "React metadata")
        data = metadata
        arguments = _arguments(metadata)
    description = workflow_description(data)
    return {
        "name": name,
        "source": str(path.resolve()),
        "format": "tsx" if source else "toml",
        "description": description,
        "args": arguments,
    }


def list_workflows(project: Path, selected: str | None = None, *,
                   workflow: str = "default", force_global: bool = False) -> list[dict]:
    """List effective sources and metadata without executing programmable files.

    TSX args and descriptions are None (unknown); TOML args are ordered records.
    Uses the same source precedence and ConfigError failures as normal launches.
    """
    config, paths = load(project, selected, workflow=workflow, discover=True,
                         force_global=force_global)
    return [_describe(config, name, path, evaluate=False)
            for name, path in zip(config["workflows"], paths)]


def describe_workflow(project: Path, workflow: str = "default", *,
                      selected: str | None = None, force_global: bool = False) -> dict:
    """Inspect one workflow without binding arguments or rendering its layout.

    For TSX this executes module-level code and requires the React runtime.
    Template values remain unexpanded; descriptions are literal summaries.
    """
    config, paths = load(project, selected, workflow=workflow, force_global=force_global)
    return _describe(config, workflow, paths[0], evaluate=True)


def list_args(project: Path, workflow: str = "default", *,
              selected: str | None = None, force_global: bool = False) -> list[dict]:
    """Return validated argument declarations in positional order.

    Each record includes name, position and required, plus default, choices and
    help when declared. Optional arguments without a default use the empty string.
    """
    return describe_workflow(project, workflow, selected=selected,
                             force_global=force_global)["args"]
