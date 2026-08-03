"""agent-builder — the bundle's COMPOSITION ROOT.

The only place in this bundle that wires anything: it reads the handles off PluginContext, builds
the rules -> services -> adapters -> tools chain, and registers the tools. Every class below is
constructor-injected, so each is unit-testable without a daemon.

Layout note: the layered code lives under the ``agent_builder/`` package rather than bare
``domain/`` + ``application/`` folders, because the loader does ``sys.path.insert(0, <plugin dir>)``
for EVERY plugin — generic top-level package names would collide between bundles. One uniquely
named package keeps the layering visible and the namespace safe.

Both tools are ungated: they only READ agent directories and REFRESH what is already on disk.
Nothing here authors agent code — that is create_agent / create_tool / write, each gated
separately by its own workshop flag.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def register(api, ctx):
    registry = getattr(ctx, "registry", None)
    if registry is None:
        return  # both tools are meaningless without the agent roster

    from agent_builder.application.reload_agent_service import ReloadAgentService
    from agent_builder.application.validate_agent_service import ValidateAgentService
    from agent_builder.domain.agent_layout_rules import AgentLayoutRules
    from agent_builder.domain.packageability_rules import PackageabilityRules
    from agent_builder.domain.sandbox_rules import SandboxRules
    from agent_builder.infrastructure.agent_dir_reader import AgentDirReader
    from agent_builder.infrastructure.registry_reload_adapter import RegistryReloadAdapter
    from agent_builder.presentation.reload_agent_tool import ReloadAgentTool
    from agent_builder.presentation.validate_agent_tool import ValidateAgentTool

    reader = AgentDirReader(registry)
    api.register_tool(
        ValidateAgentTool(
            ValidateAgentService(
                reader,
                AgentLayoutRules(),
                PackageabilityRules(),
                SandboxRules(),
            )
        )
    )

    # register_plugin_live picks up NEW agents/<id>/plugins/; broadcast_agents_changed refreshes
    # every client's sidebar. Both are OPTIONAL — reload still does what it can without them,
    # and reports honestly which steps it managed.
    reloader = RegistryReloadAdapter(
        registry,
        getattr(ctx, "register_plugin_live", None),
        getattr(ctx, "broadcast_agents_changed", None),
    )
    api.register_tool(ReloadAgentTool(ReloadAgentService(reloader)))

    if getattr(ctx, "broadcast_agents_changed", None) is None:
        log.info("agent-builder: no broadcast handle — reload_agent will not refresh clients")
