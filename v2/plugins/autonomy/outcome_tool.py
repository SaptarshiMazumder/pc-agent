"""report_outcome — a scheduled run declares whether it actually worked.

Mode-scoped: exposed ONLY on a `cron` run (see domain.agent.apply_mode), so
interactive chat never sees it. The agent calls it once at the end of a scheduled
job to say done / blocked / failed (+ a one-line reason). The gateway reads the
declaration when the run finishes and records it in the run-history ledger, so
`/cron history` shows *did it actually work*, not just *did the run not crash*.

The outcome is handed back via the run-outcome contextvar (set_run_outcome) — the
same task-local channel pattern as RunContext, so concurrent runs never cross.
"""

from __future__ import annotations

from agentd.application.run_context import set_run_outcome

from agentd.application.interfaces.tool import Tool, ToolResult

_STATUSES = ("done", "blocked", "failed")


class ReportOutcomeTool(Tool):
    name = "report_outcome"
    label = "Outcome"
    description = (
        "Record the result of THIS scheduled run. Call exactly once, at the very end. "
        "status='done' if you completed the task; 'blocked' if you could not proceed "
        "without the user (e.g. missing authorization, credentials, or input) — put the "
        "exact blocker in `detail`; 'failed' if it errored out. This is the ONLY way the "
        "user learns whether the scheduled job worked, so always call it."
    )
    parameters = {
        "type": "object",
        "required": ["status"],
        "properties": {
            "status": {"type": "string", "enum": list(_STATUSES),
                       "description": "done | blocked | failed"},
            "detail": {"type": "string",
                       "description": "One line: what you did, or the exact blocker/error."},
        },
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        status = (params.get("status") or "").strip().lower()
        detail = (params.get("detail") or "").strip()
        if status not in _STATUSES:
            return ToolResult.text(
                f"status must be one of {', '.join(_STATUSES)}", is_error=True)
        set_run_outcome(status, detail)
        text = f"outcome recorded: {status}" + (f" — {detail}" if detail else "")
        return ToolResult.text(text, details={"status": status, "detail": detail})
