"""The FACTS a template's about is written from — everything the saved chat can show, and nothing
it cannot.

Per step: the workflow's own notes (the Markdown boxes a tutorial writes into its graph), the
prompt text it ships, the settings a person would ask about (size, length, frame rate, steps),
the node types it uses; then the setup guide's models and packs, any paid services, the inputs
the person fills, and how long this chat's runs of it took. The about's writer is told to use
these and to invent nothing, so every line of the about can be traced back here.
"""

from __future__ import annotations

import json
from pathlib import Path

import studio_state
from template_setup_guide_builder import TemplateSetupGuideBuilder

#: Settings a person asks about, by the input names ComfyUI nodes use for them.
_SETTINGS = ("width", "height", "length", "frames", "num_frames", "fps", "frame_rate", "duration",
             "seconds", "steps", "cfg", "megapixels", "aspect_ratio", "resolution", "value")
#: Node types that hold the prompt a workflow ships.
_PROMPT_TYPES = ("PrimitiveStringMultiline", "PrimitiveString", "CLIPTextEncode", "TextEncodeQwenImageEdit")
_MAX_NOTE = 3000
_MAX_FACTS = 16000


class TemplateAboutFactsBuilder:
    def __init__(self, workspace: Path) -> None:
        self.workspace = Path(workspace).resolve()

    def build(self, *, name: str, description: str, steps: list[dict], inputs: list[dict]) -> str:
        """`steps`: [{role, api, ui?, manifests: [...]}] with workspace-relative paths."""
        out = [f"TEMPLATE NAME: {name}", f"THE SAVER'S ONE-LINE DESCRIPTION: {description or '(none)'}"]
        manifests: list[str] = []
        for step in steps:
            role = str(step.get("role") or "")
            out.append(f"\n=== STEP '{role}' ===")
            api = self._json(step.get("api"))
            if isinstance(api, dict):
                classes = sorted({n.get("class_type") for n in api.values() if isinstance(n, dict)} - {None})
                out.append("Node types: " + ", ".join(classes))
                for nid, node in api.items():
                    if not isinstance(node, dict):
                        continue
                    for k, v in (node.get("inputs") or {}).items():
                        if k in _SETTINGS and isinstance(v, (str, int, float)) and not isinstance(v, bool):
                            out.append(f"Setting {node.get('class_type')}.{k} = {v}")
                        if isinstance(v, str) and v.startswith("@"):
                            out.append(f"Input slot {v} feeds {node.get('class_type')}.{k}")
            ui = self._json(step.get("ui")) or {}
            for node in ui.get("nodes") or []:
                values = node.get("widgets_values") if isinstance(node, dict) else None
                if not isinstance(values, list) or not values or not isinstance(values[0], str):
                    continue
                if node.get("type") in ("MarkdownNote", "Note"):
                    out.append("Workflow note:\n" + values[0][:_MAX_NOTE])
                elif node.get("type") in _PROMPT_TYPES and len(values[0]) > 20:
                    out.append("Shipped prompt:\n" + values[0][:_MAX_NOTE])
            runs = [r for r in (studio_state.read().get("runs") or [])
                    if r.get("name") == role and r.get("status") == "complete"]
            if runs:
                took = ", ".join(str(r.get("duration")) + " s" for r in runs[:3])
                out.append(f"Completed runs in the saved chat took: {took}")
            manifests += [str(m) for m in step.get("manifests") or []]
        if manifests:
            guide, gaps = TemplateSetupGuideBuilder(self.workspace).build(manifests)
            out.append("\n=== SETUP ===")
            out += [f"Model: {m.filename} ({m.kind})" for m in guide.models]
            out += [f"Node pack: {p.name} ({p.repository})" for p in guide.node_packs]
            paid = sorted({n for m in manifests for n in (self._json(m) or {}).get("paid_api_nodes") or []})
            out.append("Paid services: " + (", ".join(paid) if paid else "none — runs on the machine's own GPU, 0 credits"))
        if inputs:
            out.append("\n=== INPUTS THE PERSON FILLS ===")
            out += [f"@{i.get('role')}: {i.get('what') or ''}" for i in inputs]
        return "\n".join(out)[:_MAX_FACTS]

    def _json(self, rel) -> dict | None:
        if not rel:
            return None
        path = (self.workspace / str(rel)).resolve()
        if not path.is_relative_to(self.workspace / "workflows"):
            raise ValueError(f"{rel} is not a file of a chat's workflows")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None
