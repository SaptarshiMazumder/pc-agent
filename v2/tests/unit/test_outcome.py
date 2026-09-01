"""Task outcomes — the cron run declares done/blocked/failed so history shows
*did it actually work*, not just *did it run*."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from outcome_tool import ReportOutcomeTool

from agent_runtime.application.run_context import set_run_outcome, take_run_outcome
from agent_runtime.domain.agent import RunMode, apply_mode
from agent_runtime.infrastructure.prompt import build_system_prompt


def test_run_outcome_sink_roundtrip_and_consume():
    take_run_outcome()  # clear any prior state
    assert take_run_outcome() is None
    set_run_outcome("blocked", "needs Drive auth")
    assert take_run_outcome() == ("blocked", "needs Drive auth")
    assert take_run_outcome() is None  # consumed on read


@pytest.mark.asyncio
async def test_report_outcome_tool_sets_the_sink():
    take_run_outcome()  # clear
    r = await ReportOutcomeTool().execute(
        "c", {"status": "blocked", "detail": "needs Drive auth"}, asyncio.Event()
    )
    assert r.is_error is False
    assert take_run_outcome() == ("blocked", "needs Drive auth")


@pytest.mark.asyncio
async def test_report_outcome_tool_rejects_bad_status():
    take_run_outcome()
    r = await ReportOutcomeTool().execute("c", {"status": "nope"}, asyncio.Event())
    assert r.is_error is True
    assert take_run_outcome() is None  # nothing recorded on a bad call


def test_report_outcome_is_cron_mode_only():
    tools = [ReportOutcomeTool()]
    assert [t.name for t in apply_mode(tools, RunMode.CRON)] == ["report_outcome"]
    assert apply_mode(tools, RunMode.INTERACTIVE) == []  # hidden in chat
    assert apply_mode(tools, RunMode.HEARTBEAT) == []  # and on heartbeat ticks


def test_cron_outcome_note_injected_only_on_cron():
    cfg = SimpleNamespace(agent_name="J", workspace=Path("."), agent_id="main")
    on = build_system_prompt(cfg, [], "m", cron=True)
    off = build_system_prompt(cfg, [], "m", cron=False)
    assert "report_outcome" in on and "Scheduled run" in on
    assert "report_outcome" not in off
