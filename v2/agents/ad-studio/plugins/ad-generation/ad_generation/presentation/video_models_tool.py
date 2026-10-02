"""video_models — every clip model the agent can use, for the window's Clip model dropdown. Read only.

THE LIST IS THE SPECS. Each entry of kind "video" in model_specs/<provider>.json is a model the
clip step can drive — its own field names, whether it takes reference images, its price note.
Adding one (MiniMax H3 Max, say) is a spec entry, and it appears here with no code change.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult

from ad_generation.infrastructure.model_spec_book import ModelSpecBook
from ad_generation.presentation.generation_backend_resolver import PLUGIN, GenerationBackendResolver


class VideoModelsTool(Tool):
    name = "video_models"
    label = "Clip models"
    plugin = PLUGIN
    description = (
        "List the clip models available (provider/model, label, whether it takes reference images, "
        "price) and the configured default. Read only."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, config, specs: ModelSpecBook) -> None:
        self.config = config
        self._specs = specs

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            provider, model = GenerationBackendResolver(self.config).resolve("shot_animate", "", "")
            rows = [
                {
                    "id": f"{p}/{m}",
                    "provider": p,
                    "model": m,
                    "label": str(spec.get("label") or m),
                    "references": "references" in (spec.get("fields") or {}),
                    "price": str(spec.get("price_note") or ""),
                }
                for p, m, spec in self._specs.models("video")
            ]
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"video_models: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(
            "\n".join(f"{r['id']} · {r['label']} · {r['price']}" for r in rows),
            details={"default": f"{provider}/{model}", "models": rows},
        )
