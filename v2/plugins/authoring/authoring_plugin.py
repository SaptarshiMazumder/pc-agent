"""Built-in 'authoring' bundle — the by-chat capability CREATORS.

Home of the meta-tools that let an agent extend the system by chatting, each gated by its own
config flag (parity with skill_workshop, which is the skill creator and lives in the `skills`
bundle):

  - agents_list   -> read-only roster of OTHER agents, for delegation. Needs the AgentRegistry;
                     ungated (listing is harmless and useful whenever >1 agent exists).
  - create_agent  -> author a new agent DEFINITION (agents/<id>/) and register it LIVE.
                     Needs the AgentRegistry (DI) and `agent_workshop`.
  - create_tool   -> author a NEW native tool by chatting + hot-load it (B1 reload seam).
                     Needs `register_plugin_live` (DI) and `tool_workshop`. Writes+runs new code.

(add_mcp + message_agent are gateway-level tools — they live where MCP/agent runs are driven.)

Each tool is registered ONLY when the handle it needs is present (and, for the authoring
creators, when their flag is on), so an off/unwired creator is simply absent."""

from __future__ import annotations


def register(api, ctx):
    if getattr(ctx, "registry", None) is None:
        return  # the roster/agent creators need the registry
    from agents_list_tool import AgentsListTool

    api.register_tool(AgentsListTool(ctx.registry))  # read-only roster — always available
    if getattr(ctx.config, "agent_workshop", False):
        from create_agent_tool import CreateAgentTool

        api.register_tool(CreateAgentTool(ctx.registry))
    if getattr(ctx.config, "tool_workshop", False) and getattr(ctx, "register_plugin_live", None):
        from create_tool_tool import CreateToolTool

        api.register_tool(CreateToolTool(ctx.config, ctx.register_plugin_live))
