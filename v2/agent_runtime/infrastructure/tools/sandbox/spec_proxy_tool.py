"""SpecProxyTool — an untrusted plugin tool ADVERTISED from a spec, never from an import.

Importing a module executes its module-level code, so enumerating an untrusted plugin's tools by
importing it runs a stranger's code inside the daemon — precisely what the sandbox exists to
prevent, one step earlier than tool execution. The fix: the SANDBOX enumerates (the worker's
`enumerate` job, in a subprocess on desktop or a microVM on hosted) and answers plain tool SPECS;
this class is the daemon-side stand-in built from one.

It carries everything a tool needs to be advertised and governed — name/description/parameters
for the model, label/concurrency for the engine, model metadata for the catalog and the
capability resolver, and the provenance stamps discovery adds — but no behaviour: its own
`execute` fails closed, because a spec proxy must ONLY ever run wrapped in a SandboxedTool,
whose backend loads the real code on the far side of the boundary.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.tool import Tool, ToolResult


class SpecProxyTool(Tool):
    def __init__(self, spec: dict):
        self.name = str(spec.get("name") or "")
        self.description = str(spec.get("description") or "")
        self.parameters = spec.get("parameters") or {}
        self.label = str(spec.get("label") or "") or self.name
        self.concurrency = str(spec.get("concurrency") or "parallel")
        self.plugin = str(spec.get("plugin") or "")
        self.needs_model = bool(spec.get("needs_model"))
        self.model_kind = str(spec.get("model_kind") or "text")
        self.default_model = str(spec.get("default_model") or "")
        self.default_timeout_sec = spec.get("default_timeout_sec")
        self.default_retryable = bool(spec.get("default_retryable"))

    async def execute(self, tool_call_id, params, abort, on_update=None) -> ToolResult:
        # Reached only if wiring ever hands this out UNWRAPPED — which must fail closed, loudly:
        # running would mean importing untrusted code in-process, the exact thing specs prevent.
        return ToolResult.text(
            f"'{self.name}' is an untrusted tool advertised from a spec and can only run inside "
            "the plugin sandbox — it reached the engine unwrapped, which is a wiring bug.",
            is_error=True,
        )
