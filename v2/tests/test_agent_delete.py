"""Deleting an agent purges everything it owns: definition + workspace + sessions
(registry), and every row in the shared ledgers — memory + cron/goals/runs/notifs/
commitments. Refuses 'main'. The gateway orchestrates all of it for any client."""

import sys
import time
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.autonomy import ScheduledTask
from agentd.domain.memory import MemoryItem
from agentd.infrastructure.agents.file_registry import FileAgentRegistry
from agentd.infrastructure.memory.bank import SqliteMemoryBank
from agentd.infrastructure.tasks.sqlite_store import SqliteTaskStore
from agentd.presentation.gateway import Gateway

# ---- store-level purges ----------------------------------------------------


def test_memory_purge_agent_only_targets_that_agent(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "memory.sqlite")
    bank.save(
        MemoryItem(id="", agent_id="scout", source="note", text="alpha", created_at=time.time())
    )
    bank.save(
        MemoryItem(id="", agent_id="scout", source="note", text="beta", created_at=time.time())
    )
    bank.save(
        MemoryItem(id="", agent_id="main", source="note", text="keep me", created_at=time.time())
    )

    n = bank.purge_agent("scout")
    assert n == 2
    assert bank.recent("scout") == []
    assert len(bank.recent("main")) == 1  # other agents untouched
    bank.close()


def test_task_store_purge_agent_clears_all_ledgers(tmp_path):
    store = SqliteTaskStore(tmp_path / "autonomy.sqlite")
    store.add(
        ScheduledTask(
            id="t1",
            agent_id="scout",
            session_key="agent:scout:x:y",
            kind="every",
            payload="ping",
            next_due=time.time(),
            every_seconds=60,
        )
    )
    store.add(
        ScheduledTask(
            id="t2",
            agent_id="main",
            session_key="main",
            kind="every",
            payload="ping",
            next_due=time.time(),
            every_seconds=60,
        )
    )
    rid = store.record_run("t1", "scout")
    store.finish_run(rid, "ok")

    counts = store.purge_agent("scout")
    assert counts["tasks"] == 1 and counts["runs"] == 1
    assert [t.id for t in store.list(None)] == ["t2"]  # main's job survives
    store.close()


# ---- registry: definition + sessions, refuse main -------------------------


def _registry(tmp_path):
    agents_dir = tmp_path / "agents"
    (agents_dir / "scout").mkdir(parents=True)
    (agents_dir / "scout" / "agent.toml").write_text('name = "Scout"\n', encoding="utf-8")
    cfg = SimpleNamespace(
        agents_dir=agents_dir,
        state_dir=tmp_path / ".agentd",
        workspace=tmp_path / "ws",
        agent_name="main",
    )
    return FileAgentRegistry(cfg), agents_dir


def test_registry_remove_deletes_dir_and_forgets(tmp_path):
    reg, agents_dir = _registry(tmp_path)
    assert "scout" in reg.list_ids()
    out = reg.remove("scout")
    assert out["definition"] is True
    assert not (agents_dir / "scout").exists()  # definition + workspace gone
    assert "scout" not in reg.list_ids()  # forgotten without a restart


def test_registry_remove_refuses_main(tmp_path):
    reg, _ = _registry(tmp_path)
    try:
        reg.remove("main")
        assert False, "should refuse"
    except ValueError:
        pass
    assert "main" in reg.list_ids()


def test_registry_remove_unknown_raises(tmp_path):
    reg, _ = _registry(tmp_path)
    try:
        reg.remove("nope")
        assert False, "should raise"
    except KeyError:
        pass


# ---- gateway orchestration -------------------------------------------------


class _Bank:
    def __init__(self):
        self.purged = None

    def purge_agent(self, agent_id):
        self.purged = agent_id
        return 3


class _Store:
    def __init__(self):
        self.purged = None

    def purge_agent(self, agent_id):
        self.purged = agent_id
        return {"tasks": 1, "goals": 0, "runs": 2, "notifications": 0, "commitments": 0}


class _Reg:
    def __init__(self):
        self.removed = None

    def list_ids(self):
        return ["main", "scout"]

    def remove(self, agent_id):
        self.removed = agent_id
        return {"id": agent_id, "definition": True, "sessions": True}


def _gw(reg, store, bank):
    return Gateway(
        config=SimpleNamespace(agent_id="main", agent_name="main"),
        service=None,
        registry=reg,
        task_store=store,
        memory_bank=bank,
    )


def test_gateway_remove_orchestrates_all_purges(tmp_path):
    reg, store, bank = _Reg(), _Store(), _Bank()
    out = _gw(reg, store, bank)._agents_remove({"agentId": "scout"})
    assert out["removed"] is True
    assert reg.removed == "scout" and store.purged == "scout" and bank.purged == "scout"
    assert out["memory"] == 3 and out["cron"]["tasks"] == 1


def test_gateway_remove_refuses_main():
    out = _gw(_Reg(), _Store(), _Bank())._agents_remove({"agentId": "main"})
    assert out["removed"] is False and "main" in out["error"]


def test_gateway_remove_unknown_agent():
    out = _gw(_Reg(), _Store(), _Bank())._agents_remove({"agentId": "ghost"})
    assert out["removed"] is False and "unknown" in out["error"]
