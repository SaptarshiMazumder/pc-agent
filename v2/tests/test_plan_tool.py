import asyncio
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "plugins" / "planning"))

from planning_plugin import UpdatePlanTool  # built-in 'planning' bundle


@pytest.mark.asyncio
async def test_stores_plan_in_details_with_empty_content():
    res = await UpdatePlanTool().execute("c1", {
        "explanation": "kick off",
        "plan": [
            {"step": "open LinkedIn", "status": "in_progress"},
            {"step": "summarize", "status": "pending"},
        ],
    }, asyncio.Event())
    assert res.is_error is False
    assert res.content == []                       # OpenClaw: empty content
    assert res.details["status"] == "updated"
    assert res.details["explanation"] == "kick off"
    assert len(res.details["plan"]) == 2


@pytest.mark.asyncio
async def test_at_most_one_in_progress_enforced():
    res = await UpdatePlanTool().execute("c1", {"plan": [
        {"step": "a", "status": "in_progress"},
        {"step": "b", "status": "in_progress"},
    ]}, asyncio.Event())
    assert res.is_error is True
    assert "at most one in_progress" in res.content[0].text


@pytest.mark.asyncio
async def test_empty_plan_is_error():
    res = await UpdatePlanTool().execute("c1", {"plan": []}, asyncio.Event())
    assert res.is_error is True
    assert "plan required" in res.content[0].text


def test_schema_matches_openclaw_shape():
    p = UpdatePlanTool.parameters
    assert p["required"] == ["plan"]
    assert p["properties"]["plan"]["minItems"] == 1
    step = p["properties"]["plan"]["items"]["properties"]
    assert set(step) == {"step", "status"}
    assert step["status"]["enum"] == ["pending", "in_progress", "completed"]
    assert "explanation" in p["properties"]


def test_registered_in_container():
    from agentd.config import load_config
    from agentd.main.container import build_service
    svc = build_service(load_config(), None, None)
    assert "update_plan" in [t.name for t in svc._tools]


def test_tooling_line_uses_the_tool_description():
    from agentd.infrastructure.prompt import build_system_prompt

    class _Cfg:
        agent_name = "JARVIS"
        workspace = Path(".")
        agent_id = "main"

    p = build_system_prompt(_Cfg(), [UpdatePlanTool()], "m")
    assert "update_plan: Update current run plan" in p        # tooling line = description first line


def test_planning_directive_is_a_bundle_prompt_section():
    # the ## Planning nudge is contributed by the planning bundle, gated on update_plan being present.
    import planning_plugin
    shown = planning_plugin._planning_section([UpdatePlanTool()], None, None)
    assert "## Planning" in shown and "FIRST action MUST be to call" in shown
    assert planning_plugin._planning_section([], None, None) == ""   # gated off when the tool is absent


def test_description_says_break_down_with_tools():
    d = UpdatePlanTool.description
    assert "BREAK THE TASK DOWN" in d and "name the specific tool" in d
