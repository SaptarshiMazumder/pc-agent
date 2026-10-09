"""brand_profile — the account's name, handle, tagline, voice, fonts and colours, which every post
carries. Read it, or change the fields the user names."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.application.brand_service import BrandService
from ad_generation.domain.brand_profile import HEADING_FONTS, LABEL_FONTS
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class BrandProfileTool(Tool):
    name = "brand_profile"
    label = "Brand profile"
    plugin = PLUGIN
    description = (
        "Read the account's brand (name, handle, tagline — the line posts sign off with — voice, caption "
        "disclosure, fonts, colours, design notes), or `set` the fields the user named, in their words. `design_notes` is the "
        "WHOLE list of the brand's design rules (read it first, then set it with the new rule added or the "
        "old one changed); every design and review follows them."
    )
    parameters = {
        "type": "object",
        "required": ["action"],
        "properties": {
            "action": {"type": "string", "enum": ["get", "set"]},
            "name": {"type": "string"},
            "handle": {"type": "string"},
            "tagline": {"type": "string"},
            "voice": {"type": "string"},
            "caption_disclosure": {"type": "string", "description": "The line every caption ends with, in the user's words."},
            "heading_font": {"type": "string", "enum": list(HEADING_FONTS)},
            "label_font": {"type": "string", "enum": list(LABEL_FONTS)},
            "text_color": {"type": "string", "description": "#rrggbb"},
            "accent_color": {"type": "string", "description": "#rrggbb"},
            "design_notes": {"type": "array", "items": {"type": "string"}, "description": "The whole list of design rules, one short rule each."},
        },
    }

    def __init__(self, brand: BrandService) -> None:
        self._brand = brand

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            if params.get("action") == "set":
                brand = self._brand.update({k: v for k, v in params.items() if k != "action"})
            else:
                brand = self._brand.get()
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"brand_profile: {type(e).__name__}: {e}", is_error=True)
        d = brand.to_dict()
        lines = [f"{k}: {v}" for k, v in d.items() if k != "design_notes"]
        lines += ["design notes:"] + [f"- {n}" for n in d["design_notes"]] if d["design_notes"] else ["design notes: none yet"]
        return ToolResult.text("\n".join(lines), details={"brand": d})
