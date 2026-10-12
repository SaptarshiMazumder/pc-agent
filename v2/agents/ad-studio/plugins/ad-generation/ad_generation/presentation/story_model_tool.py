"""The base of the story tools that write with the agent's model: it builds the StoryService with a
reasoner on the configured story model (the agent's own model unless agent.toml names one)."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool
from agent_runtime.application.tool_models import brain_model, resolve_tool_model

from ad_generation.infrastructure.model_access_reasoner import ModelAccessReasoner
from ad_generation.infrastructure.vision_image_preparer import VisionImagePreparer
from ad_generation.presentation.generation_backend_resolver import PLUGIN


class StoryModelTool(Tool):
    needs_model = True
    default_timeout_sec = 300.0

    def __init__(self, config, service_factory, images: VisionImagePreparer) -> None:
        self.config = config
        self._factory = service_factory
        self._images = images

    def _service(self):
        model = resolve_tool_model(self.config, PLUGIN, "story_draft") or brain_model(self.config)
        return self._factory(ModelAccessReasoner(self.models, model, self._images, timeout_s=180))
