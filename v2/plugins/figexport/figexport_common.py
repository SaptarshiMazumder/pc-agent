"""Shared helpers for figexport (plugin-prefixed). Path resolution + px<->EMU at 96 dpi so figure
pixel coordinates map 1:1 onto a PowerPoint slide sized in the same pixels."""

from __future__ import annotations

from pathlib import Path

from agent_runtime.application.run_context import current_workspace

EMU_PER_PX = 9525   # 914400 EMU/inch / 96 px/inch


def resolve_path(config, p: str) -> Path:
    path = Path(p)
    if path.is_absolute():
        return path
    ws = current_workspace(str(getattr(config, "workspace", "."))) or "."
    return Path(ws) / path


def px(v) -> int:
    """pixels -> EMU."""
    return int(round(float(v) * EMU_PER_PX))
