"""WorkspaceResource — one file the agent has in its workspace.

The TURN-seam "workspace awareness" layer scans an agent's workspace and surfaces a
manifest of what's there (scripts it wrote, docs/instructions, images, data) so those
resources are ALWAYS visible and reusable — the agent decides whether to read/run each,
but it never has to rediscover that they exist. Pure domain: no IO.
"""

from __future__ import annotations

from dataclasses import dataclass

# coarse kinds, derived from the file extension (see infrastructure adapter)
KIND_SCRIPT = "script"
KIND_DOC = "doc"
KIND_IMAGE = "image"
KIND_DATA = "data"
KIND_OTHER = "other"


@dataclass(frozen=True)
class WorkspaceResource:
    rel_path: str  # path relative to the agent's workspace root
    kind: str  # one of KIND_*
    size: int  # bytes
    summary: str = ""  # short hint (e.g. a script's first comment / a doc's first heading)
