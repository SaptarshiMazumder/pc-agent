"""Resource Manager composition — the cached index + describer + manager.

build_resource_manager(config) -> ResourceManager | None  (None unless the layer is on).
The vision/LLM ``rich_fn`` seam is left None by default (deterministic descriptions work
everywhere); a Gemini caption fn can be injected here later without touching callers.
"""

from __future__ import annotations

from pathlib import Path

from agentd.infrastructure.resources.describe import BasicDescriber
from agentd.infrastructure.resources.manager import ResourceManager
from agentd.infrastructure.resources.store import SqliteResourceStore

__all__ = ["ResourceManager", "SqliteResourceStore", "BasicDescriber", "build_resource_manager"]


def build_resource_manager(config):
    if not getattr(config, "resource_manager_enabled", False):
        return None
    from agentd.infrastructure.resources.vision import build_rich_fn

    store = SqliteResourceStore(Path(config.state_dir) / "resources.sqlite")
    return ResourceManager(
        store,
        BasicDescriber(),
        max_files=getattr(config, "resource_index_max_files", 100),
        rich_fn=build_rich_fn(config),  # Gemini vision captions (background) — None unless enabled
    )
