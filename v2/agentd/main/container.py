"""Composition root — wire the concrete implementations into the use-cases.

This is the ONE place that names concrete classes (LiteLLM, the native engine, the
local store, the tools). It reads the config, builds each implementation, injects
them into the AgentService use-case, and hands the assembled Gateway to the
entrypoint. Swapping any piece (a different engine, memory backend, LLM) is a change
HERE, nowhere else.
"""

from __future__ import annotations

import functools
import logging

from agentd.application.services.agent_service import AgentService
from agentd.config import Config
from agentd.infrastructure.engine.native import NativeEngine
from agentd.infrastructure.llm.litellm import litellm_stream
from agentd.infrastructure.memory.local_store import SessionStore
from agentd.infrastructure.prompt import build_system_prompt
from agentd.presentation.gateway import Gateway

log = logging.getLogger("agentd")


def build_browser_manager(config: Config):
    """Build the browser provider (Playwright today), or None if unavailable.

    Delegates to the browser package's factory — the one place a browser backend
    is selected. The returned provider is shared by the `browser` tool and the
    `web_fetch` browser-render escalation.
    """
    from agentd.infrastructure.tools.browser import build_browser_provider

    return build_browser_provider(config)


def build_computer_provider(config: Config):
    """Build the computer-use OS backend (pyautogui), or None if disabled/missing.

    OFF unless AGENTD_COMPUTER_ENABLED=1 — this tool drives the real desktop, so it
    is opt-in. The factory is the one place the backend is selected.
    """
    from agentd.infrastructure.tools.computer import build_computer_provider as _build

    return _build(config)


def build_memory_bank(config: Config):
    """The durable long-term memory store (SQLite) — only when memory is enabled; None
    otherwise. Shared by the memory tools (recall/write) across the agent's sessions."""
    if not getattr(config, "memory_enabled", False):
        return None
    from pathlib import Path

    from agentd.application.tool_models import MEMORY_EMBED_DEFAULT_MODEL, resolve_tool_model
    from agentd.infrastructure.embeddings import build_embed_fn
    from agentd.infrastructure.memory.bank import SqliteMemoryBank

    # A semantic embedder makes memory RAG (embed-on-write, cosine recall). Resolved from the unified
    # plugins map (plugins.memory.embed); defaults ON with the built-in embed model. Cache is per-bank
    # so stable notes aren't re-embedded.
    embed_model = resolve_tool_model(config, "memory", "embed", default=MEMORY_EMBED_DEFAULT_MODEL)
    embed_fn = build_embed_fn(embed_model)
    bank = SqliteMemoryBank(Path(config.state_dir) / "memory.sqlite", embed_fn=embed_fn)
    if embed_fn is not None:
        log.info("memory: enabled (semantic, model=%s)", embed_model)
    else:
        log.info("memory: enabled (keyword-only — no embedding model set)")
    return bank


