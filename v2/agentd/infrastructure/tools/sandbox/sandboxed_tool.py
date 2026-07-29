"""SandboxedTool — the wrapper that routes an untrusted tool's execute() through a PluginSandbox.

It IS a ``Tool`` (transparent: name/description/parameters/label/concurrency and every provenance
tag forward to the inner tool), so the engine, the guard middleware, the catalog, and the model see
no difference — an agent-private plugin tool is advertised and called exactly like a built-in. The
ONLY change is where the work runs: instead of calling ``inner.execute`` in-process, it resolves a
``CapabilityGrant`` for this run and hands ``(plugin_id, tool_name, params, grant)`` to the sandbox.

Grant resolution happens per-execute (not at construction) so the future approval layer can make it
dynamic/interactive without any change here — SandboxedTool just asks the resolver each call.
"""

from __future__ import annotations

from agentd.application.interfaces.plugin_sandbox import CapabilityResolver, PluginSandbox
from agentd.application.interfaces.tool import OnUpdate, Tool, ToolResult
from agentd.application.run_context import current_run_context
from agentd.domain.sandbox import DENY_ALL, PluginOrigin


class SandboxedTool(Tool):
    def __init__(
        self,
        inner: Tool,
        sandbox: PluginSandbox,
        resolver: CapabilityResolver,
        *,
        plugin_id: str,
        origin: PluginOrigin,
    ) -> None:
        self._inner = inner
        self._sandbox = sandbox
        self._resolver = resolver
        self._plugin_id = plugin_id or getattr(inner, "_plugin_id", "") or "unknown"
        self._origin = origin
        self._tool_name = getattr(inner, "name", "") or ""
        # Let an IN-PROCESS backend find the inner tool by (plugin_id, tool_name). A real
        # (container/microVM/remote) backend has no `register` and loads the code itself — the
        # getattr keeps the port clean of this local-only concern.
        register = getattr(sandbox, "register", None)
        if callable(register):
            register(self._plugin_id, self._tool_name, inner)

    # --- transparent Tool contract: forward everything to the inner tool ------
    @property
    def name(self) -> str:
        return self._inner.name

    @property
    def description(self) -> str:
        return self._inner.description

    @property
    def parameters(self) -> dict:
        return self._inner.parameters

    @property
    def label(self) -> str:
        return getattr(self._inner, "label", "") or self._inner.name

    @property
    def concurrency(self) -> str:
        return getattr(self._inner, "concurrency", "parallel")

    def __getattr__(self, item):
        # Anything not defined here (model metadata, provenance tags like _plugin_id/_agent_id,
        # default_timeout_sec, resolve_model, ...) delegates to the wrapped tool, so the catalog and
        # policy resolver see the real tool. __getattr__ only fires for genuinely missing attrs.
        return getattr(self.__dict__["_inner"], item)

    # --- the sandboxed execution ---------------------------------------------
    async def execute(
        self,
        tool_call_id: str,
        params: dict,
        abort,
        on_update: OnUpdate | None = None,
    ) -> ToolResult:
        ctx = current_run_context()
        try:
            grant = self._resolver.resolve(self._plugin_id, self._origin, ctx)
        except Exception:  # noqa: BLE001 — a resolver bug must never widen access
            grant = DENY_ALL
        return await self._sandbox.run_tool(
            self._plugin_id,
            self._tool_name,
            tool_call_id,
            params,
            abort,
            on_update,
            grant=grant,
            ctx=ctx,
        )
