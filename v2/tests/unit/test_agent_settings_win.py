"""AN AGENT'S OWN SETTINGS DECIDE HOW THAT AGENT RUNS. Always.

This is one rule with no exceptions, and it is written down here because it was broken in a way
nobody could see from the outside. A user set their agent's model, turned its cost-efficiency off,
saved, restarted the whole application — and every single run still used the daemon's cheap model,
which had no credit, failing with an error naming a model they had never chosen.

The cause was one word. `router_for()` answers `None` for an agent whose cost-efficiency is OFF —
a DECISION. The engine read it as `model_router or self._model_router`, which cannot tell a
decision from an absence, so "off" fell through to the daemon's router, which was on. The off
switch re-enabled the thing it switched off, and because a router overwrites the model on every
turn, the agent's chosen model was discarded too.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from agent_runtime.domain.agent_config import resolve
from agent_runtime.infrastructure.engine.native import NativeEngine
from agent_runtime.infrastructure.llm.model_router import router_for

DAEMON_CHEAP = "daemon/cheap-model"
AGENT_CHOICE = "agent/chosen-model"


def _config(agent_entry: dict, daemon_cost_efficiency: dict | None = None):
    """A daemon whose own cost-efficiency is ON — the state that made this invisible. With it off
    the fallback was harmless, which is why it survived so long."""
    return SimpleNamespace(
        model="daemon/default",
        reasoning_effort="medium",
        max_turns=100,
        model_fallbacks=[],
        verify_tool=False,
        memory_enabled=False,
        cost_efficiency=daemon_cost_efficiency
        if daemon_cost_efficiency is not None
        else {"enabled": True, "text_model": DAEMON_CHEAP, "vision_model": "daemon/vision"},
        agents={"a": agent_entry},
    )


# ── resolution: the agent's values win, key by key ──────────────────────────
def test_an_agents_model_wins_over_the_daemons():
    values, sources = resolve(_config({"model": AGENT_CHOICE}), "a")
    assert values["model"] == AGENT_CHOICE
    assert sources["model"] == "agent"


def test_a_knob_the_agent_did_not_set_still_comes_from_the_daemon():
    """Key by key, not all-or-nothing. An agent that sets only `model` must not boot with no
    reasoning effort and no turn limit."""
    values, sources = resolve(_config({"model": AGENT_CHOICE}), "a")
    assert values["reasoning_effort"] == "medium"
    assert sources["reasoning_effort"] == "daemon"


def test_an_agent_that_turns_cost_efficiency_off_resolves_to_no_router():
    values, _ = resolve(_config({"cost_efficiency": {"enabled": False}}), "a")
    assert values["cost_efficiency"] == {"enabled": False}
    assert router_for(values["cost_efficiency"]) is None, "off must mean no routing"


# ── the engine: 'no router' must not become 'the daemon's router' ───────────
def _model_used(run_router, engine_router) -> str:
    """Drive one turn and report which model actually reached the provider."""
    seen: list[str] = []

    async def stream_fn(*, model, **_):
        seen.append(model)
        yield {"type": "done", "message": _assistant()}

    engine = NativeEngine(
        stream_fn=stream_fn, model="engine/default", model_router=engine_router
    )
    asyncio.run(
        engine.run(
            messages=[],
            system_prompt="",
            tools=[],
            on_event=_noop,
            abort=asyncio.Event(),
            model=AGENT_CHOICE,
            model_router=run_router,
        )
    )
    return seen[0]


def _assistant():
    from agent_runtime.domain.messages import AssistantMessage, TextContent

    return AssistantMessage(content=[TextContent(text="ok")])


async def _noop(_ev):
    return None


def test_an_explicit_no_router_is_honoured_over_the_engine_default():
    """THE BUG, PINNED. Passing None means "this agent wants no routing". Falling back to the
    engine's own router there is what silently reinstated the daemon's cheap model and threw away
    the agent's choice on every turn."""
    daemon_router = router_for({"enabled": True, "text_model": DAEMON_CHEAP})
    assert daemon_router is not None

    assert _model_used(run_router=None, engine_router=daemon_router) == AGENT_CHOICE


def test_a_caller_that_says_nothing_still_gets_the_engine_default():
    """The other half of the distinction: a sub-agent run or a tool driving the engine directly
    passes no router at all, and must behave exactly as before."""
    daemon_router = router_for({"enabled": True, "text_model": DAEMON_CHEAP})
    seen: list[str] = []

    async def stream_fn(*, model, **_):
        seen.append(model)
        yield {"type": "done", "message": _assistant()}

    engine = NativeEngine(stream_fn=stream_fn, model="engine/default", model_router=daemon_router)
    asyncio.run(
        engine.run(
            messages=[],
            system_prompt="",
            tools=[],
            on_event=_noop,
            abort=asyncio.Event(),
            model=AGENT_CHOICE,
            # model_router deliberately NOT passed
        )
    )
    assert seen[0] == DAEMON_CHEAP


def test_an_agents_own_router_still_wins_when_it_wants_one():
    """Cost efficiency ON for the agent, with its own cheap model — the agent's router runs, not
    the daemon's."""
    agent_router = router_for({"enabled": True, "text_model": "agent/cheap"})
    daemon_router = router_for({"enabled": True, "text_model": DAEMON_CHEAP})

    assert _model_used(run_router=agent_router, engine_router=daemon_router) == "agent/cheap"
