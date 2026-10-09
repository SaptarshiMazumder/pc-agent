"""pipeline_status — where this chat's job is: each stage, its model, what it has made, what is next."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_workspace

from pipeline_run_record import PipelineRunRecord
from pipeline_tool_context import PipelineToolContext


class PipelineStatusTool(Tool):
    name = "pipeline_status"
    label = "Where the job is"
    default_retryable = True
    description = "This chat's pipeline: each stage, its model, whether it has run and what it made, and what runs next."
    parameters = {"type": "object", "properties": {}}

    def __init__(self, context: Callable[[], PipelineToolContext] | None = None) -> None:
        self._context = context or (lambda: PipelineToolContext.for_workspace(Path(current_workspace(".") or ".")))

    async def execute(self, tool_call_id, params, abort, on_update=None):
        try:
            ctx = self._context()
            pipeline = ctx.store.load()
            if pipeline is None:
                return ToolResult.text("no pipeline in this chat yet.")
            record = PipelineRunRecord(ctx.workspace)
            lines = [f"{pipeline.name}:"]
            for s in pipeline.stages:
                model = ("Seedream 5 Pro (provider)" if s.seedream
                         else f"{s.family}/{s.recipe}" if not s.custom else "custom graph")
                rec = record.stage(s.name)
                status = rec.get("status") or "not run"
                made = ", ".join(f for fs in (rec.get("outputs") or {}).values() for f in fs)
                ins = ", ".join(f"{i.name}<-{i.source}" for i in s.inputs)
                lines.append(f"  {s.name} [{model}] {status}" + (f" — made {made}" if made else "")
                             + (f" — reads {ins}" if ins else "") + (" — review point" if s.review else ""))
            nxt = record.next_to_run(pipeline)
            lines.append(f"next: {nxt}" if nxt else "every stage has run")
            return ToolResult.text("\n".join(lines))
        except Exception as e:  # noqa: BLE001
            return ToolResult.text(f"pipeline_status failed: {type(e).__name__}: {e}", is_error=True)


__all__ = ["PipelineStatusTool"]
