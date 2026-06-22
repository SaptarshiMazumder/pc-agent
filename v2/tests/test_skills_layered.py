"""Layered skills: an agent reads the shared GLOBAL library + its OWN agents/<id>/skills/,
with its own skills overriding a global one of the same name; skill_workshop writes into the
calling agent's own dir (the default `main` writes global)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application import run_context as rc
from agentd.application.interfaces.skills import Skill
from agentd.application.run_context import RunContext
from agentd.domain.agent import merge_skills
from agentd.infrastructure.skills.file_skills import load_skills_dir
from agentd.infrastructure.tools.skill_tool import SkillWorkshopTool


def _skill(name, desc="d"):
    return Skill(name=name, description=desc, path=f"/{name}", always=False, body="")


# ---- merge (pure) ----------------------------------------------------------

def test_merge_own_overrides_global_by_name():
    merged = {s.name: s for s in merge_skills(
        [_skill("a", "global-a"), _skill("b")],
        [_skill("a", "own-a"), _skill("c")])}
    assert merged["a"].description == "own-a"          # own wins the collision
    assert set(merged) == {"a", "b", "c"}              # union of names


# ---- per-dir loading -------------------------------------------------------

def test_load_skills_dir(tmp_path):
    d = tmp_path / "skills" / "foo"
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: foo\ndescription: when to foo\n---\nbody\n", encoding="utf-8")
    skills = load_skills_dir(tmp_path / "skills")
    assert [s.name for s in skills] == ["foo"] and "foo" in skills[0].description


def test_load_skills_dir_absent_is_empty(tmp_path):
    assert load_skills_dir(tmp_path / "nope") == []


# ---- skill_workshop writes per-agent --------------------------------------

def _cfg(tmp_path):
    return SimpleNamespace(agents_dir=tmp_path / "agents", skills_dir=tmp_path / "skills")


def test_skill_workshop_writes_to_agents_workspace_skills(tmp_path):
    ws = tmp_path / "agents" / "scout" / "workspace"
    tool = SkillWorkshopTool(_cfg(tmp_path))
    tok = rc._current.set(RunContext("scout", "s", "interactive", workspace=str(ws)))
    try:
        asyncio.run(tool.execute("c", {"action": "create", "name": "my-flow",
                                       "description": "when X", "body": "do Y"}, asyncio.Event()))
    finally:
        rc._current.reset(tok)
    f = ws / "skills" / "my-flow" / "SKILL.md"           # <workspace>/skills/<name>/SKILL.md
    assert f.is_file() and "name: my-flow" in f.read_text(encoding="utf-8")


def test_skill_workshop_main_writes_global(tmp_path):
    tool = SkillWorkshopTool(_cfg(tmp_path))
    tok = rc._current.set(RunContext("main", "s", "interactive"))
    try:
        asyncio.run(tool.execute("c", {"action": "create", "name": "g",
                                       "description": "d", "body": "b"}, asyncio.Event()))
    finally:
        rc._current.reset(tok)
    assert (tmp_path / "skills" / "g" / "SKILL.md").is_file()        # main -> global library
    assert not (tmp_path / "agents" / "main").exists()
