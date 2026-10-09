"""The base of the post tools that plan with the agent's model: it builds the PostService with a reasoner on the configured planning model."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import Tool
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.tool_progress_reporter import ToolProgressReporter


class PostModelTool(Tool):
    needs_model = True
    model_kind = "vision"
    default_timeout_sec = 600.0

    def __init__(self, config, service_factory, images: VisionImagePreparer) -> None:
        self.config = config
        self._factory = service_factory
        self._images = images

    def _service(self, on_update):
        model = resolve_tool_model(self.config, PLUGIN, "post_plan", kind="vision") or brain_model(self.config)
        reasoner = ModelAccessReasoner(self.models, model, self._images, timeout_s=180)
        return self._factory(reasoner, ToolProgressReporter(asyncio.get_running_loop(), on_update))
