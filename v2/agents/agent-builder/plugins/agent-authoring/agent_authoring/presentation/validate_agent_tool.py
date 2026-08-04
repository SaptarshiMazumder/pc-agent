"""ValidateAgentTool — the `validate_agent` tool surface.

A thin adapter: params in, service call, ToolResult out. No rules, no I/O, no formatting (the
Report renders itself). Read-only and idempotent, hence retryable.

``is_error`` is set when the report has ERRORs, so the model treats a failed validation the way
it treats any failing tool: read the reason, fix, call again.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class ValidateAgentTool(Tool):
    name = "validate_agent"
    label = "Validate Agent"
    default_retryable = True  # read-only; safe to repeat
    description = (
        "Check an authored AGENT before relying on it. Verifies three things the daemon itself "
        "will not tell you: (1) LAYOUT — is agent.toml built correctly, and is anything you wrote "
        "being silently ignored (e.g. display keys nested inside [app]); (2) PACKAGEABILITY — can "
        "it be built into an installer, is `version` set, does the [app] entry file exist, is "
        "anything load-bearing hidden in a directory the packer excludes; (3) SANDBOX — the "
        "agent's own plugins/ tools are classified untrusted, so flag any that reach for API keys "
        "or the network. Call it after authoring an agent, and again after every fix."
    )
    parameters = {
        "type": "object",
        "required": ["agent_id"],
        "properties": {
            "agent_id": {"type": "string", "description": "the agent to validate (e.g. my-agent)"}
        },
    }

    def __init__(self, service):
        self._service = service

    async def execute(self, tool_call_id, params, abort, on_update=None):
        report = self._service.validate(params.get("agent_id", ""))
        return ToolResult.text(
            report.as_text(),
            is_error=not report.ok,
            details={
                "agentId": report.agent_id,
                "ok": report.ok,
                "counts": report.counts(),
                "codes": [f.code for f in report.findings],
            },
        )
