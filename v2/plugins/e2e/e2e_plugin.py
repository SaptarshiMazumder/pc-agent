"""Composition root for the e2e plugin — three tools over the `agent_runtime.e2e` engine.

The ENGINE (scenario/trace/signals/checks/report/live_driver) lives in `agent_runtime.e2e` so it
ships in the wheel and stays importable from a checkout (`python -m agent_runtime.e2e.runner`).
This plugin is presentation only: it hands the same engine to the Agent Builder as tools, driving
runs through the in-process gateway client (`ctx.gateway_client`) instead of a socket — the
transport that works identically on desktop and hosted daemons.

Shared-tier on purpose: shipping in `plugins/` puts it in every wheel, but a shared tool reaches
an agent only if its agent.toml `[tools] allow` names it — today that is agent-builder and
cloud-agent-builder. The engine's README and the build-agent skill's `reference/testing.md`
carry the procedure; the tools carry the mechanics.
"""

from __future__ import annotations


def register(api, ctx):
    from e2e_checks_tool import E2eChecksTool
    from e2e_replay_tool import E2eReplayTool
    from e2e_run_tool import E2eRunTool

    api.register_tool(E2eRunTool(ctx))
    api.register_tool(E2eReplayTool(ctx))
    api.register_tool(E2eChecksTool())
