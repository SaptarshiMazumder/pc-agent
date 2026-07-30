"""WorkspaceIndex port — surfaces an agent's workspace files for the prompt.

A driven port (the core depends on this interface, not the file scan). The default
adapter is a live directory scan; a richer one (descriptions, embeddings, a persisted
index) can swap in behind the same port without touching callers. Returning a ready
manifest string keeps the prompt assembler pure (no IO).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_runtime.domain.workspace import WorkspaceResource


class WorkspaceIndex(Protocol):
    def scan(self, workspace: Path) -> list[WorkspaceResource]:
        """The resources currently in the workspace (bounded, noise filtered)."""
        ...

    def manifest(self, workspace: Path, agent_id: str = "") -> str:
        """A ready-to-inject markdown block listing those resources ("" if none).
        ``agent_id`` lets a cached/described index scope its metadata per agent."""
        ...
