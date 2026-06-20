"""Phase 2b-ii — goals: the goal store + the goal tool (advisory budget)."""

import asyncio
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application.run_context import RunContext, set_run_context
from agentd.domain.autonomy import Goal
from agentd.infrastructure.tasks import SqliteTaskStore
from agentd.infrastructure.tools.goal_tool import GoalTool


def _goal(**over):
    base = dict(id="g1", agent_id="main", session_key="s", objective="ship it",
                token_budget=1000, status="active", created_at=0.0)
    base.update(over)
    return Goal(**base)


def test_goal_store_crud(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.create_goal(_goal(id="g1", session_key="s1", objective="A", created_at=0.0))
    store.create_goal(_goal(id="g2", session_key="s1", objective="B", created_at=2.0))
    assert store.active_goal("s1").id == "g2"            # latest active
    assert store.active_goal("other") is None
    assert store.update_goal("g2", "complete") is True
    assert store.active_goal("s1").id == "g1"            # g2 done -> falls back to g1
    store.close()


@pytest.mark.asyncio
async def test_goal_tool_create_get_update(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = GoalTool(store)
    set_run_context(RunContext("watcher", "agent:watcher:cron", "heartbeat"))
    r = await tool.execute("c", {"action": "create", "objective": "find 5 leads",
                                 "token_budget": 50000}, asyncio.Event())
    assert r.is_error is False and "find 5 leads" in r.content[0].text
    g = store.active_goal("agent:watcher:cron")
    assert g.objective == "find 5 leads" and g.token_budget == 50000 and g.agent_id == "watcher"

    r2 = await tool.execute("c", {"action": "get"}, asyncio.Event())
    assert "find 5 leads" in r2.content[0].text and "50000" in r2.content[0].text

    r3 = await tool.execute("c", {"action": "update", "status": "complete"}, asyncio.Event())
    assert r3.is_error is False
    assert store.active_goal("agent:watcher:cron") is None   # no longer active
    store.close()


@pytest.mark.asyncio
async def test_goal_tool_get_none_and_bad_status(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = GoalTool(store)
    set_run_context(RunContext("main", "s", "interactive"))
    none = await tool.execute("c", {"action": "get"}, asyncio.Event())
    assert "No active goal" in none.content[0].text
    await tool.execute("c", {"action": "create", "objective": "x"}, asyncio.Event())
    bad = await tool.execute("c", {"action": "update", "status": "nope"}, asyncio.Event())
    assert bad.is_error is True
    store.close()