def build_service(config: Config, browser_manager, computer_provider=None,
                  registry=None, task_store=None, memory_bank=None,
                  credential_store=None, connect_token_store=None) -> AgentService:
    """Assemble the AgentService use-case from concrete implementations.

    `registry` (the agent registry) and `task_store` (the cron ledger) are injected so
    the gateway and service share them; if omitted, a file-backed registry is built
    here (single-agent / tests) and there is no task ledger. `memory_bank` is the durable
    long-term memory (None unless memory is enabled)."""
    from agentd.infrastructure.resources import build_resource_manager
    from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy

    # Resource Manager: a described, cached index of workspace resources + a CRUD tool
    # (None unless enabled). Shared as both the manifest source and the `resource` tool.
    resource_manager = build_resource_manager(config)
    # THE TOOL CATALOG: internal-native tools + any installed plugins (drop-in dir / pip
    # entry-points), then the GLOBAL on/off filter (apply_enablement), then wrap EVERY tool in
    # the reliability middleware (timeout + retry + error-norm). New tools — internal or plugin —
    # are discovered + guarded uniformly. (Internal-/plugin-MCP tools are added async at gateway
    # startup and pass through the SAME enablement there.) The credential vault + connect-token
    # store are injected (shared with the gateway's /connect web form).
    from agentd.domain.agent import apply_enablement, apply_plugin_enablement
    from agentd.infrastructure.plugins import AllowAllEntitlement, discover_plugin_contributions

    # plugins contribute tools (join the catalog), prompt sections (teach the model how to use
    # them), and MCP servers (connected at gateway startup — appended to config.mcp_servers so
    # build_mcp_provider, called AFTER build_service, picks them up). A plugin's tools receive the
    # SAME injected singletons the built-ins do (browser, ledgers, stores) via its PluginContext.
    # the agent registry is built HERE (before plugin discovery) so it can be injected into
    # plugins too — the create_agent tool uses it to register a newly-authored agent live.
    from agentd.infrastructure.agents import FileAgentRegistry
    from agentd.application.services.agent_service import tool_source
    registry = registry or FileAgentRegistry(config)
    # ENTITLEMENT seam (4th load gate): default = entitle every compatible plugin. A commercial
    # build swaps this for a plan/tenant-aware policy here, without touching the core or plugins.
    entitlement = AllowAllEntitlement()

    # HOT-RELOAD seam (B1): `loaded_plugin_ids` tracks what discovery has loaded; `_late` carries
    # the AgentService once it exists (it's built below). `register_plugin_live` re-scans, loads
    # only NEW plugins, and merges their tools+sections into the LIVE catalog (create_tool uses it).
    loaded_plugin_ids: set = set()
    _late: dict = {}

    def _wrap(raw_tools: list) -> list:
        out = []
        for t in raw_tools:
            gt = GuardedTool(t, resolve_policy(config, t))
            gt.source = tool_source(t)         # plugin:<id> / mcp:<server> — tagged for the catalog
            out.append(gt)
        return out

    def register_plugin_live() -> dict:
        service = _late.get("service")
        if service is None:
            return {"ok": False, "error": "catalog not ready"}
        new_tools, new_sections, _srv, _sk = discover_plugin_contributions(
            config, plugin_deps, entitlement, skip_ids=loaded_plugin_ids)
        if new_tools:
            kept = apply_enablement(list(new_tools), getattr(config, "tools_enabled", None),
                                    getattr(config, "tools_disabled", ()))
            kept = apply_plugin_enablement(kept, getattr(config, "plugins", None))
            service.add_tools(_wrap(kept))
        if new_sections:
            plugin_sections.extend(new_sections)   # same list the prompt builder reads -> live
        return {"ok": True, "tools": [getattr(t, "name", "") for t in new_tools],
                "sections": len(new_sections)}

    # the agent registry is built HERE (before plugin discovery) so it can be injected into
    # plugins too — the create_agent tool uses it to register a newly-authored agent live.
    plugin_deps = {
        "browser": browser_manager, "computer": computer_provider,
        "task_store": task_store, "memory_bank": memory_bank,
        "resource_manager": resource_manager, "credential_store": credential_store,
        "connect_token_store": connect_token_store, "registry": registry,
        "register_plugin_live": register_plugin_live,
    }
    plugin_tools, plugin_sections, plugin_mcp_servers, plugin_skill_dirs = \
        discover_plugin_contributions(config, plugin_deps, entitlement, skip_ids=loaded_plugin_ids)
    if plugin_mcp_servers:
        config.mcp_servers = list(config.mcp_servers or []) + plugin_mcp_servers
    # The catalog is assembled ENTIRELY by plugin discovery now — built-in capability bundles
    # (plugins/) + third-party plugins both flow through discover_plugin_contributions. The core
    # contributes no tool implementations (they all live outside agentd/). Global on/off filter,
    # then wrap EVERY tool in the reliability middleware + tag its source.
    tools = _wrap(apply_plugin_enablement(
        apply_enablement(list(plugin_tools), getattr(config, "tools_enabled", None),
                         getattr(config, "tools_disabled", ())),
        getattr(config, "plugins", None)))
    # the LLM service: LiteLLM with the configured thinking level + idle/request
    # timeouts pre-bound (a silent/hung stream ends the turn gracefully).
    stream_fn = functools.partial(
        litellm_stream,
        reasoning_effort=config.reasoning_effort,
        idle_timeout_sec=config.llm_idle_timeout_seconds,
        request_timeout_sec=config.llm_request_timeout_seconds,
    )
    # Model failover (S11): on a clean primary error, retry the turn on the next model.
    # No fallbacks => returns the stream unwrapped (unchanged).
    if getattr(config, "model_fallbacks", None):
        from agentd.infrastructure.llm.failover import make_failover_stream

        stream_fn = make_failover_stream(stream_fn, config.model_fallbacks)
    # Decoupled liveness seam (default OFF => unchanged behavior). Answer verification
    # is the agent-invoked `verify_answer` tool (registered in build_tools), not a loop hook.
    from agentd.infrastructure.liveness import build_observers

    # Context compaction (S7): cap history to the most-recent N messages (boundary-safe).
    # 0 => None => send everything (unchanged).
    context_policy = None
    if getattr(config, "context_max_messages", 0):
        from agentd.infrastructure.context import WindowContextPolicy

        context_policy = WindowContextPolicy(config.context_max_messages)
    from agentd.application.tool_models import brain_model
    engine = NativeEngine(                                  # swap here for Claude SDK / LangGraph
        stream_fn, brain_model(config), max_iterations=config.max_turns,  # brain model: CONFIG-ONLY
        observers=build_observers(config), context_policy=context_policy,
        execution_contract=getattr(config, "execution_contract", ""),
    )
    # the agent registry: which agent owns a session + its persona/scope.
    # (built above, before plugin discovery, so plugins receive the SAME instance.)
    from agentd.domain.agent import RunMode, merge_skills, select_skills
    from agentd.infrastructure.skills.file_skills import load_skills_dir, skill_eligible

    # Layered skills (read fresh per turn, so a new SKILL.md takes effect next message):
    # MAIN's skills (agents/main/skills/) are the SHARED/global library that EVERY agent
    # inherits. A named agent ALSO sees its OWN agents/<id>/skills/ (its own overrides a
    # global of the same name). main sees only its own (= the global). Inheritance is
    # ONE-WAY: main never sees a named agent's skills, nor do siblings see each other's.
    main_skills_dir = registry.get("main").skills_dir

    # plugins bundle their own skills (plugins/<id>/skills/) — they join the SHARED set every
    # agent sees, alongside main's global library (a same-named plugin skill wins over global).
    plugin_skills = [s for d in plugin_skill_dirs for s in load_skills_dir(d)]

    def resolve_skills(agent):
        shared = select_skills(merge_skills(load_skills_dir(main_skills_dir), plugin_skills), agent)
        if agent.id == "main":
            result = shared                            # main = global + plugin skills
        else:
            own = load_skills_dir(agent.skills_dir) if getattr(agent, "skills_dir", None) else []
            result = merge_skills(shared, own)         # named: shared + own (own wins)
        return [s for s in result if skill_eligible(s, config)]   # requires gate (bins/env/config)
    # workspace-awareness layer (TURN seam): a manifest of the agent's files in every
    # prompt, so resources it created stay visible. The described Resource Manager wins
    # when on; else the plain workspace index; else nothing (all cut-out-able by flag).
    from agentd.infrastructure.workspace import build_workspace_index
    from agentd.infrastructure.workspace.cleanup import sweep_scratch
    workspace_index = resource_manager or build_workspace_index(config)

    # Optional relevance filter (post-parity): advertise only the top-K skills most related to the
    # current message. Built lazily; None unless enabled + a model is configured => no behavior change.
    from agentd.infrastructure.skills.relevance import build_skill_embed_fn, rank_skills_by_relevance
    skill_embed_fn = build_skill_embed_fn(config)

    def _build_prompt(tools, agent, mode, query=""):
        # prompt for the resolved agent + run mode: its identity/bootstrap + scoped skills +
        # model; on a heartbeat tick also inject HEARTBEAT.md. FIRST: scratch hygiene —
        # auto-sweep <workspace>/tmp/ by age once per run (ungated, cheap, bounded to the
        # scratch dir) so throwaway files never pile up or get enriched.
        sweep_scratch(agent.workspace, getattr(config, "scratch_ttl_hours", 0.0))
        skills = resolve_skills(agent)
        if skill_embed_fn is not None:                  # relevance filter (no-op when fn is None)
            skills = rank_skills_by_relevance(
                skills, query, skill_embed_fn, getattr(config, "skills_relevance_top_k", 30))
        # On a heartbeat tick, re-read HEARTBEAT.md FRESH from the agent's dir (so edits — and a
        # runtime-created agent's checklist — take effect next tick, not only after a restart);
        # fall back to the cached spec text if the dir isn't known.
        heartbeat_text = ""
        if mode == RunMode.HEARTBEAT:
            from agentd.infrastructure.agents.bootstrap import load_heartbeat
            heartbeat_text = (load_heartbeat(agent.dir) if getattr(agent, "dir", None)
                              else agent.heartbeat_instructions)
        return build_system_prompt(
            config, tools, agent.model or config.model, config.reasoning_effort,
            skills=skills, agent=agent,
            heartbeat=heartbeat_text,
            cron=(mode == RunMode.CRON),   # inject the report_outcome note on scheduled runs
            channel=(mode == RunMode.CHANNEL),   # inject the channel-reply note on channel runs
            workspace_resources=(workspace_index.manifest(agent.workspace, agent.id)
                                 if workspace_index else ""),
            plugin_sections=plugin_sections,   # tools self-describe; plugins add guidance sections
        )

    def _recall(agent, query):
        # Auto-recall block for a user turn: the top relevant long-term memories, prepended to
        # the prompt. None/empty unless memory + auto-recall are on. Vector-ranked when an embedder
        # is wired, else keyword — either way the bank records the recall to feed dreaming.
        if not (getattr(config, "memory_auto_recall", False) and memory_bank is not None):
            return ""
        hits = memory_bank.search(
            agent.id, query,
            limit=getattr(config, "memory_auto_recall_limit", 5),
            min_score=getattr(config, "memory_recall_min_score", 0.0))
        if not hits:
            return ""
        log.info("memory: auto-recalled %d note(s) for agent '%s'", len(hits), agent.id)
        lines = "\n".join(f"- {h.text}" for h in hits)
        return ("## Relevant memories\n"
                "Recalled automatically from earlier sessions — may bear on this message; verify "
                "before relying on them:\n" + lines)

    service = AgentService(
        engine=engine,
        tools=tools,
        registry=registry,
        # per-agent session store: agent.state_dir partitions sessions (all under agents/<id>/)
        make_session=lambda sid, agent: SessionStore(
            agent.state_dir, sid, cwd=str(agent.workspace)
        ),
        build_prompt=_build_prompt,
        recall=_recall,
    )
    _late["service"] = service           # late-bind so register_plugin_live can hot-add tools
    return service


