"""demo-kit — the reference AGENT-PRIVATE plugin (ships inside agents/app-demo/).

One deliberately tiny tool, `demo_stamp`: it echoes a message back stamped with WHICH agent ran
it (from the run context) — so the demo UI can prove, in one click, that (a) a tool that lives
inside the agent's own folder is invokable like any other, and (b) it executes AS that agent.
No models, no deps, no side effects: the point is the plumbing, not the tool.
"""

from __future__ import annotations

from agentd.application.interfaces.tool import Tool, ToolResult
from agentd.application.run_context import current_run_context


class DemoStampTool(Tool):
    name = "demo_stamp"
    plugin = "demo-kit"
    description = (
        "Stamp a message with the identity of the agent running it. A PRIVATE tool shipped "
        "inside the app-demo agent — only app-demo can see or call it. Params: `message` "
        "(optional text to stamp)."
    )
    label = "Demo Stamp"
    concurrency = "parallel"
    parameters = {
        "type": "object",
        "properties": {
            "message": {"type": "string", "description": "Text to stamp (optional)."},
        },
    }

    def __init__(self, config):
        self.config = config

    async def execute(self, tool_call_id, params, abort, on_update=None):
        ctx = current_run_context()
        who = ctx.agent_id if ctx is not None else "(no run context)"
        message = str(params.get("message") or "hello from my private tool")
        return ToolResult.text(
            f"[demo-kit] '{message}' — stamped by agent '{who}'. This tool lives inside "
            f"agents/app-demo/plugins/demo-kit/ and travels with the agent."
        )


def register(api, ctx):
    api.register_tool(DemoStampTool(ctx.config))
