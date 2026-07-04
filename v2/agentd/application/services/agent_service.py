"""AgentService — the use-case for handling one inbound user message.

This is the APPLICATION layer: pure orchestration, no IO libraries. It coordinates
the steps of a turn by calling interfaces only — everything concrete (the engine,
the tools, how to make a session store, how to build the system prompt) is INJECTED
in the constructor. So this class imports nothing from infrastructure and never
changes when you swap the engine, the memory backend, or the model.

The conductor analogy: this class decides "load history, append the user message,
build the prompt, run the engine, persist" — the *order of the work* — but plays no
instrument itself (the engine streams the LLM, the session store hits the disk).
"""

from __future__ import annotations

from typing import Callable

from agentd.application.interfaces.agent_engine import AgentEngine
from agentd.application.interfaces.agents import AgentRegistry
from agentd.application.interfaces.events import EventSink
from agentd.application.interfaces.memory import SessionStore
from agentd.application.run_context import RunContext, current_run_outcome, set_run_context
from agentd.domain.agent import AgentSpec, RunMode, apply_mode, select_tools
from agentd.domain.messages import UserMessage


def tool_source(t) -> str:
    """Where a tool came from: ``plugin:<id>`` (tagged at load), ``mcp:<server>`` (namespaced
    name), else ``internal`` (build_tools). The GuardedTool wrapper carries the resolved value in
    ``.source`` (set in the composition root); this derives it when that's absent."""
    src = getattr(t, "source", None)
    if src:
        return src
    pid = getattr(t, "_plugin_id", None)
    if pid:
        return f"plugin:{pid}"
    name = getattr(t, "name", "") or ""
    if "__" in name:
        return "mcp:" + name.split("__", 1)[0]
    return "internal"


def _tool_info(t) -> dict:
    """A client-renderable summary of one tool (duck-typed; works on a GuardedTool too):
    name + label + summary (the tool's ``description`` first line) + concurrency + accurate
    ``source`` (internal / plugin:<id> / mcp:<server>)."""
    desc = (getattr(t, "description", "") or "").strip()
    return {
        "name": getattr(t, "name", "") or "",
        "label": getattr(t, "label", "") or "",
        "summary": desc.splitlines()[0].strip() if desc else "",
        "concurrency": getattr(t, "concurrency", "parallel"),
        "source": tool_source(t),
    }


