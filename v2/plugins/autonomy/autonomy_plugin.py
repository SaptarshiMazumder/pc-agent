"""Built-in 'autonomy' bundle — heartbeat_respond + report_outcome (always when autonomy is on),
and cron + goal + commitment (when a durable task ledger is wired).

Gating ported verbatim from the old build_tools: autonomy OFF => nothing registers, so interactive
chat is exactly as before. report_outcome/heartbeat_respond are exposed only on the right run mode
by domain.agent.apply_mode (keyed on tool name — unchanged by the move). The ledger arrives via DI
(``ctx.task_store``).
"""

from __future__ import annotations


def register(api, ctx):
    if not getattr(ctx.config, "autonomy_enabled", False):
        return
    from heartbeat_tool import HeartbeatRespondTool
    from outcome_tool import ReportOutcomeTool

    api.register_tool(HeartbeatRespondTool())
    api.register_tool(ReportOutcomeTool())     # exposed ONLY on a cron run (apply_mode)
    if ctx.task_store is not None:
        from commitment_tool import CommitmentTool
        from cron_tool import CronTool
        from goal_tool import GoalTool

        api.register_tool(CronTool(ctx.task_store))
        api.register_tool(GoalTool(ctx.task_store))   # SqliteTaskStore implements GoalStore too
        api.register_tool(CommitmentTool(ctx.task_store))
