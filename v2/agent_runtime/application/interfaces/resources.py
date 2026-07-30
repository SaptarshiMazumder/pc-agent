"""Resource Manager ports.

``ResourceStore``    — the durable, cached index of an agent's resources (path -> metadata
                       + description). Keeps descriptions so they aren't recomputed each turn.
``ResourceDescriber`` — turns a file into a one-line description. The default is deterministic
                       (first line / image dimensions); a richer vision/LLM describer can swap
                       in behind the same port. Sync, so the per-turn manifest can use it cheaply;
                       expensive (vision) captioning is an explicit, async manager call.

Driven ports: the core depends on these interfaces, never on sqlite or any model.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from agent_runtime.domain.resource import Resource


class ResourceStore(Protocol):
    def get(self, agent_id: str, rel_path: str) -> Resource | None: ...
    def put(self, agent_id: str, res: Resource) -> None: ...
    def delete(self, agent_id: str, rel_path: str) -> bool: ...
    def list(self, agent_id: str) -> list[Resource]: ...
    def close(self) -> None: ...


class ResourceDescriber(Protocol):
    def describe(self, kind: str, path: Path, sample: bytes) -> str:
        """A short, deterministic one-line description (sync). ``sample`` is the first
        bytes of the file (header for images, text head for code/docs)."""
        ...
