"""Recipe — one ready workflow from the knowledge base: a family's graph for one task and variant.

`knowledge_base/<family>/recipes/<id>.api.json` is `{"recipe": {...}, "graph": {...}}`. The graph
is pure ComfyUI API format, built from the official template and checked against the node catalogue
of the pinned ComfyUI. The recipe part says what a stage built from it exposes:

    ports    the settings a design may change — {name: {node, input}} or {nodes: [...], input}
    inputs   the media it needs, each a reference slot (`@start_image`) on a loader
    outputs  what it produces — {name: {node, type}}
    files    every model file the graph names (their records are in the family's profile)
    packs    node packs it needs beyond core ComfyUI
    min_comfyui, confidence, from_template — where it came from and what it needs

A design starts from a recipe and changes ports; the wiring stays what the publisher shipped.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

SUFFIX = ".api.json"
_VERSION = re.compile(r"(\d+)(?:\.(\d+))?(?:\.(\d+))?")


@dataclass
class Recipe:
    id: str
    family: str
    task: str
    path: Path
    meta: dict
    graph: dict = field(repr=False)

    @classmethod
    def load(cls, path: Path) -> "Recipe":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        meta, graph = data.get("recipe") or {}, data.get("graph") or {}
        if not meta or not graph:
            raise ValueError(f"{path}: a recipe needs `recipe` and `graph`")
        rid = str(meta.get("id") or Path(path).name[: -len(SUFFIX)])
        return cls(id=rid, family=str(meta.get("family") or ""), task=str(meta.get("task") or ""),
                   path=Path(path), meta=meta, graph=graph)

    @property
    def ports(self) -> dict:
        return dict(self.meta.get("ports") or {})

    @property
    def inputs(self) -> dict:
        return dict(self.meta.get("inputs") or {})

    @property
    def outputs(self) -> dict:
        return dict(self.meta.get("outputs") or {})

    @property
    def files(self) -> list[str]:
        return [str(f) for f in self.meta.get("files") or []]

    @property
    def packs(self) -> list:
        return list(self.meta.get("packs") or [])

    @property
    def min_comfyui(self) -> tuple[int, int, int]:
        return version_tuple(str(self.meta.get("min_comfyui") or "0"))

    def runs_on(self, comfyui_version: str) -> bool:
        """False when the recipe needs a newer ComfyUI than this one."""
        have = version_tuple(comfyui_version)
        return have == (0, 0, 0) or self.min_comfyui <= have


def version_tuple(text: str) -> tuple[int, int, int]:
    m = _VERSION.search(str(text or ""))
    if not m:
        return (0, 0, 0)
    return tuple(int(g or 0) for g in m.groups())  # type: ignore[return-value]


__all__ = ["Recipe", "version_tuple"]
