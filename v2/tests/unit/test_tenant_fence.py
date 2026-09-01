"""The tenant fence — what a hosted run may SEE, and where it may WRITE.

One shared store, many tenants. Isolation is not "each tool defaults its cwd to the right
folder" — it is a positive grant, decided once per run (user_state.tenant_scope), carried on
the RunContext, enforced at the one choke point every fs path already flows through
(write_scope.check_read / check_write). Empty scope = unrestricted = every desktop run,
byte for byte.

The probe tests at the bottom are the incident that motivated this, replayed: account Y runs
the agent whose definition lives in account X's subtree; Y must see the definition and Y's
OWN workspace, and X's workspace and transcripts must not exist for Y's run.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.application.write_scope import (
    NOTHING,
    ReadRefused,
    WriteRefused,
    check_read,
    check_write,
)
from agent_runtime.domain.agent import agent_dir_key, definition_entries
from agent_runtime.infrastructure import user_state


@pytest.fixture(autouse=True)
def _clean_ctx():
    yield
    set_run_context(None)


# ── definition_entries: the one authority on "the shareable part of an agent" ──────────


def test_definition_entries_excludes_the_users_subtrees(tmp_path):
    agent = tmp_path / "marketing-agent"
    for name in ("plugins", "skills", "templates", "workspace", "sessions"):
        (agent / name).mkdir(parents=True)
    (agent / "agent.toml").write_text("id='x'", encoding="utf-8")
    entries = definition_entries(agent)
    names = {Path(e).name for e in entries}
    assert "workspace" not in names and "sessions" not in names
    assert {"agent.toml", "plugins", "skills", "templates"} <= names


def test_definition_entries_of_a_missing_dir_is_empty(tmp_path):
    assert definition_entries(tmp_path / "nope") == ()


# ── tenant_scope: the per-run values ───────────────────────────────────────────────────


def _hosted_config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        hosted=True,
        state_dir=tmp_path / "state",
        agents_dir=str(tmp_path / "agents"),
        plugins_dir=str(tmp_path / "plugins"),
        builtin_plugins_dir="",
        hosted_read_roots=[],
    )


def test_a_non_hosted_daemon_gets_no_scope_at_all(tmp_path):
    cfg = _hosted_config(tmp_path)
    cfg.hosted = False
    assert user_state.tenant_scope(cfg, "acct_x", tmp_path, tmp_path) == ((), ())


def test_a_signed_in_caller_sees_own_subtree_definition_and_shared(tmp_path):
    cfg = _hosted_config(tmp_path)
    agent = tmp_path / "agents" / "helper"
    (agent / "skills").mkdir(parents=True)
    (agent / "workspace").mkdir()
    ws = user_state.account_workspace(cfg.state_dir, "acct_y", "helper")
    reads, clamp = user_state.tenant_scope(cfg, "acct_y", agent, ws)

    own = str(user_state.account_root(cfg.state_dir, "acct_y"))
    assert own in reads and str(ws) in reads
    assert str(agent / "skills") in reads  # the definition view, in place
    assert str(tmp_path / "agents") in reads  # the shared catalogue
    # the leak: the agent folder's OWN user data is not granted (only the caller's subtree is)
    assert str(agent / "workspace") not in reads
    assert str(agent) not in reads  # the folder itself would include workspace/ + sessions/
    assert clamp == (own, str(ws))


def test_another_tenants_subtree_is_never_in_the_answer(tmp_path):
    cfg = _hosted_config(tmp_path)
    reads, clamp = user_state.tenant_scope(cfg, "acct_y", None, "")
    other = str(user_state.account_root(cfg.state_dir, "acct_x"))
    assert all(not r.startswith(other) for r in reads)
    assert all(not c.startswith(other) for c in clamp)


def test_an_anonymous_caller_touches_no_account_and_writes_only_the_workspace(tmp_path):
    cfg = _hosted_config(tmp_path)
    agent = tmp_path / "agents" / "public-app"
    (agent / "ui").mkdir(parents=True)
    ws = agent / "workspace"
    reads, clamp = user_state.tenant_scope(cfg, "", agent, ws)
    assert all("accounts" not in r for r in reads)
    assert clamp == (str(ws),)


def test_an_anonymous_caller_with_no_workspace_can_write_nowhere(tmp_path):
    cfg = _hosted_config(tmp_path)
    _reads, clamp = user_state.tenant_scope(cfg, "", None, "")
    assert clamp == (NOTHING,)
    set_run_context(RunContext(agent_id="a", session_key="s", mode="chat", write_clamp=clamp))
    with pytest.raises(WriteRefused):
        check_write(tmp_path / "anything.txt")


# ── check_read: empty = unrestricted, values = a fence ─────────────────────────────────


def test_no_context_and_no_roots_both_pass_everything(tmp_path):
    assert check_read(tmp_path / "anywhere.txt")  # no ctx at all
    set_run_context(RunContext(agent_id="a", session_key="s", mode="chat"))
    assert check_read(tmp_path / "anywhere.txt")  # ctx, empty roots (desktop)


def test_inside_a_root_passes_outside_is_refused(tmp_path):
    mine = tmp_path / "mine"
    (mine / "sub").mkdir(parents=True)
    theirs = tmp_path / "theirs"
    theirs.mkdir()
    set_run_context(
        RunContext(
            agent_id="a",
            session_key="s",
            mode="chat",
            workspace=str(mine),
            read_roots=(str(mine),),
        )
    )
    assert check_read(mine / "sub" / "f.txt")
    with pytest.raises(ReadRefused) as e:
        check_read(theirs / "f.txt")
    assert str(mine) in str(e.value)  # the refusal names the legitimate place


def test_dot_dot_cannot_walk_out_of_a_root(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    (tmp_path / "secret.txt").write_text("x", encoding="utf-8")
    set_run_context(
        RunContext(agent_id="a", session_key="s", mode="chat", read_roots=(str(mine),))
    )
    with pytest.raises(ReadRefused):
        check_read(mine / ".." / "secret.txt")


# ── check_write's tenant clamp: platform rule, not agent declaration ───────────────────


def test_the_clamp_refuses_writes_outside_even_with_no_declared_roots(tmp_path):
    mine = tmp_path / "mine"
    mine.mkdir()
    set_run_context(
        RunContext(agent_id="a", session_key="s", mode="chat", write_clamp=(str(mine),))
    )
    assert check_write(mine / "ok.txt")
    with pytest.raises(WriteRefused) as e:
        check_write(tmp_path / "elsewhere.txt")
    assert "your own space" in str(e.value)


def test_no_clamp_keeps_the_desktop_rules_exactly(tmp_path):
    set_run_context(RunContext(agent_id="a", session_key="s", mode="chat"))
    assert check_write(tmp_path / "anywhere.txt")


# ── the incident, replayed: two accounts, one shared store ─────────────────────────────


def _two_tenant_world(tmp_path):
    """Account X authored marketing-agent (definition + X's data in ONE folder); Y runs it."""
    cfg = _hosted_config(tmp_path)
    x_agent = user_state.account_agents_dir(cfg.state_dir, "acct_x") / "marketing-agent"
    (x_agent / "templates").mkdir(parents=True)
    (x_agent / "workspace").mkdir()
    (x_agent / "sessions").mkdir()
    (x_agent / "agent.toml").write_text("id='marketing-agent'", encoding="utf-8")
    (x_agent / "templates" / "style.md").write_text("shipped", encoding="utf-8")
    (x_agent / "workspace" / "x-private.txt").write_text("X's file", encoding="utf-8")
    (x_agent / "sessions" / "chat.jsonl").write_text("X's chat", encoding="utf-8")
    y_ws = user_state.account_workspace(cfg.state_dir, "acct_y", "marketing-agent")
    y_ws.mkdir(parents=True)
    (y_ws / "y-own.txt").write_text("Y's file", encoding="utf-8")
    return cfg, x_agent, y_ws


def test_y_sees_the_definition_and_its_own_files_never_xs(tmp_path):
    cfg, x_agent, y_ws = _two_tenant_world(tmp_path)
    reads, clamp = user_state.tenant_scope(cfg, "acct_y", x_agent, y_ws)
    set_run_context(
        RunContext(
            agent_id="marketing-agent",
            session_key="s",
            mode="chat",
            workspace=str(y_ws),
            read_roots=reads,
            write_clamp=clamp,
        )
    )
    assert check_read(y_ws / "y-own.txt")  # own workspace
    assert check_read(x_agent / "templates" / "style.md")  # shipped data, in place
    with pytest.raises(ReadRefused):
        check_read(x_agent / "workspace" / "x-private.txt")  # the reported incident
    with pytest.raises(ReadRefused):
        check_read(x_agent / "sessions" / "chat.jsonl")  # transcripts: in NO grant, ever
    with pytest.raises(WriteRefused):
        check_write(x_agent / "templates" / "style.md")  # read-only: clamp says not yours


def test_the_real_read_tool_refuses_across_the_fence(tmp_path):
    """End to end through the actual core_fs funnel, not just the check."""
    from fs_tools import _resolve

    cfg, x_agent, y_ws = _two_tenant_world(tmp_path)
    reads, clamp = user_state.tenant_scope(cfg, "acct_y", x_agent, y_ws)
    set_run_context(
        RunContext(
            agent_id="marketing-agent",
            session_key="s",
            mode="chat",
            workspace=str(y_ws),
            read_roots=reads,
            write_clamp=clamp,
        )
    )
    tool_cfg = SimpleNamespace(workspace=str(y_ws))
    assert _resolve(tool_cfg, "y-own.txt")  # relative -> own workspace
    assert _resolve(tool_cfg, str(x_agent / "templates" / "style.md"))
    with pytest.raises(ReadRefused):
        _resolve(tool_cfg, str(x_agent / "workspace" / "x-private.txt"))


def test_exec_refuses_to_run_inside_a_fence(tmp_path):
    """A shell reads whatever the daemon's OS user can — it cannot honor a read fence, so a
    run that carries one must not get it. Decided by the values on the run, not by a mode."""
    import asyncio

    from exec_tool import ExecTool

    set_run_context(
        RunContext(agent_id="a", session_key="s", mode="chat", read_roots=(str(tmp_path),))
    )
    result = asyncio.run(
        ExecTool(SimpleNamespace(workspace=str(tmp_path))).execute(
            "t1", {"command": "echo leak"}, asyncio.Event()
        )
    )
    assert result.is_error and "not available" in result.content[0].text


# ── the private-tool map: location is identity, ids can collide ────────────────────────


def test_two_agents_with_one_id_get_their_own_tools_not_each_others(tmp_path):
    from agent_runtime.application.services.agent_service import AgentService

    x_dir = tmp_path / "accounts" / "acct_x" / "agents" / "helper"
    y_dir = tmp_path / "accounts" / "acct_y" / "agents" / "helper"
    x_dir.mkdir(parents=True)
    y_dir.mkdir(parents=True)
    x_tool = SimpleNamespace(name="x_secret_tool")
    y_tool = SimpleNamespace(name="y_secret_tool")
    service = AgentService(
        engine=None,
        tools=[],
        registry=SimpleNamespace(get=lambda _id: (_ for _ in ()).throw(KeyError(_id))),
        make_session=lambda sid, agent: None,
        build_prompt=lambda *a, **k: "",
        agent_tools={
            agent_dir_key(x_dir): [x_tool],
            agent_dir_key(y_dir): [y_tool],
        },
    )
    x_spec = SimpleNamespace(id="helper", dir=str(x_dir), tools_allow=None, tools_deny=())
    y_spec = SimpleNamespace(id="helper", dir=str(y_dir), tools_allow=None, tools_deny=())
    assert [t.name for t in service._private_for(x_spec)] == ["x_secret_tool"]
    assert [t.name for t in service._private_for(y_spec)] == ["y_secret_tool"]
    # a spec with no dir (minimal stand-in) simply has no private tools
    assert service._private_for(SimpleNamespace(id="helper")) == []
