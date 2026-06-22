"""Layered skills: an agent reads the shared GLOBAL library (= MAIN's agents/main/skills/)
PLUS its OWN agents/<id>/skills/, its own overriding a global of the same name; skill_workshop
writes into the calling agent's own agents/<id>/skills/ (main's own IS the global library)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.application import run_context as rc
from agentd.application.interfaces.skills import Skill
from agentd.application.run_context import RunContext
from agentd.domain.agent import merge_skills, select_skills
from agentd.infrastructure.agents import FileAgentRegistry
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


def test_skill_workshop_writes_to_agents_skills(tmp_path):
    # a named agent writes into its OWN agents/<id>/skills/ (out of the workspace), private to it
    ws = tmp_path / "agents" / "scout" / "workspace"
    tool = SkillWorkshopTool(_cfg(tmp_path))
    tok = rc._current.set(RunContext("scout", "s", "interactive", workspace=str(ws)))
    try:
        asyncio.run(tool.execute("c", {"action": "create", "name": "my-flow",
                                       "description": "when X", "body": "do Y"}, asyncio.Event()))
    finally:
        rc._current.reset(tok)
    f = tmp_path / "agents" / "scout" / "skills" / "my-flow" / "SKILL.md"   # agents/<id>/skills/
    assert f.is_file() and "name: my-flow" in f.read_text(encoding="utf-8")
    assert not (ws / "skills").exists()                  # NOT inside the workspace anymore


def test_skill_workshop_main_writes_global(tmp_path):
    # main writes into agents/main/skills/ — which IS the shared/global library every agent reads
    tool = SkillWorkshopTool(_cfg(tmp_path))
    tok = rc._current.set(RunContext("main", "s", "interactive"))
    try:
        asyncio.run(tool.execute("c", {"action": "create", "name": "g",
                                       "description": "d", "body": "b"}, asyncio.Event()))
    finally:
        rc._current.reset(tok)
    assert (tmp_path / "agents" / "main" / "skills" / "g" / "SKILL.md").is_file()


# ---- read matrix: main = global to ALL; named private; one-way -------------

def _put_skill(agents_dir, agent, skill):
    d = agents_dir / agent / "skills" / skill
    d.mkdir(parents=True)
    (d / "SKILL.md").write_text(
        f"---\nname: {skill}\ndescription: d\n---\nb\n", encoding="utf-8")


def test_skill_read_matrix(tmp_path):
    # main's skills are the GLOBAL library every agent reads; each named agent's own skills
    # are PRIVATE to it. Inheritance is one-way (main never sees a named agent; siblings don't
    # see each other). This mirrors container.resolve_skills exactly.
    agents = tmp_path / "agents"
    _put_skill(agents, "main", "shared")
    _put_skill(agents, "scout", "scout-own")
    _put_skill(agents, "other", "other-secret")
    cfg = SimpleNamespace(agent_name="JARVIS", workspace=tmp_path / "ws",
                          state_dir=tmp_path / "state", agents_dir=agents)
    reg = FileAgentRegistry(cfg)
    main_dir = reg.get("main").skills_dir

    def reads(agent_id):                                  # == container.resolve_skills
        agent = reg.get(agent_id)
        glob = select_skills(load_skills_dir(main_dir), agent)
        if agent.id == "main":
            return {s.name for s in glob}
        own = load_skills_dir(agent.skills_dir)
        return {s.name for s in merge_skills(glob, own)}

    assert reads("main") == {"shared"}                    # main: only the global (its own)
    assert reads("scout") == {"shared", "scout-own"}      # named: global + own
    assert reads("other") == {"shared", "other-secret"}
    # one-way: nobody sees another named agent's private skills, and main sees no named skills
    assert "scout-own" not in reads("other")
    assert "other-secret" not in reads("scout")
    assert reads("main") == {"shared"}
