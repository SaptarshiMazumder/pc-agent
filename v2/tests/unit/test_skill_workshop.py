"""S10 — skill_workshop: the agent writes a reusable SKILL.md (frontmatter + body) the
registry can pick up next turn."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from skill_tool import SkillWorkshopTool

from agentd.infrastructure.skills import FileSkillRegistry


@pytest.mark.asyncio
async def test_skill_workshop_creates_loadable_skill(tmp_path):
    skills = tmp_path / "skills"
    tool = SkillWorkshopTool(SimpleNamespace(skills_dir=str(skills)))

    r = await tool.execute(
        "c",
        {
            "action": "create",
            "name": "SUUMO Deal Watch",
            "description": "watch suumo for price drops",
            "body": "1. open the search\n2. extract listings",
        },
        asyncio.Event(),
    )
    assert r.is_error is False
    f = skills / "suumo-deal-watch" / "SKILL.md"  # name slugified
    assert f.exists()
    txt = f.read_text(encoding="utf-8")
    assert "name: suumo-deal-watch" in txt and "watch suumo" in txt and "1. open the search" in txt

    # the registry actually loads it (hot-reload path)
    loaded = {s.name for s in FileSkillRegistry(skills).all()}
    assert "suumo-deal-watch" in loaded

    lst = await tool.execute("c", {"action": "list"}, asyncio.Event())
    assert "suumo-deal-watch" in lst.content[0].text


@pytest.mark.asyncio
async def test_skill_workshop_validates(tmp_path):
    tool = SkillWorkshopTool(SimpleNamespace(skills_dir=str(tmp_path / "skills")))
    assert (
        await tool.execute("c", {"action": "create", "body": "x"}, asyncio.Event())
    ).is_error  # no name
    assert (await tool.execute("c", {"action": "bogus"}, asyncio.Event())).is_error  # bad action
