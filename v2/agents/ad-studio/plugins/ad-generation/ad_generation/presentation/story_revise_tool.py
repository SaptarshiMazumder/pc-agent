"""story_revise — rewrite a story with the user's change or answers, with the agent's model. Only
what they asked changes; the shots keep their ids. A new version: approval is asked again."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.story_model_tool import StoryModelTool
from ad_generation.presentation.story_text import StoryText


class StoryReviseTool(StoryModelTool):
    name = "story_revise"
    label = "Revise a story"
    plugin = PLUGIN
    description = (
        "Rewrite a story with the user's `change` in their words (a new hook, another look, a different "
        "occasion, their own story) and/or `answers` to its questions. Precise field edits (a count, a "
        "shape, one shot's text) are story_update instead — free."
    )
    parameters = {
        "type": "object",
        "required": ["story"],
        "properties": {
            "story": {"type": "string"},
            "change": {"type": "string", "description": "What the user wants different, in their words."},
            "answers": {"type": "object", "description": "Their answers to the story's questions."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            story, questions = await asyncio.to_thread(
                self._service().revise, str(params.get("story") or ""), str(params.get("change") or ""),
                dict(params.get("answers") or {}),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_revise: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(StoryText.of(story, questions), details={"story": story.to_dict(), "questions": questions})
