"""Resource — one catalogued item in an agent's workspace, with a cached description.

The Resource Manager turns raw workspace files into first-class, described, CRUD-able
resources: scripts the agent wrote, docs/instructions, images, data. Each carries a
``description`` (a first-line summary, image dimensions, or a richer vision/LLM caption)
so the agent can read WHAT a resource is and decide whether to use it — without opening
every file. Pure domain: no IO. The cached index + describers live in infrastructure.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

KIND_SCRIPT = "script"
KIND_DOC = "doc"
KIND_IMAGE = "image"
KIND_DATA = "data"
KIND_OTHER = "other"

_EXT_KIND = {
    **{e: KIND_SCRIPT for e in
       (".py", ".js", ".mjs", ".ts", ".sh", ".bash", ".ps1", ".bat", ".cmd",
        ".rb", ".go", ".rs", ".php", ".pl", ".r")},
    **{e: KIND_DOC for e in (".md", ".markdown", ".txt", ".rst")},
    **{e: KIND_IMAGE for e in
       (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".ico", ".tiff")},
    **{e: KIND_DATA for e in
       (".csv", ".tsv", ".json", ".ndjson", ".yaml", ".yml", ".xml",
        ".xlsx", ".xls", ".parquet", ".db", ".sqlite", ".sqlite3")},
}


def kind_for_path(path) -> str:
    """Coarse resource kind from the file extension."""
    return _EXT_KIND.get(Path(path).suffix.lower(), KIND_OTHER)


@dataclass(frozen=True)
class Resource:
    rel_path: str            # path relative to the agent's workspace root
    kind: str                # one of KIND_*
    size: int                # bytes
    sig: str = ""            # cheap change-signature (size + mtime) for cache staleness
    description: str = ""     # first-line summary / image dims / vision caption
    updated_at: float = 0.0
    enriched: bool = False   # the EXPENSIVE describer already ran for this sig (persisted,
    #                          so a restart never re-summarizes an unchanged file)
