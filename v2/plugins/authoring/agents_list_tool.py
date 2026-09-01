"""agents_list — discover the OTHER agents this one can hand work to.

Read-only roster for DELEGATION: an orchestrator calls this to see the available specialists
(id + name + one-line description), then runs the right one with
``spawn_subagent(agent="<id>", task=...)`` (several at once → they run in parallel). Excludes
the caller itself, and — when the caller has a ``[subagents] allow`` scope — shows only the
specialists it is allowed to delegate to (the same allowlist the spawn path enforces)."""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult
from agent_runtime.application.run_context import current_run_context


class AgentsListTool(Tool):
    name = "agents_list"
    label = "Agents"
    default_retryable = True
    description = (
        "List the OTHER agents you can hand work to — their id, name, and what each is for — so "
        "you delegate to the right specialist. After picking one, run it with "
        'spawn_subagent(agent="<id>", task=...); you may spawn several at once and they run in '
        "parallel. Excludes yourself; if you have a scoped specialist allowlist, only those show."
    )
    parameters = {"type": "object", "properties": {}}

    def __init__(self, registry):
        self._registry = registry

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.domain.agent import _matches

        ctx = current_run_context()
        me = (ctx.agent_id if ctx else "") or "main"
        try:
            allow = getattr(self._registry.get(me), "subagents_allow", None)
        except KeyError:
            allow = None
        rows = []
        for aid in self._registry.list_ids():
            if aid == me:
                continue
            if allow is not None and not any(_matches(aid, p) for p in allow):
                continue
            spec = self._registry.get(aid)
            line = f"- {aid}"
            if spec.name and spec.name != aid:
                line += f" ({spec.name})"
            desc = (getattr(spec, "description", "") or "").strip()
            if desc:
                line += f": {desc}"
            rows.append(line)
        if not rows:
            return ToolResult.text("No other agents available to delegate to.")
        return ToolResult.text("Agents you can delegate to:\n" + "\n".join(rows))
