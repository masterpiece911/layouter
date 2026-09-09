from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


def digest(*parts: str) -> str:
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Pane:
    id: str
    cwd: Path
    env: dict[str, str]
    command: tuple[str, ...] = ()
    title: str | None = None
    hold: bool = False
    after: str | None = None
    location: str = "default"


@dataclass(frozen=True)
class Tab:
    id: str
    title: str
    layout: str
    panes: tuple[Pane, ...]


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    cwd: Path
    env: dict[str, str]
    parent: str | None = None
    name: str | None = None
    layout: str = "splith"
    command: tuple[str, ...] = ()
    match: dict[str, str] = field(default_factory=dict)
    adopt: bool = False
    tabs: tuple[Tab, ...] = ()
    executable: str = "kitty"
    config: Path | None = None
    options: dict[str, str] = field(default_factory=dict)
    session_file: Path | None = None
    wm_class: str | None = None
    size: float | None = None


@dataclass(frozen=True)
class Workflow:
    project: Path
    name: str
    session: str
    arguments: dict[str, str]
    nodes: tuple[Node, ...]
    focus: str | None
    timeout: float
    sources: tuple[Path, ...] = ()

    @property
    def session_id(self) -> str:
        return digest(str(self.project), self.session)

    def element_id(self, node: str) -> str:
        return digest(self.session_id, node)

    def mark(self, node: str) -> str:
        return "layouter_" + self.element_id(node)

    def pane_key(self, node: str, tab: str, pane: str) -> str:
        return digest(self.session_id, node, tab, pane)

    def tab_key(self, node: str, tab: str) -> str:
        return digest(self.session_id, node, tab)

    @property
    def by_id(self) -> dict[str, Node]:
        return {n.id: n for n in self.nodes}

    @property
    def leaves(self) -> tuple[Node, ...]:
        return tuple(n for n in self.nodes if n.kind in {"app", "kitty"})
