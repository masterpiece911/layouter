"""Desired-state records and stable identity keys shared by all backends."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path


def digest(*parts: str) -> str:
    """Hash an unambiguous sequence of identity parts into a compact stable key."""
    data = json.dumps(parts, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(data.encode()).hexdigest()[:24]


@dataclass(frozen=True)
class Pane:
    """A terminal process declaration, including identity and initial placement preferences."""
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
    """A declared kitty tab and its panes in dependency order."""
    id: str
    title: str
    layout: str
    panes: tuple[Pane, ...]


@dataclass(frozen=True)
class Node:
    """A workspace, structural container, GUI application, or kitty OS window declaration."""
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
    """A resolved project session and the complete desired desktop hierarchy."""
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
        """Identify the expanded session within its canonical project directory."""
        return digest(str(self.project), self.session)

    def element_id(self, node: str) -> str:
        """Scope a compositor element identity to this session."""
        return digest(self.session_id, node)

    def mark(self, node: str) -> str:
        """Encode an element identity as a namespaced i3/Sway mark."""
        return "layouter_" + self.element_id(node)

    def pane_key(self, node: str, tab: str, pane: str) -> str:
        """Identify a declared pane independently of its current live tab or title."""
        return digest(self.session_id, node, tab, pane)

    def tab_key(self, node: str, tab: str) -> str:
        """Identify a declared tab within its kitty window and session."""
        return digest(self.session_id, node, tab)

    @property
    def by_id(self) -> dict[str, Node]:
        """Index desired compositor nodes by declaration ID."""
        return {n.id: n for n in self.nodes}

    @property
    def leaves(self) -> tuple[Node, ...]:
        """Return launchable applications and kitty windows in declaration order."""
        return tuple(n for n in self.nodes if n.kind in {"app", "kitty"})
