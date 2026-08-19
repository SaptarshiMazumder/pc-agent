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

import logging
from collections.abc import Callable

from agent_runtime.application.interfaces.agent_engine import AgentEngine
from agent_runtime.application.interfaces.agents import AgentRegistry
from agent_runtime.application.interfaces.events import EventSink
from agent_runtime.application.interfaces.memory import SessionStore
from agent_runtime.application.run_context import (
    RunContext,
    current_run_outcome,
    current_trace_ids,
    set_run_context,
)
from agent_runtime.domain import ownership as _ownership
from agent_runtime.domain.agent import (
    MCP_WORKSHOP_TOOL,
    AgentSpec,
    RunMode,
    apply_mode,
    capability_enabled,
    select_private_tools,
    select_tools,
)
from agent_runtime.domain.messages import Artifact, UserMessage

log = logging.getLogger("agentd")


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
        registry: AgentRegistry,  # which agent handles a session
        make_session: Callable[[str, AgentSpec], SessionStore],  # (id, agent) -> store
        build_prompt: Callable[..., str],  # (tools, agent, mode, query="") -> prompt
        recall: Callable[[AgentSpec, str], str]
        | None = None,  # (agent, query) -> memory block, or ""
        plugin_reloader: Callable[[], dict] | None = None,  # hot-load NEW plugins into the live
        # catalog (marketplace installs / create_tool). Filled by the composition root — the
        # service only knows "something can extend my toolset live", never how discovery works.
        resolve_workspace: Callable[[AgentSpec, str], str] | None = None,  # (agent, session_id) ->
        # the EFFECTIVE working dir for this run's file/exec tools. Injected by the composition
        # root so a PROJECT chat binds to the project's shared workspace (plan §11: file ownership
        # follows context, not identity). None => the agent's own workspace, exactly as before.
        on_catalog_change: Callable[[list], None] | None = None,  # (all tools) -> None, called
        # whenever the toolset changes. The ONE hook: MCP servers connect after startup and
        # create_tool hot-adds without a restart, and every one of those paths lands in
        # add_tools below — so hooking here covers them all instead of five call sites.
        installed_agents: Callable[[], frozenset | None] | None = None,  # () -> the ids that
        # arrived in a .agentpkg, i.e. the marketplace ledger's roster. Their directories become
        # PROTECTED: an agent may author agents, but not edit someone else's installed one.
        # Injected rather than read here because the ledger is the composition root's to own.
        # `None` from the callable means the ledger could not be READ, which is different from
        # "nothing installed" and is handled by failing closed.
        resolve_models: Callable[[AgentSpec], tuple] | None = None,  # (agent) -> (model, router)
        # for THIS run, after the agent's own config has been layered over the daemon's. An
        # injected callable rather than a Config handle, matching build_prompt / recall /
        # resolve_workspace above: the service only knows "something decides which brain runs",
        # never how the layering works. None => the agent.toml model and the engine's own
        # router, exactly as before.
        mention_routing: str = "direct",  # what a user @mention of ANOTHER agent does: "direct"
        # (the mentioned agent answers the turn AS ITSELF, one-off) | "delegate" (this agent
        # orchestrates it via message_agent). Injected from Config; single unambiguous mentions
        # honor it, 2+ mentions always delegate. See handle_message's reroute.
        agent_tools: dict | None = None,  # AGENT-PRIVATE tools shipped INSIDE an agent's own
        # folder (agents/<id>/plugins/): {agent_dir_key(dir): [Tool]}. Keyed by LOCATION, not
        # id: two accounts can each hold an agent of the same id on a hosted daemon, and an
        # id-keyed map let the later layer's tools answer for both (a cross-tenant hole).
        # Offered ONLY to the agent resolved at that directory — implicitly allowed for it
        # (deny still wins), invisible to every other agent and to the global catalog.
        # Composition root discovers + wraps them (discover_agent_plugins).
        resolve_scope: Callable[[AgentSpec, str], tuple] | None = None,  # (agent, workspace) ->
        # (read_roots, write_clamp): the TENANT scope for this run — what it may see and the
        # outer boundary of its writes. Injected like resolve_workspace above: the composition
        # root knows who is signed in and what the deployment is; the service only knows "a
        # run carries a scope". None / ((), ()) => unrestricted, the desktop degenerate case.
        mcp_workshop_default: bool = False,  # the daemon-wide `mcp_workshop` flag. An agent's own
        # [capabilities] mcp_workshop overrides it either way — see capability_enabled.
        mcp_connector=None,  # AgentMcpConnector — brings up the servers an agent DECLARES in its
        # agent.toml [[mcp]], lazily, on its first run. Kept OUT of `agent_tools` because that map
        # is rebuilt wholesale on every marketplace hot-reload, which would drop these silently.
        # None => no declared-MCP support (tests, and any composition that does not want it).
    ):
        self._engine = engine
        self._tools = tools
        self._agent_tools = dict(agent_tools or {})
        self._mcp = mcp_connector
        self._mcp_workshop_default = bool(mcp_workshop_default)
        self._registry = registry
        self._make_session = make_session
        self._build_prompt = build_prompt
        self._recall = recall  # auto-recall: prepends relevant memories on user turns
        self.plugin_reloader = plugin_reloader
        self._resolve_workspace = resolve_workspace
        self._resolve_models = resolve_models
        self._installed_agents = installed_agents
        self._on_catalog_change = on_catalog_change
        self._mention_routing = (mention_routing or "direct").strip().lower()
        self._resolve_scope = resolve_scope

    def _agent_roots(self, agent) -> tuple:
        """Every root where the CALLER's agents may live — asked of the REGISTRY, the one
        authority on agent placement (the shared catalogue plus the connection's account
        overlay). Resolved per run, because expansion happens at RunContext creation inside
        the connection's context — so a signed-in caller's roots include their overlay
        automatically, on desktop and hosted alike.

        Deriving this from the calling agent's own parent directory — the old rule — went
        stale the day overlays arrived: the agent-builder sits in the SHARED catalogue, so a
        signed-in user's create_agent wrote to the overlay (correct, the registry's answer)
        and was refused by a scope that only named the shared dir (stale, the layout's
        answer). One authority now; a registry without the method (minimal test stand-ins)
        keeps the parent derivation, which in a single-layer world is the same answer."""
        from pathlib import Path

        roots_fn = getattr(self._registry, "agent_roots", None)
        if callable(roots_fn):
            try:
                return tuple(str(r) for r in roots_fn())
            except Exception:  # noqa: BLE001 — a broken resolver narrows the scope, never the turn
                pass
        agent_dir = Path(agent.dir) if getattr(agent, "dir", None) else None
        return (str(agent_dir.parent),) if agent_dir is not None else ()

    def _expand_paths(self, agent, declared: tuple) -> tuple:
        """Turn an agent's authored write scope into absolute paths.

        `agent.toml` writes ``<agents_dir>`` rather than a real path, because where agents
        live depends on the deployment AND the caller: ``<repo>/v2/agents/`` in a checkout,
        ``~/.agentd/agents/`` on an install, plus the account overlay when the connection is
        signed in. The REGISTRY owns that answer (``_agent_roots``), so the token expands to
        one entry PER root — never derived from file layout here. Expanded at RunContext
        creation so the filesystem tools do plain containment and never learn what a token
        or an overlay is.

        ``<agent_dir>`` is the calling agent's own folder, which is what a self-deny needs.
        An unknown token is left as-is: it then matches nothing, so a typo narrows the scope
        rather than widening it."""
        from pathlib import Path

        agent_dir = Path(agent.dir) if getattr(agent, "dir", None) else None
        roots = self._agent_roots(agent)
        out = []
        for raw in declared or ():
            text = str(raw)
            expansions = (
                [text.replace("<agents_dir>", root) for root in roots]
                if "<agents_dir>" in text
                else [text]
            )
            for expanded in expansions:
                if agent_dir is not None:
                    expanded = expanded.replace("<agent_dir>", str(agent_dir))
                if "<" in expanded:
                    continue  # an unexpanded token would silently mean the literal string
                out.append(str(Path(expanded).expanduser()))
        # `dict.fromkeys`: order-preserving dedupe — with no overlay, the shared root is the
        # write target too, and a scope that lists one directory twice reads like a bug.
        return tuple(dict.fromkeys(out))

    def _installed_write_clamp(self, agent, expanded: tuple) -> tuple:
        """A marketplace-INSTALLED agent's declared write scope may not exceed its own folder.

        `write_roots` exists so an agent can be granted writes beyond its workspace — the
        agent-builder writes into every agent root, by design. That grant travels inside
        agent.toml, so a PUBLISHED agent could ship the same line and arrive on a buyer's
        machine holding builder-grade reach. Origin is DATA (the ownership record): `authored`
        and `curated` (platform-seeded — the agent-builder itself) keep their declaration;
        `installed` gets it intersected with the agent's own folder. `or (own dir,)` because
        an EMPTY tuple means unrestricted — dropping every root would WIDEN the scope, the
        exact inversion this clamp exists to prevent. `_protected_paths` still guards the
        definition inside that folder; workspace/sessions stay writable, which is the job.

        A registry without provenance (minimal test stand-ins) clamps nothing — same
        convention as publish's `_origin`, and `_protected_paths` remains the backstop."""
        if not expanded:
            return expanded
        agent_dir = getattr(agent, "dir", None)
        origin_of = getattr(self._registry, "origin_of", None)
        if not agent_dir or not callable(origin_of):
            return expanded
        try:
            if str(origin_of(agent.id)) != "installed":
                return expanded
        except Exception:  # noqa: BLE001 — no provenance answer: treat as authored
            return expanded
        from agent_runtime.application.write_scope import is_inside

        own = str(agent_dir)
        return tuple(r for r in expanded if is_inside(r, own)) or (own,)

    def _protected_paths(self, agent) -> tuple:
        """Directories of agents that were INSTALLED from a package — never writable.

        Guarded under EVERY root the caller's agents live in (``_agent_roots``, the same
        authority scope expansion asks): installs land in the overlay for a signed-in
        connection and in the shared catalogue otherwise, and the ledger names only ids.

        FAILS CLOSED. When the ledger cannot be read the callable returns None, and we protect
        the WHOLE of every root rather than guessing: we do not know which agents are
        someone else's, and quietly permitting the write would be a fallback that hides the
        failure. The refusal is loud, the user fixes the ledger, authoring resumes."""
        from pathlib import Path

        if self._installed_agents is None:
            return ()
        roots = self._agent_roots(agent)
        if not roots:
            return ()
        installed = self._installed_agents()
        if installed is None:
            return tuple(roots)  # unreadable ledger -> protect everything, loudly
        # THE DEFINITION IS PROTECTED, THE USER'S OWN SUBTREES ARE NOT. One folder now holds an
        # installed agent's definition next to the `workspace/` and `sessions/` that belong to
        # whoever runs it — protecting the folder wholesale meant an installed agent could not
        # write its own workspace, which is its entire job. `definition_entries` is the one
        # authority on that split (the sandbox's read grant and the hosted read scope use it too).
        from agent_runtime.domain.agent import definition_entries

        protected = []
        for root in roots:
            for aid in sorted(installed):
                agent_dir = Path(root) / aid
                if not agent_dir.is_dir():
                    protected.append(str(agent_dir))  # not there yet: protect the whole name
                    continue
                protected += definition_entries(agent_dir)
        return tuple(protected)

    def _models_for(self, agent) -> tuple:
        """``(model, model_router)`` for one run of ``agent``.

        Both together or neither: a router overwrites the model on every turn, so handing the
        engine an agent's model while it keeps a daemon-wide router is how a per-agent model
        came to be silently discarded in the first place."""
        if self._resolve_models is None:
            return agent.model, None
        return self._resolve_models(agent)

    def _resolve_agent(self, session_id: str, agent_id: str | None):
        """Explicit agent_id wins (a client naming the agent); else resolve from the
        session key. An unknown explicit id falls back to the default agent."""
        if agent_id:
            try:
                return self._registry.get(agent_id)
            except KeyError:
                pass
        return self._registry.resolve(session_id)

    def _mentioned_agents(self, text: str, exclude: AgentSpec) -> list[tuple[str, str]]:
        """Pure parse: the OTHER agents named in ``text`` via ``@id`` or ``@Display Name``
        (case-insensitive), matched against the registry and EXCLUDING ``exclude`` (the serving/
        owning agent — @-ing yourself isn't routing). No tool gate; shared by BOTH direct routing
        and the delegation directive. Returns ``[(id, name), …]`` in registry order."""
        if "@" not in (text or ""):
            return []
        list_ids = getattr(self._registry, "list_ids", None)
        if list_ids is None:
            return []
        low = text.lower()
        found: list[tuple[str, str]] = []
        for aid in list_ids():
            if aid == exclude.id:
                continue  # mentioning yourself isn't routing
            try:
                spec = self._registry.get(aid)
            except KeyError:
                continue
            name = (getattr(spec, "name", "") or aid).strip()
            if f"@{aid.lower()}" in low or (name and f"@{name.lower()}" in low):
                found.append((aid, name))
        return found

    def _mention_directive(self, text: str, agent: AgentSpec, tools: list) -> str:
        """The delegation directive — for when the current agent must ORCHESTRATE the mentioned
        agent(s) (routing="delegate", or 2+ agents named, i.e. NOT a single-agent direct reroute).

        Emitted ONLY when ``message_agent`` is actually in this turn's toolset — otherwise a
        mention degrades to plain text (no false capability). Pure string work + registry."""
        if not any(getattr(t, "name", "") == "message_agent" for t in tools):
            return ""
        mentioned = self._mentioned_agents(text, agent)
        if not mentioned:
            return ""
        listing = ", ".join(f"'{name}' (id: {aid})" for aid, name in mentioned)
        return (
            "## Delegation directive\n"
            f"The user @-mentioned other agent(s) in their message: {listing}. Delegate the "
            "relevant part of the request to each mentioned agent NOW with the `message_agent` "
            "tool (message_agent(agent=<id>, message=<clear, self-contained task>)), wait for "
            "the replies, and weave them into your answer, crediting each agent. Do NOT answer "
            "on a mentioned agent's behalf or pretend to be them."
        )

    def _agents_roster_section(self, agent: AgentSpec, tools: list) -> str:
        """Advertise the OTHER agents this one may delegate to — the roster, surfaced in the
        prompt the SAME way skills are, so the serving agent SEES specialists (not just the
        delegation verb) and can choose the right one. Fires only when it actually holds a
        delegation tool, and honors its `subagents_allow` scope (the same gate agents_list uses).
        Each line is the agent's uniformly-resolved one-line description — no hand-kept text."""
        if not any(getattr(t, "name", "") in ("spawn_subagent", "message_agent") for t in tools):
            return ""
        list_ids = getattr(self._registry, "list_ids", None)
        if list_ids is None:
            return ""
        from agent_runtime.domain.agent import _matches

        allow = getattr(agent, "subagents_allow", None)
        rows: list[str] = []
        for aid in list_ids():
            if aid == agent.id:
                continue  # never advertise yourself
            if allow is not None and not any(_matches(aid, p) for p in allow):
                continue  # outside this agent's delegation scope
            try:
                spec = self._registry.get(aid)
            except KeyError:
                continue
            name = (getattr(spec, "name", "") or aid).strip()
            desc = (getattr(spec, "description", "") or "").strip()
            if len(desc) > 200:
                desc = desc[:199].rstrip() + "…"
            label = f"{aid} ({name})" if name and name != aid else aid
            rows.append(f"- {label}: {desc}" if desc else f"- {label}")
        if not rows:
            return ""
        return (
            "## Other agents (delegation)\n"
            "These specialist agents exist alongside you. When a request fits one of them better "
            "than it fits you, DELEGATE that part instead of doing it yourself: "
            "`message_agent(agent=<id>, message=…)` reaches a persistent specialist (it remembers), "
            "`spawn_subagent(agent=<id>, task=…)` runs a one-off. Wait for the reply and weave it "
            "into your answer, crediting the agent. Prefer a specialist over attempting specialized "
            "work yourself.\n" + "\n".join(rows)
        )

    def add_tools(self, tools: list) -> None:
        """Register more tools after construction (e.g. MCP tools discovered async
        at gateway startup). They join the full toolset; each turn is then scoped to
        the resolved agent's allow/deny."""
        self._tools.extend(tools)
        self._catalog_changed()

    def _catalog_changed(self) -> None:
        """Tell whoever cares that the toolset moved. Today that is the on-disk catalog an
        agent reads before choosing which tools to grant; a boot-time snapshot would be stale
        for exactly the tool that was just created."""
        if self._on_catalog_change is None:
            return
        try:
            self._on_catalog_change(list(self._tools))
        except Exception:  # noqa: BLE001 — a bookkeeping side-effect never breaks a hot-add
            log.exception("tool catalog change hook failed")

    def set_agent_tools(self, agent_tools: dict) -> None:
        """Replace the AGENT-PRIVATE tool map wholesale (marketplace hot-reload: an installed
        agent may ship its own plugins). Wholesale replacement keeps reloads idempotent —
        no duplicate-tool bookkeeping."""
        self._agent_tools = dict(agent_tools or {})

    def _private_for(self, agent) -> list:
        """The private tools of THIS agent object, looked up by its DIRECTORY.

        Location is the identity, not the id: on a hosted daemon two accounts can each hold
        an agent of the same id, and the id-keyed map this replaces let whichever layer was
        scanned last answer for both — a stranger's private tools, or yours offered to a
        stranger. An agent without a dir (minimal stand-ins) has no folder to ship tools in."""
        from agent_runtime.domain.agent import agent_dir_key

        d = getattr(agent, "dir", None)
        return list(self._agent_tools.get(agent_dir_key(d), [])) if d else []

    def _resolve_for_caller(self, agent_id: str):
        """The CALLER's view of one agent id, or None — the registry resolves inside the
        connection's context (shared catalogue + this caller's own overlay), so an id never
        reaches into a layer the caller may not see."""
        try:
            return self._registry.get(agent_id)
        except Exception:  # noqa: BLE001 — unknown id / stand-in registry: no agent, no tools
            return None

    def agent_private_tools(self, agent_id: str | None) -> list:
        """The tools shipped INSIDE one agent's own folder (empty for a plain agent)."""
        spec = self._resolve_for_caller(agent_id) if agent_id else None
        return self._private_for(spec) if spec is not None else []

    def find_tool(self, name: str, agent_id: str | None = None):
        """Look up a registered tool by name (e.g. a namespaced MCP tool a channel invokes
        outside the agent loop). With ``agent_id``, that agent's OWN (private) tools are
        searched FIRST — an agent's shipped tool wins a name collision with the shared
        catalog for its owner. Returns the Tool or None."""
        if agent_id:
            own = [
                # agent_private_tools resolves the LOCATION-keyed map — never index
                # self._agent_tools by id here; that is the cross-tenant hole it replaced.
                *self.agent_private_tools(agent_id),
                # Its DECLARED MCP servers' tools. Here too, not only in _tools_for: a cron job,
                # a channel, and an app window calling tools.invoke all arrive through this
                # lookup, and "aws__get_cost" existing during a chat turn but not from the
                # agent's own dashboard would be an inexplicable difference.
                *(self._mcp.tools_for(agent_id) if self._mcp is not None else ()),
            ]
            for t in own:
                if getattr(t, "name", None) == name:
                    return t
        return next((t for t in self._tools if getattr(t, "name", None) == name), None)

    def remove_tools(self, prefix: str) -> int:
        """Drop every tool whose name starts with ``prefix`` from the live catalog (e.g.
        ``notion__`` when an MCP server is removed). Returns how many were dropped."""
        before = len(self._tools)
        self._tools = [t for t in self._tools if not getattr(t, "name", "").startswith(prefix)]
        return before - len(self._tools)

    def _select_tools(self, agent_id: str | None = None) -> list:
        """The live tool objects behind a catalog query (read-only; safe to call any time).

        No ``agent_id`` => the FULL active catalog (every tool currently loaded + enabled —
        agent-PRIVATE tools deliberately excluded: they belong to one agent, not the catalog).
        With ``agent_id`` => the subset THAT agent actually sees in an interactive turn:
        the shared catalog through its allow/deny PLUS its own private tools (deny still wins) —
        exactly what ``handle_message`` would pass the model. Unknown id => the full catalog."""
        tools = self._tools
        if agent_id:
            try:
                agent = self._registry.get(agent_id)
                tools = apply_mode(self._tools_for(agent), RunMode.INTERACTIVE)
            except KeyError:
                pass
        return list(tools)

    def _tools_for(self, agent: AgentSpec) -> list:
        """ONE rule for an agent's toolset, used by runs and catalog queries alike: the shared
        catalog scoped by allow/deny + the agent's OWN shipped tools (implicitly allowed,
        deny-only filtered) + the tools of the MCP servers it declared.

        Declared-MCP tools are treated exactly like shipped ones: implicitly allowed, because
        declaring a server IS the allow. Requiring `[tools] allow = ["aws__*"]` as well would mean
        every author writes the same list twice and one of them silently wins."""
        private = [
            # _private_for resolves the LOCATION-keyed map (see the constructor) — the
            # id-keyed lookup it replaced let one account's tools answer for another's
            # same-named agent.
            *self._private_for(agent),
            *(self._mcp.tools_for(agent.id) if self._mcp is not None else ()),
        ]
        shared = select_tools(self._tools, agent)
        # `add_mcp` lets the MODEL wire up an arbitrary server from chat text — text that can have
        # arrived from a web page or an email. It is per-agent (agent.toml [capabilities]
        # mcp_workshop) over the daemon's default, so switching it on for the agent being BUILT
        # does not switch it on for every agent that was installed.
        if not capability_enabled(agent, "mcp_workshop_enabled", self._mcp_workshop_default):
            shared = [t for t in shared if getattr(t, "name", "") != MCP_WORKSHOP_TOOL]
        return shared + select_private_tools(private, agent)

    def list_tools(self, agent_id: str | None = None) -> list:
        """Client-renderable SUMMARIES of the live tool catalog (name/label/summary/source). See
        ``_select_tools`` for the selection rules; ``catalog_tools`` returns the raw objects."""
        return [_tool_info(t) for t in self._select_tools(agent_id)]

    def catalog_tools(self, agent_id: str | None = None) -> list:
        """The RAW live tool objects (not summaries) behind the catalog — needed by the capability,
        model, and settings views, which read each tool's discovery metadata (plugin id, needs_model,
        resolved model, full description) that ``_tool_info`` deliberately drops. Same selection as
        ``list_tools``; tools may be wrapped (GuardedTool), so callers unwrap to read metadata."""
        return self._select_tools(agent_id)

    async def handle_message(
        self,
        session_id: str,
        text: str,
        on_event: EventSink,
        abort,
        mode: str = RunMode.INTERACTIVE,
        agent_id: str | None = None,
        attachments: list[Artifact] | None = None,
    ) -> None:
        """Run one turn end to end for the resolved agent.

        ``mode`` is the run mode (interactive | heartbeat | cron). ``agent_id`` is an
        EXPLICIT agent selection from a client (it wins); when absent, the agent is
        resolved from the session key (autonomy uses ``agent:<id>:heartbeat``). An
        unknown override falls back to the default agent. Defaults keep the reactive
        path unchanged.
        """
        owner = self._resolve_agent(session_id, agent_id)  # whose thread this is (history/files)
        # DIRECT @mention routing (Layer B): when the user addresses exactly ONE other agent, that
        # agent answers THIS turn AS ITSELF — no sub-agent hop — while the thread's history and
        # workspace stay bound to the OWNER, so the conversation is continuous and reloads intact.
        # The next message reverts to the owner (one-off). Two+ mentions stay orchestration (the
        # delegation directive below). `agent` = who SERVES (persona / tools / prompt / identity).
        agent = owner
        reroute = False
        if self._mention_routing != "delegate" and mode in (RunMode.INTERACTIVE, RunMode.CHANNEL):
            others = self._mentioned_agents(text, owner)
            if len(others) == 1:
                try:
                    agent = self._registry.get(others[0][0])
                    reroute = True
                except KeyError:
                    agent = owner  # vanished mid-flight — just answer as the owner
        # The EFFECTIVE workspace for this run (plan §11): a chat inside a project binds to the
        # project's SHARED folder (every agent in the project reads/writes the same files); a
        # standalone chat stays on the OWNER's workspace — byte-for-byte today's behavior. The
        # serving agent never chooses: the daemon 'cd's it here, before the first tool runs.
        workspace = str(owner.workspace)
        if self._resolve_workspace is not None:
            try:
                workspace = self._resolve_workspace(owner, session_id) or workspace
            except Exception:  # noqa: BLE001 — resolution is an enhancement, never blocks a turn
                pass
        # expose the run context to context-aware tools (e.g. cron tags its task with
        # this agent). Task-local, so concurrent runs never cross. Set BEFORE the prompt is
        # built, so the workspace manifest indexes the same folder the tools will use.
        # Carry the run's tracking number as DATA on the context. Contextvars cannot cross a
        # process boundary, so anything that hands work to another process (the plugin sandbox
        # today, a remote backend later) needs it explicitly — otherwise that work appears in
        # the logs as an orphan with no run attached.
        # The agent's DECLARED MCP servers, brought up on first use. Here rather than at boot
        # because its credentials are filled in after it is installed, and an agent nobody opens
        # should not be launching subprocesses. Never raises — a server that is down becomes a
        # line in the prompt (see _mcp_problems), not a turn that fails to start.
        if self._mcp is not None:
            await self._mcp.ensure(agent)
        _run_id, _turn_id = current_trace_ids()
        # The TENANT scope for this run — what it may see, and the outer write boundary. The
        # SERVING agent's, because its skills/templates are what this turn's tools will read;
        # the thread's files are the workspace above either way. ((), ()) — no resolver, or a
        # resolver that answers empty — is the desktop degenerate case: unrestricted.
        read_roots, write_clamp = ((), ())
        if self._resolve_scope is not None:
            try:
                read_roots, write_clamp = self._resolve_scope(agent, workspace)
            except Exception:  # noqa: BLE001 — fail CLOSED: a broken scope must not open the store
                from agent_runtime.application.write_scope import NOTHING

                read_roots, write_clamp = ((NOTHING,), (NOTHING,))
        # ORG ATTRIBUTION (tenancy E2): a turn that runs an ORG'S agent bills that org's pool.
        # The agent's stamped OWNER is the whole rule — the registry only resolves an org's
        # agent for that org's members in the first place, so owner-is-an-org implies the
        # caller belongs to it. Everything else (personal agents, curated, desktop) carries "".
        _owner = str(getattr(agent, "owner", "") or "")
        set_run_context(
            RunContext(
                agent_id=agent.id,
                session_key=session_id,
                mode=mode,
                workspace=workspace,
                plugins=getattr(agent, "plugins", None) or None,
                run_id=_run_id,
                turn_id=_turn_id,
                org_id=_owner if _ownership.is_org(_owner) else "",
                # getattr, like `plugins` above: an AgentSpec always has these, but the service
                # is handed stand-in agent objects too (tests, and any caller that builds a
                # minimal spec). A missing field means "declared nothing" — unrestricted.
                write_roots=self._installed_write_clamp(
                    agent, self._expand_paths(agent, getattr(agent, "write_roots", ()))
                ),
                write_denies=self._expand_paths(agent, getattr(agent, "write_denies", ())),
                protected_paths=self._protected_paths(agent),
                read_roots=tuple(read_roots),
                write_clamp=tuple(write_clamp),
                # NAMES only — what this agent declared it needs. The values stay in the .env
                # under their prefixed names and are read one at a time, at the moment a
                # credential is substituted into a request.
                settings=tuple(f.key for f in (getattr(agent, "settings", ()) or ())),
            )
        )
        tools = apply_mode(self._tools_for(agent), mode)  # serving-agent scope + private + run mode
        session = self._make_session(session_id, owner)  # history stays in the OWNER's partition
        messages = session.load()  # prior history (read)
        # attachments (already saved to the workspace by the transport layer, carried by
        # reference) ride along on the user turn: the client renders them, and the LLM
        # adapter inlines any image so a vision model can see it.
        user_msg = UserMessage(content=text, attachments=list(attachments or []))
        messages.append(user_msg)  # add the new user turn to context
        session.append(user_msg)  # persist it
        system_prompt = self._build_prompt(tools, agent, mode, text)  # identity + bootstrap + tools
        # @mention delegation (Layer B): with routing="delegate" (or 2+ agents named, i.e. NOT a
        # single-agent direct reroute) the serving agent delegates via message_agent and weaves
        # the replies in. Suppressed when we ALREADY rerouted this turn straight to the one agent
        # named — it's answering as itself, so there's nothing to delegate.
        directive = "" if reroute else self._mention_directive(text, agent, tools)
        if directive:
            system_prompt = system_prompt + "\n\n" + directive
        # Standing roster (Layer B): advertise the OTHER agents this one can delegate to — the fix
        # for "the generalist never knew a specialist existed." Gated to agents that hold a
        # delegation tool; costs a few lines/turn (a handful of agents), no relevance filter needed.
        roster = self._agents_roster_section(agent, tools)
        if roster:
            system_prompt = system_prompt + "\n\n" + roster
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
        run_model, run_router = self._models_for(agent)
        await self._engine.run(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            on_event=on_event,
            abort=abort,
            session=session,
            model=run_model,  # per-agent override (None = the engine default)
            model_router=run_router,  # ...and the router that would otherwise overwrite it
        )
        # RUN seam: a scheduled run MUST record an outcome. If the agent finished WITHOUT
        # calling report_outcome (common: it did the work but skipped the bookkeeping), force
        # ONE follow-up turn to make it declare — so a successful run isn't mislabeled
        # 'incomplete'. Fires at most once; if it still won't declare, the gateway marks it.
        if mode == RunMode.CRON and not abort.is_set() and current_run_outcome() is None:
            nudge = UserMessage(
                content=(
                    "You are a SCHEDULED run and finished WITHOUT recording the outcome. Call "
                    "`report_outcome` now, exactly once: status='done' if you completed the task, "
                    "'blocked' if you could not proceed (put the blocker in `detail`), or 'failed' "
                    "if it errored. Do this now — it is the only way the user learns the result."
                )
            )
            messages.append(nudge)
            session.append(nudge)
            await self._engine.run(
                messages=messages,
                system_prompt=system_prompt,
                tools=tools,
                on_event=on_event,
                abort=abort,
                session=session,
                model=run_model,
                model_router=run_router,
            )
