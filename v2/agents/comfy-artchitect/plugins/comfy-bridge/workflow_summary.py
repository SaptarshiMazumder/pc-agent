"""A workflow file, read out loud: what the agent gets instead of a wall of JSON.

WHY NOT THE RAW FILE. An api.json is a map of node ids to class, inputs and links; a UI json is
the editor's own save with positions and widget arrays. Both are the right thing to run and the
wrong thing to read: a model handed 40 KB of it spends the turn reformatting instead of
answering "what does this do and what would change". The summary is the answer-shaped view —
every node, what it is, what it is fed, which slots it declares, which models it names — with
the long strings cut short and the links written as arrows. The raw graph still travels with it
(library_read) when it is small enough, because an edit needs the exact keys.

BOTH FORMATS, TOLD APART. `{"<id>": {"class_type": …}}` is API format; `{"nodes": [...]}` is the
editor's. The summary says which, because only the first can be submitted (rule 7: never convert
by hand).

Pure: a dict in, text out.
"""

from __future__ import annotations

import json
import re
from typing import Any

import reference_slots

#: A string input longer than this is shown cut, with its length — prompts run to paragraphs.
_CUT = 160
#: Field names that name a model file, whatever the node calls them.
_MODEL_FIELD = re.compile(r"(ckpt|checkpoint|unet|model|lora|vae|clip|controlnet|upscale)", re.I)
_MODEL_SUFFIX = (".safetensors", ".ckpt", ".pt", ".pth", ".bin", ".gguf", ".sft")


def _short(s: str) -> str:
    s = s.replace("\n", " ")
    return s if len(s) <= _CUT else f"{s[:_CUT]}… ({len(s)} chars)"


def _node_key(nid: str):
    return (0, int(nid)) if nid.isdigit() else (1, nid)


class WorkflowSummary:
    """One graph, described."""

    def __init__(self, graph: Any) -> None:
        self.graph = graph
        self.format = self._detect(graph)

    @staticmethod
    def _detect(graph: Any) -> str:
        if isinstance(graph, dict) and isinstance(graph.get("nodes"), list):
            return "ui"
        if isinstance(graph, dict) and graph and all(
            isinstance(v, dict) and "class_type" in v for v in graph.values()
        ):
            return "api"
        return "unknown"

    @classmethod
    def from_text(cls, text: str) -> "WorkflowSummary | None":
        try:
            return cls(json.loads(text))
        except ValueError:
            return None

    # ------------------------------------------------------------------ text

    def text(self) -> str:
        if self.format == "api":
            return self._api_text()
        if self.format == "ui":
            return self._ui_text()
        return "not a ComfyUI workflow: neither API format (id → class_type) nor editor format (nodes[])."

    def _api_text(self) -> str:
        g: dict = self.graph
        links = 0
        models: list[str] = []
        lines: list[str] = []
        for nid in sorted(g, key=_node_key):
            node = g[nid]
            title = str((node.get("_meta") or {}).get("title") or "")
            head = f"#{nid} {node.get('class_type')}" + (f' "{title}"' if title else "")
            lines.append(head)
            for field, value in (node.get("inputs") or {}).items():
                if isinstance(value, list) and len(value) == 2 and isinstance(value[1], int):
                    links += 1
                    lines.append(f"    {field} ← #{value[0]}")
                    continue
                if isinstance(value, str):
                    if reference_slots.role_of(value) is not None:
                        lines.append(f"    {field} = {value}   (slot)")
                        continue
                    if _MODEL_FIELD.search(field) or value.lower().endswith(_MODEL_SUFFIX):
                        models.append(value)
                    lines.append(f"    {field} = {json.dumps(_short(value), ensure_ascii=False)}")
                else:
                    lines.append(f"    {field} = {json.dumps(value, ensure_ascii=False)}")
        slots = sorted(reference_slots.roles_in(g))
        head = [
            f"API-format graph: {len(g)} nodes, {links} links.",
            "slots: " + (", ".join(f"@{r}" for r in slots) if slots else "none"),
            "models named: " + (", ".join(dict.fromkeys(models)) if models else "none"),
        ]
        return "\n".join(head + lines)

    def _ui_text(self) -> str:
        g: dict = self.graph
        nodes = [n for n in g.get("nodes") or [] if isinstance(n, dict)]
        lines = [
            f"EDITOR-format graph (what ComfyUI's editor saves): {len(nodes)} nodes, "
            f"{len(g.get('links') or [])} links.",
            "comfy_run needs the API format. If this item has no .api.json beside it, ask the "
            "user for the API export from ComfyUI — never convert by hand (rule 7).",
        ]
        for n in sorted(nodes, key=lambda n: int(n.get("id") or 0)):
            title = str(n.get("title") or "")
            head = f"#{n.get('id')} {n.get('type')}" + (f' "{title}"' if title else "")
            lines.append(head)
            for w in n.get("widgets_values") or []:
                if isinstance(w, str):
                    lines.append(f"    {json.dumps(_short(w), ensure_ascii=False)}")
                else:
                    lines.append(f"    {json.dumps(w, ensure_ascii=False)}")
        return "\n".join(lines)
