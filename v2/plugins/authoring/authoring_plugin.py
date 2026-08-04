"""Built-in 'authoring' bundle — the read-only agent ROSTER.

  - agents_list -> who else exists, for delegation. Needs the AgentRegistry; ungated (listing is
                   harmless and useful whenever more than one agent exists).

The CREATORS that used to live here — create_agent and create_tool — moved into the agent that
uses them: agents/agent-builder/plugins/agent-authoring/. They were shared tools gated by config
flags (agent_workshop / tool_workshop), which meant every agent could reach them unless an
allow/deny list said otherwise, and a fresh install had them switched off. As one agent's PRIVATE
tools they are visible only to agent-builder — a structural boundary instead of a maintained
blocklist — so the flags became redundant and were dropped.

(add_mcp + message_agent are gateway-level tools — they live where MCP/agent runs are driven.)
"""

from __future__ import annotations


def register(api, ctx):
    if getattr(ctx, "registry", None) is None:
        return  # the roster needs the registry
    from agents_list_tool import AgentsListTool

    api.register_tool(AgentsListTool(ctx.registry))  # read-only roster — always available
