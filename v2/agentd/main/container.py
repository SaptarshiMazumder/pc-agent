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
from agentd.infrastructure.skills import FileSkillRegistry
from agentd.infrastructure.tools import build_tools
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


def build_service(config: Config, browser_manager, computer_provider=None) -> AgentService:
    """Assemble the AgentService use-case from concrete implementations."""
    from agentd.infrastructure.tools.guard import GuardedTool, resolve_policy

    # Wrap EVERY tool in the reliability middleware (timeout + retry + error-norm),
    # per-tool policy resolved from config. New tools are guarded automatically.
    tools = [GuardedTool(t, resolve_policy(config, t))
             for t in build_tools(config, browser_manager, computer_provider)]
    # the LLM service: LiteLLM with the configured thinking level + idle/request
    # timeouts pre-bound (a silent/hung stream ends the turn gracefully).
    stream_fn = functools.partial(
        litellm_stream,
        reasoning_effort=config.reasoning_effort,
        idle_timeout_sec=config.llm_idle_timeout_seconds,
        request_timeout_sec=config.llm_request_timeout_seconds,
    )
    engine = NativeEngine(stream_fn, config.model, max_iterations=config.max_turns)   # swap here for Claude SDK / LangGraph
    # skills are read fresh per turn, so dropping a SKILL.md into the folder takes
    # effect on the next message without a restart (swap here for a cloud registry)
    skills = FileSkillRegistry(config.skills_dir)
    return AgentService(
        engine=engine,
        tools=tools,
        # how to make a session store for a given session id (swap here for a cloud store)
        make_session=lambda sid: SessionStore(config.state_dir, sid, cwd=str(config.workspace)),
        # how to build the system prompt for a turn (skills advertised, read on demand)
        build_prompt=lambda tools: build_system_prompt(
            config, tools, config.model, config.reasoning_effort, skills=skills.all()
        ),
    )


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
    service = build_service(config, browser_manager, computer_provider)
    return Gateway(
        config=config,
        service=service,
        browser_manager=browser_manager,
        mcp_provider=build_mcp_provider(config),
    )
