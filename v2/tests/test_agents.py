"""Phase 1 — agent definitions: session routing, scoping, bootstrap, file registry."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.agent import (
    AgentSpec,
    agent_id_from_session_key,
    select_skills,
    select_tools,
)
from agentd.infrastructure.agents import FileAgentRegistry
from agentd.infrastructure.agents.bootstrap import load_bootstrap


def _spec(**over):
    base = dict(id="x", name="x", workspace=Path("."), state_dir=Path("."))
    base.update(over)
    return AgentSpec(**base)


def _cfg(tmp_path, agents_dir=None):
    return SimpleNamespace(
        agent_name="JARVIS",
        workspace=tmp_path / "ws",
        state_dir=tmp_path / "state",
        agents_dir=agents_dir if agents_dir is not None else tmp_path / "agents",
    )


class _T:
    def __init__(self, name):
        self.name = name


# ---- session-key routing ---------------------------------------------------

def test_agent_id_from_session_key():
    assert agent_id_from_session_key("agent:support:slack:u1") == "support"
    assert agent_id_from_session_key("agent:marketing:main") == "marketing"
    assert agent_id_from_session_key("default") == "main"       # legacy plain key
    assert agent_id_from_session_key("agent:") == "main"
    assert agent_id_from_session_key("") == "main"


# ---- tool / skill scoping --------------------------------------------------

def test_select_tools_allow_deny_wildcard():
    tools = [_T("read"), _T("write"), _T("google__gmail"), _T("google__drive")]
    assert [t.name for t in select_tools(tools, _spec())] == \
        ["read", "write", "google__gmail", "google__drive"]            # None = all
    allow = _spec(tools_allow=("read", "google__*"))                   # wildcard server
    assert [t.name for t in select_tools(tools, allow)] == \
        ["read", "google__gmail", "google__drive"]
    deny = _spec(tools_deny=("write",))
    assert "write" not in [t.name for t in select_tools(tools, deny)]


def test_select_skills():
    skills = [_T("github"), _T("git"), _T("language")]
    assert [s.name for s in select_skills(skills, _spec(skills_allow=("git*",)))] == \
        ["github", "git"]
    assert len(select_skills(skills, _spec())) == 3                    # None = all


# ---- bootstrap loader ------------------------------------------------------

def test_load_bootstrap_concatenates(tmp_path):
    d = tmp_path / "support"
    d.mkdir()
    (d / "IDENTITY.md").write_text("I am support.", encoding="utf-8")
    (d / "AGENTS.md").write_text("Be kind.", encoding="utf-8")
    out = load_bootstrap(d)
    assert out.startswith("# Agent Definition")
    assert "## Identity (IDENTITY.md)" in out and "I am support." in out
    assert "## Operating rules (AGENTS.md)" in out and "Be kind." in out


def test_load_bootstrap_empty_dir(tmp_path):
    d = tmp_path / "empty"
    d.mkdir()
    assert load_bootstrap(d) == ""


# ---- registry: back-compat synthesized main --------------------------------

def test_registry_synthesizes_main_when_no_agents_dir(tmp_path):
    cfg = _cfg(tmp_path)  # agents_dir does not exist
    reg = FileAgentRegistry(cfg)
    assert reg.list_ids() == ["main"]
    main = reg.get("main")
    assert main.name == "JARVIS"
    assert main.state_dir == cfg.state_dir          # legacy flat path (back-compat)
    assert main.workspace == cfg.workspace
    assert main.tools_allow is None and main.instructions == ""
    assert reg.resolve("default") is main           # legacy keys -> main
    assert reg.resolve("agent:nope:x") is main      # unknown agent -> main


# ---- registry: an independent file-defined agent ---------------------------

def test_registry_loads_file_agent(tmp_path):
    agents = tmp_path / "agents"
    sup = agents / "support"
    sup.mkdir(parents=True)
    (sup / "agent.toml").write_text(
        'name = "Support Bot"\nskills = ["github"]\n[tools]\nallow = ["read", "web_search"]\n',
        encoding="utf-8",
    )
    (sup / "IDENTITY.md").write_text("You are Acme support.", encoding="utf-8")
    reg = FileAgentRegistry(_cfg(tmp_path, agents_dir=agents))
    assert set(reg.list_ids()) == {"main", "support"}
    s = reg.get("support")
    assert s.name == "Support Bot"
    assert s.tools_allow == ("read", "web_search")
    assert s.skills_allow == ("github",)
    assert "You are Acme support." in s.instructions
    # partitioned session path (NOT the legacy flat path)
    assert s.state_dir == (tmp_path / "state") / "agents" / "support"
    assert reg.resolve("agent:support:slack:u1") is s
    assert reg.resolve("default").id == "main"      # main still default


def test_file_defined_main_keeps_legacy_session_path(tmp_path):
    agents = tmp_path / "agents"
    (agents / "main").mkdir(parents=True)
    (agents / "main" / "IDENTITY.md").write_text("Main persona.", encoding="utf-8")
    cfg = _cfg(tmp_path, agents_dir=agents)
    reg = FileAgentRegistry(cfg)
    main = reg.get("main")
    assert main.state_dir == cfg.state_dir          # legacy, even when file-defined
    assert "Main persona." in main.instructions


def test_registry_skips_bad_agent_without_breaking_others(tmp_path):
    agents = tmp_path / "agents"
    bad = agents / "broken"
    bad.mkdir(parents=True)
    (bad / "agent.toml").write_text("this is = = not valid toml ===", encoding="utf-8")
    good = agents / "sales"
    good.mkdir(parents=True)
    (good / "agent.toml").write_text('name = "Sales"\n', encoding="utf-8")
    reg = FileAgentRegistry(_cfg(tmp_path, agents_dir=agents))
    ids = set(reg.list_ids())
    assert "sales" in ids and "main" in ids and "broken" not in ids
