"""poster_text — edit the words of a text ad whose text is set in real fonts (overlay mode). Free and
instant: nothing is generated, so nothing needs approving.

get         a design's layers (words, area, font, size, colour)
preview     an edit drawn but not saved (the studio's live editor)
save        an edit saved as a new design in the step (the old one stays)
words       new words for some layers, everything else kept: {"headline": "...", "cta": "..."}
retext_all  the brief's current copy on every such design of the step
"""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.poster_text_service import PosterTextService
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class PosterTextTool(Tool):
    name = "poster_text"
    label = "Edit a text ad's words"
    plugin = PLUGIN
    description = (
        "Change the words on a text-ad design whose text is set in real fonts (overlay): `words` "
        "({role: new words} — headline, subline, offer, cta, fine_print) on one `design`, or "
        "`retext_all` to put the brief's current copy on every such design of a step. Free and "
        "instant; the result is a new design in the step. Designs whose words the image model drew "
        "cannot be edited here — use still_fix."
    )
    parameters = {
        "type": "object",
        "required": ["campaign", "step", "action"],
        "properties": {
            "campaign": {"type": "string"},
            "step": {"type": "string"},
            "action": {"type": "string", "enum": ["get", "preview", "save", "words", "retext_all"]},
            "design": {"type": "string", "description": "Workspace path of the design (all actions but retext_all)."},
            "words": {"type": "object", "description": "words: {role: new words}, exactly as the user wrote them."},
            "layers": {"type": "array", "items": {"type": "object"}, "description": "preview / save: every layer, as `get` returns them."},
        },
    }

    def __init__(self, texts: PosterTextService) -> None:
        self._texts = texts

    async def execute(self, tool_call_id, params, abort, on_update=None):
        campaign, step = str(params.get("campaign") or ""), str(params.get("step") or "")
        design = str(params.get("design") or "")
        action = str(params.get("action") or "")
        try:
            if action == "get":
                text = self._texts.get(campaign, design)
                return ToolResult.text(
                    "\n".join(f"{l.role}: {l.text}" for l in text.layers), details={"text": text.to_dict()}
                )
            if action == "preview":
                path = await asyncio.to_thread(self._texts.preview, campaign, design, list(params.get("layers") or []))
                return ToolResult.text(path, details={"preview": path})
            if action == "save":
                made = [await asyncio.to_thread(self._texts.save, campaign, step, design, list(params.get("layers") or []))]
            elif action == "words":
                words = {str(k): str(v) for k, v in dict(params.get("words") or {}).items()}
                made = [await asyncio.to_thread(self._texts.set_words, campaign, step, design, words)]
            elif action == "retext_all":
                made = await asyncio.to_thread(self._texts.retext_all, campaign, step)
            else:
                raise ValueError(f"action is get / preview / save / words / retext_all, not '{action}'")
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"poster_text: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(f"{m.path} (new design from {m.detail.get('retext_of')}, free)" for m in made),
            details={"made": [m.to_dict() for m in made]},
            artifacts=[m.path for m in made],
        )
