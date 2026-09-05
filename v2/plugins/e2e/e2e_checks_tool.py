"""e2e_checks — the authorable check vocabulary, straight from the registry.

Scenarios are declarative; a check is `{"name": ..., ...args}` resolved against the registry in
`agent_runtime.e2e.checks`. A name that is not in the registry fails at RUN time as "unknown
check" — after the model spend. This tool exists so scenario authoring starts from the real
list, and an invented check name never reaches a run.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class E2eChecksTool(Tool):
    name = "e2e_checks"
    label = "E2E Check Vocabulary"
    default_retryable = True  # pure read
    description = (
        "LIST THE E2E CHECK VOCABULARY — every check name a scenario may use, its args, and what "
        "it asserts. Call this BEFORE authoring a scenario's `checks`; never invent a check "
        "name. Derive checks from what the agent CLAIMS: each capability → tool_succeeded / "
        "produced_artifact; each must-not (don't stall, don't punt, don't re-ask) → the "
        "matching no_* check; promised ordering → call_order. And encode the bug you are about "
        "to fix as a check that is RED today — the red→green flip is the proof the fix worked."
    )
    parameters = {"type": "object", "properties": {}}

    async def execute(self, tool_call_id, params, abort, on_update=None):
        from agent_runtime.e2e.checks import vocabulary

        lines = ["Check vocabulary (scenario `checks` entries are {\"name\": ..., ...args}):", ""]
        for c in vocabulary():
            args = ", ".join(f"{k}: {v}" for k, v in c["args"].items()) or "no args"
            lines.append(f"  {c['name']}  ({args})")
            lines.append(f"      {c['asserts']}")
        return ToolResult.text("\n".join(lines))