def build_task_store(config: Config):
    """The durable cron ledger (SQLite) — only when autonomy is enabled; None otherwise.
    Shared by the cron tool (writes jobs) and the scheduler (fires due jobs)."""
    if not getattr(config, "autonomy_enabled", False):
        return None
    from agentd.infrastructure.tasks import SqliteTaskStore

    return SqliteTaskStore(config.state_dir / "autonomy.sqlite")


def build_mcp_provider(config: Config):
    """Build the MCP client provider (external tool connectors), or None if no
    servers are configured / the `mcp` SDK is absent. The gateway discovers its
    tools asynchronously at startup (discovery needs an event loop)."""
    from agentd.infrastructure.tools.mcp import build_mcp_provider as _build

    return _build(config)


def _add_agent_browser_mcp_server(config: Config) -> None:
    """Register the external `agent-browser` engine as an stdio MCP server, so its
    tools are discovered through the existing MCP client (namespaced agentbrowser__*).
    Idempotent. agent-browser must be installed; if it isn't, MCP discovery skips it
    gracefully (and there will be no browser — the operator opted in)."""
    from agentd.config import McpServerConfig

    existing = config.mcp_servers or []
    if any(getattr(s, "name", "") == "agentbrowser" for s in existing):
        return
    cmd = config.agent_browser_command or ["agent-browser", "mcp"]
    config.mcp_servers = list(existing) + [
        McpServerConfig(name="agentbrowser", transport="stdio", command=cmd)
    ]
    log.info("browser engine: agent-browser via MCP (command: %s)", " ".join(cmd))


