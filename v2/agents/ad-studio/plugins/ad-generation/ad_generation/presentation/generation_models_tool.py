"""generation_models — every image and clip model the agent can use, for the window's model
dropdowns. Read only.

THE LIST IS THE SPECS. Each entry in model_specs/<provider>.json is a model the steps can drive —
its own field names, how many reference images it takes, which clip lengths it can make, its price
note. Adding one is a spec entry, and it appears here with no code change.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.presentation.generation_backend_resolver import IMAGES, PLUGIN, VIDEO, GenerationBackendResolver


class GenerationModelsTool(Tool):
    name = "generation_models"
    label = "Generation models"
    plugin = PLUGIN
    description = (
        "List the image and clip models available (provider/model, label, price, how many reference "
        "images each takes, clip lengths) and the configured defaults. Read only."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, config, specs: ModelSpecBook) -> None:
        self.config = config
        self._specs = specs

    def _rows(self, kind: str) -> list[dict]:
        return [
            {
                "id": f"{p}/{m}",
                "provider": p,
                "model": m,
                "label": str(spec.get("label") or m),
                "price": str(spec.get("price_note") or ""),
                "references": "references" in (spec.get("fields") or {}),
                # how many reference images it takes (None = no stated limit)
                "max_references": spec.get("max_references"),
                # the clip lengths it can make; absent = only its default
                "durations": spec.get("durations"),
                # clips: the resolutions it makes, each with its price; one entry = the only one
                "resolutions": spec.get("resolutions"),
                # an edit mode of a model, for fixing a still — not for making one
                "fix_only": bool(spec.get("fix_only")),
                # a clip model that only edits clips, never makes one
                "edit_only": bool(spec.get("edit_only")),
                # clips: what it does to a clip the user selected, with that mode's price
                "edit": self._mode(spec, "edit"),
                "extend": self._mode(spec, "extend"),
            }
            for p, m, spec in self._specs.models(kind)
        ]

    @staticmethod
    def _mode(spec: dict, mode: str) -> dict | None:
        block = spec.get(mode)
        return {"price": str(block.get("price_note") or "")} if isinstance(block, dict) else None

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            resolver = GenerationBackendResolver(self.config)
            video = "/".join(resolver.resolve(VIDEO, "", ""))
            image = "/".join(resolver.resolve(IMAGES, "", ""))
            fix = "/".join(resolver.resolve("still_fix", "", ""))
            edit = "/".join(resolver.resolve("clip_edit", "", ""))
            clips = self._rows("video")
            makers = [r for r in clips if not r["edit_only"]]
            details = {
                "video": {"default": video, "models": makers},
                "image": {"default": image, "models": [r for r in self._rows("image") if not r["fix_only"]]},
                "fix": {"default": fix, "models": [r for r in self._rows("image") if (r["max_references"] or 99) >= 2]},
                "edit": {"default": edit, "models": [r for r in clips if r["edit"]]},
                # Every clip model extends: natively when it has an extend mode, else by a new clip
                # from the source's last frame (its ordinary clip price).
                "extend": {"default": video, "models": makers},
            }
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"generation_models: {type(e).__name__}: {e}", is_error=True)
        lines = [f"{k}: {r['id']} · {r['label']} · {r['price']}" for k in ("image", "video") for r in details[k]["models"]]
        lines += [f"edit: {r['id']} · {r['label']} · {r['edit']['price']}" for r in details["edit"]["models"]]
        lines += [
            f"extend: {r['id']} · {r['label']} · "
            + (r["extend"]["price"] if r["extend"] else f"new clip from the last frame, {r['price']}")
            for r in details["extend"]["models"]
        ]
        return ToolResult.text("\n".join(lines), details=details)
