"""S14–S18: failure-alert escalation · commitments · auth-profile rotation · sandbox seam
· agent version."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from commitment_tool import CommitmentTool

from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.domain.auth_profile import AuthProfile
from agent_runtime.domain.autonomy import ScheduledTask
from agent_runtime.domain.commitment import Commitment
from agent_runtime.infrastructure.auth import SqliteAuthProfileStore
from agent_runtime.infrastructure.tasks import SqliteTaskStore

# ---- S14: failure-alert (consecutive failures) ------------------------------


def test_consecutive_failures_and_alert_field(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add(
        ScheduledTask(
            id="j",
            agent_id="main",
            session_key="agent:main:cron",
            kind="every",
            payload="x",
            next_due=0.0,
            every_seconds=60.0,
            failure_alert=2,
        )
    )
    assert store.get("j").failure_alert == 2  # field round-trips
    store.finish_run(store.record_run("j", "main"), "ok")
    store.finish_run(store.record_run("j", "main"), "error")
    store.finish_run(store.record_run("j", "main"), "error")
    assert store.consecutive_failures("j") == 2  # 2 errors in a row (newest-first)
    store.finish_run(store.record_run("j", "main"), "ok")
    assert store.consecutive_failures("j") == 0  # a success resets the streak
    store.close()


# ---- S15: commitments -------------------------------------------------------


@pytest.mark.asyncio
async def test_commitments_tool_roundtrip_scoped(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    tool = CommitmentTool(store)
    set_run_context(RunContext("scout", "s", "interactive"))
    r = await tool.execute("c", {"action": "add", "text": "follow up with Jane"}, asyncio.Event())
    assert r.is_error is False
    cid = r.details["id"]
    assert (
        "follow up with Jane"
        in (await tool.execute("c", {"action": "list"}, asyncio.Event())).content[0].text
    )
    await tool.execute("c", {"action": "done", "id": cid}, asyncio.Event())
    assert (
        "no open commitments"
        in (await tool.execute("c", {"action": "list"}, asyncio.Event())).content[0].text
    )
    assert store.commitments("other") == []  # scoped to the calling agent
    store.close()


def test_commitment_store_due_ordering(tmp_path):
    store = SqliteTaskStore(tmp_path / "a.sqlite")
    store.add_commitment(Commitment(id="x", agent_id="a", text="later", due_at=2000.0))
    store.add_commitment(Commitment(id="y", agent_id="a", text="soon", due_at=1000.0))
    store.add_commitment(Commitment(id="z", agent_id="a", text="someday"))  # no due
    assert [c.id for c in store.commitments("a")] == ["y", "x", "z"]  # due first, then undated
    store.close()


# ---- S16: auth-profile rotation + cooldown ----------------------------------


def test_auth_rotation_and_cooldown(tmp_path):
    s = SqliteAuthProfileStore(tmp_path / "auth.sqlite")
    s.add(AuthProfile(id="a", provider="google", label="work"))
    s.add(AuthProfile(id="b", provider="google", label="personal"))
    now = 1000.0
    p1 = s.available("google", now)
    s.record_success(p1.id, now)
    p2 = s.available("google", now + 1)
    assert p2.id != p1.id  # LRU rotation -> the other one
    s.record_failure(p2.id, now + 1, cooldown_sec=100)
    assert s.available("google", now + 2).id == p1.id  # p2 cooled down -> back to p1
    assert s.available("google", now + 200) is not None  # p2 cooldown expired -> both eligible
    assert s.available("nope", now) is None
    s.close()


# ---- S17: sandbox seam ------------------------------------------------------


def test_build_sandbox_defaults_local():
    from agent_runtime.infrastructure.sandbox import LocalSandbox, build_sandbox

    assert isinstance(build_sandbox(SimpleNamespace(sandbox="")), LocalSandbox)
    assert isinstance(build_sandbox(SimpleNamespace(sandbox="docker")), LocalSandbox)  # fallback


@pytest.mark.asyncio
async def test_local_sandbox_runs_a_command():
    from agent_runtime.infrastructure.sandbox import LocalSandbox

    try:
        code, out = await LocalSandbox().run("echo sandbox-ok")
    except NotImplementedError:
        pytest.skip("asyncio subprocess not supported on this event loop")
    assert code == 0 and "sandbox-ok" in out


# ---- S18: agent version -----------------------------------------------------


def test_agent_version_from_toml(tmp_path):
    from agent_runtime.infrastructure.agents import FileAgentRegistry

    agents = tmp_path / "agents"
    (agents / "scout").mkdir(parents=True)
    (agents / "scout" / "agent.toml").write_text(
        'name = "Scout"\nversion = "2.1"\n', encoding="utf-8"
    )
    (agents / "scout" / "IDENTITY.md").write_text("# Scout", encoding="utf-8")
    cfg = SimpleNamespace(
        agents_dir=str(agents),
        state_dir=tmp_path / ".agentd",
        workspace=tmp_path,
        agent_name="main",
        model="m",
        agent_id="main",
    )
    reg = FileAgentRegistry(cfg)
    assert reg.get("scout").version == "2.1"
    assert reg.get("main").version == "1"  # default when unset