def build_gateway(config: Config) -> Gateway:
    """Top-level: build everything and return the ready-to-serve Gateway."""
    from agentd.config import resolve_browser_engine

    engine = resolve_browser_engine(config)
    if engine == "agent_browser":
        # Use the external engine: omit our built-in browser tool and surface
        # agent-browser's tools via MCP instead.
        _add_agent_browser_mcp_server(config)
        browser_manager = None
    else:
        log.info("browser engine: playwright/cdp (built-in)")
        browser_manager = build_browser_manager(config)
    computer_provider = build_computer_provider(config)
    # one registry, shared by the service (resolves the agent per turn) and the
    # gateway (drives the heartbeat scheduler).
    from agentd.infrastructure.agents import FileAgentRegistry

    registry = FileAgentRegistry(config)
    task_store = build_task_store(config)   # durable cron ledger (None unless autonomy on)
    memory_bank = build_memory_bank(config)  # long-term memory (None unless memory on)
    # Login vault + connect-token store — built ONCE and SHARED by the simple_login tool
    # (mints links) and the gateway's /connect web form (writes the captured creds), so a
    # credential saved via the form is immediately visible to the tool. None unless AGENTD_VAULT_KEY.
    from agentd.infrastructure.credentials import ConnectTokenStore, build_credential_store
    credential_store = build_credential_store(config)
    connect_token_store = ConnectTokenStore() if credential_store is not None else None
    service = build_service(config, browser_manager, computer_provider,
                            registry=registry, task_store=task_store, memory_bank=memory_bank,
                            credential_store=credential_store, connect_token_store=connect_token_store)
    from agentd.infrastructure.events import build_event_log
    from agentd.infrastructure.safe_to_send import build_safe_to_send_gate
    return Gateway(
        config=config,
        service=service,
        browser_manager=browser_manager,
        mcp_provider=build_mcp_provider(config),
        registry=registry,
        task_store=task_store,
        memory_bank=memory_bank,
        event_log=build_event_log(config),   # durable per-run event stream (None unless enabled)
        credential_store=credential_store,   # /connect form writes here (shared with simple_login)
        connect_tokens=connect_token_store,
        safe_to_send_gate=build_safe_to_send_gate(config),  # egress privacy gate (None unless enabled)
    )
