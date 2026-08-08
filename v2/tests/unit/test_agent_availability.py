"""A hosted daemon must be able to withhold an agent — from DATA, never from an id in code.

The forcing case is Agent Builder: `create_tool` writes Python and hot-loads it into the running
process, and `exec` runs a shell. On a desktop that is the owner acting on their own machine, and
it is the whole product. On one container serving strangers it is any signed-in visitor acting on
everyone else's files. Same code, different blast radius, because the boundary moved.

The temptation is `if agent_id == "agent-builder"`. These tests pin the shape that avoids it: the
AUTHOR declares `requires_local`, the OPERATOR overrides in either direction, and the whole
mechanism is inert on a desktop install.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import tomllib

from agent_runtime.domain.agent_availability import is_hosted, withheld_reason


class _Cfg:
    def __init__(self, **kw):
        self.hosted = kw.pop("hosted", False)
        self.hosted_agents_deny = kw.pop("deny", ())
        self.hosted_agents_allow = kw.pop("allow", ())
        for k, v in kw.items():
            setattr(self, k, v)


# --- desktop is untouched ---------------------------------------------------


def test_nothing_is_withheld_on_a_desktop_install():
    """The single most important property: this whole mechanism must not exist for the desktop
    user, whose agent CAN have a shell because the machine is theirs."""
    cfg = _Cfg(hosted=False, deny=("agent-builder",))
    assert withheld_reason("agent-builder", True, cfg) is None


def test_hosted_is_read_not_re_derived():
    """One answer, computed once in load_config. Two callers deriving 'am I hosted' from
    different inputs is how a system ends up in two modes at the same time."""
    assert is_hosted(_Cfg(hosted=True))
    assert not is_hosted(_Cfg(hosted=False))
    assert not is_hosted(object())  # a config that predates the field must not crash


# --- the author's declaration -----------------------------------------------


def test_an_agent_that_declares_requires_local_is_withheld_when_hosted():
    reason = withheld_reason("agent-builder", True, _Cfg(hosted=True))
    assert reason and "requires_local" in reason


def test_an_ordinary_agent_is_offered():
    assert withheld_reason("weather", False, _Cfg(hosted=True)) is None


def test_main_is_never_withheld():
    """Withholding the default agent leaves a daemon with nothing to talk to — so a bad glob
    degrades the roster instead of emptying it."""
    assert withheld_reason("main", True, _Cfg(hosted=True, deny=("*",))) is None


# --- the operator's overrides -----------------------------------------------


def test_the_operator_can_withhold_an_agent_that_did_not_declare_it():
    reason = withheld_reason("weather", False, _Cfg(hosted=True, deny=("weather",)))
    assert reason and "hosted_agents_deny" in reason


def test_the_operator_can_permit_an_agent_that_declared_it():
    """For a deployment that HAS solved the isolation problem — a runtime per user. The agent
    cannot grant this to itself; it lives in the operator's config."""
    assert withheld_reason("agent-builder", True, _Cfg(hosted=True, allow=("agent-builder",))) is None


def test_deny_beats_allow():
    cfg = _Cfg(hosted=True, deny=("build-*",), allow=("build-bot",))
    assert withheld_reason("build-bot", False, cfg)


def test_globs_match_the_same_way_subagents_allow_does():
    cfg = _Cfg(hosted=True, deny=("check-*",))
    assert withheld_reason("check-links", False, cfg)
    assert withheld_reason("checkout", False, cfg) is None  # prefix is 'check-', not 'check'


# --- the declaration is real, not just documented ---------------------------

ROOT = Path(__file__).resolve().parents[2]


def test_agent_builder_actually_declares_it():
    """The rule is only worth having if the agent it exists for uses it. A future edit that
    drops this line would silently expose create_tool to every hosted visitor."""
    data = tomllib.loads((ROOT / "agents" / "agent-builder" / "agent.toml").read_text("utf-8"))
    assert data.get("requires_local") is True, (
        "agent-builder must stay local-only: it hot-loads Python into the running process"
    )


def test_the_declaration_is_a_top_level_key():
    """TOML scopes a key written after the first [table] INTO that table, where nothing reads
    it — the exact silent-failure this repo has already been bitten by once."""
    text = (ROOT / "agents" / "agent-builder" / "agent.toml").read_text("utf-8")
    line = next(i for i, ln in enumerate(text.splitlines()) if ln.startswith("requires_local"))
    first_table = next(
        (i for i, ln in enumerate(text.splitlines()) if ln.strip().startswith("[")), 10**6
    )
    assert line < first_table


# --- the registry actually drops it -----------------------------------------
# The rule is only enforcement if the roster is short one agent. Everything else in the daemon
# derives from the registry, so this is where "withheld" has to mean "absent".

from types import SimpleNamespace  # noqa: E402 — grouped with the tests that need it

from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry  # noqa: E402


def _registry(tmp_path, *, hosted, requires_local=True, **policy):
    agents = tmp_path / "agents"
    d = agents / "builder"
    d.mkdir(parents=True)
    body = 'name = "Builder"\n'
    if requires_local:
        body += "requires_local = true\n"
    (d / "agent.toml").write_text(body, encoding="utf-8")
    (d / "IDENTITY.md").write_text("I build agents.", encoding="utf-8")
    cfg = SimpleNamespace(
        agent_name="JARVIS",
        workspace=tmp_path / "ws",
        state_dir=tmp_path / "state",
        agents_dir=agents,
        hosted=hosted,
        hosted_agents_deny=policy.get("deny", ()),
        hosted_agents_allow=policy.get("allow", ()),
    )
    return FileAgentRegistry(cfg)


def test_a_local_only_agent_is_absent_from_a_hosted_roster(tmp_path):
    reg = _registry(tmp_path, hosted=True)
    assert reg.list_ids() == ["main"]


def test_it_is_not_even_resolvable_by_session_key(tmp_path):
    """Absent, not merely unlisted — an unknown agent falls back to main, so there is no route
    to it left at all."""
    reg = _registry(tmp_path, hosted=True)
    assert reg.resolve("agent:builder:chat:x").id == "main"


def test_the_same_agent_is_present_on_a_desktop_daemon(tmp_path):
    reg = _registry(tmp_path, hosted=False)
    assert set(reg.list_ids()) == {"main", "builder"}
    assert reg.get("builder").requires_local is True


def test_the_operator_can_put_it_back(tmp_path):
    reg = _registry(tmp_path, hosted=True, allow=("builder",))
    assert set(reg.list_ids()) == {"main", "builder"}
