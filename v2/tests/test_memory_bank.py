"""S4/S5 — long-term memory: the SQLite bank (FTS/LIKE search, agent-scoped) + the
remember/memory_search/memory_get tools."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from memory_tools import (
    MemoryGetTool,
    MemorySearchTool,
    RememberTool,
)

from agentd.application.run_context import RunContext, set_run_context
from agentd.domain.memory import MemoryItem
from agentd.infrastructure.memory.bank import SqliteMemoryBank

# ---- bank (S4) --------------------------------------------------------------


def test_bank_save_search_get(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite")
    mid = bank.save(
        MemoryItem(
            id="",
            agent_id="scout",
            source="note",
            text="The SUUMO login expires after about two weeks",
        )
    )
    hits = bank.search("scout", "login expires")
    assert hits and hits[0].id == mid and "expires" in hits[0].text
    assert bank.get(mid).agent_id == "scout"
    assert bank.search("scout", "") == []  # empty query -> nothing
    bank.close()


def test_bank_is_agent_scoped(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite")
    bank.save(MemoryItem(id="a", agent_id="A", source="note", text="alpha fact apple"))
    bank.save(MemoryItem(id="b", agent_id="B", source="note", text="beta fact apple"))
    assert {i.id for i in bank.search("A", "apple")} == {"a"}  # only A's
    assert {i.id for i in bank.search(None, "apple")} == {"a", "b"}  # all agents
    assert len(bank.recent("B")) == 1
    bank.close()


def test_bank_persists_across_reopen(tmp_path):
    p = tmp_path / "m.sqlite"
    SqliteMemoryBank(p).save(
        MemoryItem(id="x", agent_id="A", source="note", text="durable across runs")
    )
    reopened = SqliteMemoryBank(p)
    assert reopened.search("A", "durable")[0].id == "x"  # survived a restart
    reopened.close()


def test_consolidate_dedups_per_agent(tmp_path):
    # S6 — collapse exact-duplicate notes (case/space-insensitive), per agent, keep newest
    from agentd.infrastructure.memory.consolidate import consolidate

    bank = SqliteMemoryBank(tmp_path / "m.sqlite")
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="prefers dark mode"))
    bank.save(MemoryItem(id="2", agent_id="A", source="note", text="Prefers   dark mode  "))  # dup
    bank.save(MemoryItem(id="3", agent_id="A", source="note", text="lives in Tokyo"))
    bank.save(
        MemoryItem(id="4", agent_id="B", source="note", text="prefers dark mode")
    )  # other agent
    assert consolidate(bank, "A") == 1  # one dup removed
    assert {" ".join(i.text.split()).lower() for i in bank.recent("A")} == {
        "prefers dark mode",
        "lives in tokyo",
    }
    assert len(bank.recent("B")) == 1  # B untouched
    bank.close()


# ---- tools (S5) -------------------------------------------------------------


@pytest.mark.asyncio
async def test_memory_tools_roundtrip_scoped_to_agent(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite")
    set_run_context(RunContext("scout", "s", "interactive"))
    r = await RememberTool(bank).execute(
        "c", {"text": "user prefers concise emails"}, asyncio.Event()
    )
    assert r.is_error is False
    mid = r.details["id"]

    s = await MemorySearchTool(bank).execute("c", {"query": "concise emails"}, asyncio.Event())
    assert "concise emails" in s.content[0].text and mid in s.content[0].text

    g = await MemoryGetTool(bank).execute("c", {"id": mid}, asyncio.Event())
    assert "concise emails" in g.content[0].text

    # a DIFFERENT agent doesn't see it
    set_run_context(RunContext("other", "s", "interactive"))
    s2 = await MemorySearchTool(bank).execute("c", {"query": "concise emails"}, asyncio.Event())
    assert "no matching memories" in s2.content[0].text
    bank.close()
