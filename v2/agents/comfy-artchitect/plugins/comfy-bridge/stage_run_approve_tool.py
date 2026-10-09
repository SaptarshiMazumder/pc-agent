"""stage_run_approve — the Run button on a stage: records the person's one-time approval to run it.

Called by the WINDOW (tools.invoke), never by a model turn — a call from a turn is refused, so the
agent cannot approve its own run. The window then sends the chat the exact call to make,
`pipeline_run {"stage": …, "approval": <token>}`, and pipeline_run admits it (StageRunApprovals).

`session` is the chat's key, as the window knows it: a click runs outside any chat, so the chat is
named, as Ad Studio's window names its campaign.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context, current_workspace

import chat_paths
from stage_run_approvals import StageRunApprovals


def _from_window() -> bool:
    ctx = current_run_context()
    return bool(getattr(ctx, "direct_invoke", False)) if ctx else False


class StageRunApproveTool(Tool):
    name = "stage_run_approve"
    label = "Approve running a stage"
    default_retryable = False
    description = (
        "WINDOW ONLY — the Run button on a stage in the Stages panel. Refused from a conversation: "
        "a stage runs when the person presses Run on it, and the window then sends you "
        "`pipeline_run` with the approval to use."
    )
    parameters = {
        "type": "object",
        "required": ["session", "stage"],
        "properties": {
            "session": {"type": "string", "description": "The chat's key."},
            "stage": {"type": "string", "description": "The stage to run."},
        },
    }

    def __init__(self, approvals: Callable[[], StageRunApprovals] | None = None) -> None:
        self._approvals = approvals or (lambda: StageRunApprovals(Path(current_workspace(".") or "."),
                                                                 lambda: secrets.token_hex(8)))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        if not _from_window():
            return ToolResult.text("stage_run_approve is the Run button in the Stages panel — only the person "
                                   "approves a run. Tell them which stage is ready to run there.", is_error=True)
        session = str(params.get("session") or "").strip()
        stage = str(params.get("stage") or "").strip()
        try:
            token = self._approvals().approve(chat_paths.chat_folder(session), stage)
        except ValueError as e:
            return ToolResult.text(str(e), is_error=True)
        return ToolResult.text(f"stage {stage} approved to run once", details={"token": token})


__all__ = ["StageRunApproveTool"]
