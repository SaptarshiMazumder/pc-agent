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
    """Build the Playwright browser manager, or None if Playwright isn't installed."""
    try:
        from agentd.infrastructure.tools.browser import BrowserManager

        return BrowserManager(config)
    except ImportError:
        log.warning("playwright not installed; browser tool disabled")
        return None


def build_service(config: Config, browser_manager) -> AgentService:
    """Assemble the AgentService use-case from concrete implementations."""
    tools = build_tools(config, browser_manager)
    # the LLM service: LiteLLM with the configured thinking level pre-bound
    stream_fn = functools.partial(litellm_stream, reasoning_effort=config.reasoning_effort)
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


def build_gateway(config: Config) -> Gateway:
    """Top-level: build everything and return the ready-to-serve Gateway."""
    browser_manager = build_browser_manager(config)
    service = build_service(config, browser_manager)
    return Gateway(config=config, service=service, browser_manager=browser_manager)
