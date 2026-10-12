"""story_draft — write a new story from a theme and/or the user's own words, with the agent's
model. The draft is saved; nothing is made until the user approves it."""

from __future__ import annotations

import asyncio

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.application.run_context import current_run_context

from ad_generation.presentation.generation_backend_resolver import PLUGIN
from ad_generation.presentation.story_model_tool import StoryModelTool
from ad_generation.presentation.story_text import StoryText


class StoryDraftTool(StoryModelTool):
    name = "story_draft"
    label = "Draft a story"
    plugin = PLUGIN
    description = (
        "Draft a story — who it is for, the occasion, the offer, the hook and the SHOT LIST of what to "
        "make — from a `theme` (story_suggest) and/or the user's own `words`, for the picked "
        "`products`. Show it and ask the questions it returns; nothing is made before story_approve."
    )
    parameters = {
        "type": "object",
        "properties": {
            "theme": {"type": "string", "description": "A theme key from story_suggest; empty when the user told their own story."},
            "words": {"type": "string", "description": "The user's own words about the story, exactly."},
            "products": {"type": "array", "items": {"type": "string"}, "description": "Shop product keys."},
            "occasion": {"type": "string", "description": "An occasion key from story_suggest, or the user's words."},
            "answers": {"type": "object", "description": "The user's answers to earlier questions (offer, audience, …)."},
            "auto": {"type": "boolean", "description": "Only when the user asked for Auto: the agent fills unchosen counts, shapes and lengths."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            story, questions = await asyncio.to_thread(
                self._service().draft,
                str(params.get("theme") or ""), str(params.get("words") or ""),
                [str(p) for p in params.get("products") or []], str(params.get("occasion") or ""),
                dict(params.get("answers") or {}), bool(params.get("auto")),
                str(getattr(current_run_context(), "session_key", "") or ""),
            )
        except Exception as e:  # noqa: BLE001 — every failure is reported, with its reason
            return ToolResult.text(f"story_draft: {type(e).__name__}: {e}", is_error=True)
        return ToolResult.text(StoryText.of(story, questions), details={"story": story.to_dict(), "questions": questions})