class AgentService:
    def __init__(
        self,
        *,
        engine: AgentEngine,
        tools: list,
        registry: AgentRegistry,                            # which agent handles a session
        make_session: Callable[[str, AgentSpec], SessionStore],  # (id, agent) -> store
        build_prompt: Callable[..., str],  # (tools, agent, mode, query="") -> prompt
        recall: Callable[[AgentSpec, str], str] | None = None,   # (agent, query) -> memory block, or ""
        plugin_reloader: Callable[[], dict] | None = None,  # hot-load NEW plugins into the live
        # catalog (marketplace installs / create_tool). Filled by the composition root — the
        # service only knows "something can extend my toolset live", never how discovery works.
    ):
        self._engine = engine
        self._tools = tools
        self._registry = registry
        self._make_session = make_session
        self._build_prompt = build_prompt
        self._recall = recall               # auto-recall: prepends relevant memories on user turns
        self.plugin_reloader = plugin_reloader

    def _resolve_agent(self, session_id: str, agent_id: str | None):
        """Explicit agent_id wins (a client naming the agent); else resolve from the
        session key. An unknown explicit id falls back to the default agent."""
        if agent_id:
            try:
                return self._registry.get(agent_id)
            except KeyError:
                pass
        return self._registry.resolve(session_id)

    def add_tools(self, tools: list) -> None:
        """Register more tools after construction (e.g. MCP tools discovered async
        at gateway startup). They join the full toolset; each turn is then scoped to
        the resolved agent's allow/deny."""
        self._tools.extend(tools)

    def find_tool(self, name: str):
        """Look up a registered tool by name (e.g. a namespaced MCP tool a channel
        invokes outside the agent loop). Returns the Tool or None."""
        return next((t for t in self._tools if getattr(t, "name", None) == name), None)

    def remove_tools(self, prefix: str) -> int:
        """Drop every tool whose name starts with ``prefix`` from the live catalog (e.g.
        ``notion__`` when an MCP server is removed). Returns how many were dropped."""
        before = len(self._tools)
        self._tools = [t for t in self._tools if not getattr(t, "name", "").startswith(prefix)]
        return before - len(self._tools)

    def list_tools(self, agent_id: str | None = None) -> list:
        """Enumerate the live tool catalog (read-only; safe to call any time).

        No ``agent_id`` => the FULL active catalog (every tool currently loaded + enabled).
        With ``agent_id`` => the subset THAT agent actually sees in an interactive turn — i.e.
        ``apply_mode(select_tools(...), INTERACTIVE)``, exactly what ``handle_message`` would
        pass the model. An unknown id falls back to the full catalog."""
        tools = self._tools
        if agent_id:
            try:
                agent = self._registry.get(agent_id)
                tools = apply_mode(select_tools(self._tools, agent), RunMode.INTERACTIVE)
            except KeyError:
                pass
        return [_tool_info(t) for t in tools]

    async def handle_message(self, session_id: str, text: str,
                             on_event: EventSink, abort,
                             mode: str = RunMode.INTERACTIVE,
                             agent_id: str | None = None) -> None:
        """Run one turn end to end for the resolved agent.

        ``mode`` is the run mode (interactive | heartbeat | cron). ``agent_id`` is an
        EXPLICIT agent selection from a client (it wins); when absent, the agent is
        resolved from the session key (autonomy uses ``agent:<id>:heartbeat``). An
        unknown override falls back to the default agent. Defaults keep the reactive
        path unchanged.
        """
        agent = self._resolve_agent(session_id, agent_id)  # explicit override or session key
        # expose the run context to context-aware tools (e.g. cron tags its task with
        # this agent). Task-local, so concurrent runs never cross.
        set_run_context(RunContext(agent_id=agent.id, session_key=session_id, mode=mode,
                                   workspace=str(agent.workspace),
                                   plugins=getattr(agent, "plugins", None) or None))
        tools = apply_mode(select_tools(self._tools, agent), mode)  # agent scope + run-mode scope
        session = self._make_session(session_id, agent)   # per-agent session store
        messages = session.load()                         # prior history (read)
        user_msg = UserMessage(content=text)
        messages.append(user_msg)                         # add the new user turn to context
        session.append(user_msg)                          # persist it
        system_prompt = self._build_prompt(tools, agent, mode, text)  # identity + bootstrap + tools
        # Auto-recall (OpenClaw's before_prompt_build): on a USER turn only, silently retrieve
        # relevant long-term memories and prepend them — the agent doesn't call a tool. Gated to
        # INTERACTIVE so heartbeat/cron runs don't burn embeddings; fail-open so a slow/broken
        # embedder never blocks the turn.
        if self._recall is not None and mode == RunMode.INTERACTIVE:
            try:
                block = self._recall(agent, text)
            except Exception:  # noqa: BLE001 — recall is an enhancement, never a hard dependency
                block = ""
            if block:
                system_prompt = block + "\n\n" + system_prompt

        # hand off to the engine; it streams the LLM, runs tools, and re-feeds until done.
        # (it persists each assistant/tool message via the `session` it's given.)
        await self._engine.run(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            on_event=on_event,
            abort=abort,
            session=session,
            model=agent.model,        # per-agent override (None = the engine default)
        )
        # RUN seam: a scheduled run MUST record an outcome. If the agent finished WITHOUT
        # calling report_outcome (common: it did the work but skipped the bookkeeping), force
        # ONE follow-up turn to make it declare — so a successful run isn't mislabeled
        # 'incomplete'. Fires at most once; if it still won't declare, the gateway marks it.
        if (mode == RunMode.CRON and not abort.is_set()
                and current_run_outcome() is None):
            nudge = UserMessage(content=(
                "You are a SCHEDULED run and finished WITHOUT recording the outcome. Call "
                "`report_outcome` now, exactly once: status='done' if you completed the task, "
                "'blocked' if you could not proceed (put the blocker in `detail`), or 'failed' "
                "if it errored. Do this now — it is the only way the user learns the result."))
            messages.append(nudge)
            session.append(nudge)
            await self._engine.run(
                messages=messages, system_prompt=system_prompt, tools=tools,
                on_event=on_event, abort=abort, session=session, model=agent.model)
