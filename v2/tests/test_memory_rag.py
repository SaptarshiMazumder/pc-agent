"""Semantic memory (RAG): vector search + recall tracking + dreaming consolidation + auto-recall.

Uses a deterministic one-hot embedder (no network) so cosine ranking is exact and offline.
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agentd.domain.agent import AgentSpec
from agentd.domain.memory import MemoryItem
from agentd.infrastructure.embeddings import build_embed_fn, cosine
from agentd.infrastructure.memory.bank import SqliteMemoryBank
from agentd.infrastructure.memory.dreaming import dream

_VOCAB = ["login", "expires", "weeks", "lives", "tokyo", "prefers", "dark",
          "mode", "concise", "emails", "cat", "dog"]


def _embed(texts):
    out = []
    for t in texts:
        words = set(t.lower().split())
        out.append([1.0 if w in words else 0.0 for w in _VOCAB])
    return out


class _Cfg:
    memory_dreaming_min_score = 0.0
    memory_dreaming_min_recall_count = 2
    memory_dreaming_min_unique_queries = 2
    memory_dreaming_recency_half_life_days = 14.0
    memory_dreaming_max_age_days = 30
    memory_dreaming_merge_threshold = 0.9


# ---- embeddings util --------------------------------------------------------

def test_build_embed_fn_none_without_model():
    assert build_embed_fn("") is None and build_embed_fn(None) is None


def test_cosine_basic():
    assert cosine([1, 0], [1, 0]) == pytest.approx(1.0)
    assert cosine([1, 0], [0, 1]) == pytest.approx(0.0)
    assert cosine([0, 0], [1, 1]) == 0.0                 # degenerate -> 0, no div-by-zero


# ---- vector search ----------------------------------------------------------

def test_vector_search_ranks_by_cosine(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    bank.save(MemoryItem(id="2", agent_id="A", source="note", text="lives tokyo"))
    bank.save(MemoryItem(id="3", agent_id="A", source="note", text="prefers dark mode"))
    hits = bank.search("A", "login expires", limit=2)
    assert hits[0].id == "1"                              # closest by cosine, not keyword rank
    bank.close()


def test_vector_search_agent_scoped(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="a", agent_id="A", source="note", text="prefers dark mode"))
    bank.save(MemoryItem(id="b", agent_id="B", source="note", text="prefers dark mode"))
    assert [h.id for h in bank.search("A", "dark mode")] == ["a"]   # only A's row
    bank.close()


def test_falls_back_to_keyword_when_no_embeddings(tmp_path):
    # notes saved WITHOUT an embedder (embedding NULL); a later embed_fn still recalls them via keyword
    plain = SqliteMemoryBank(tmp_path / "m.sqlite")
    plain.save(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    plain.close()
    reopened = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    assert reopened.search("A", "login expires")[0].id == "1"      # vector path empty -> keyword
    reopened.close()


# ---- recall tracking --------------------------------------------------------

def test_recall_records_ledger_and_bumps_count(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    bank.search("A", "login expires")                    # a recall
    bank.search("A", "when does login expire weeks")     # another, different query
    aggs = bank.recall_aggregates("A")
    assert aggs["1"].count == 2 and aggs["1"].unique_queries == 2
    assert [r for r in bank.rows_for("A") if r.id == "1"][0].recall_count == 2
    bank.close()


def test_search_record_false_skips_ledger(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    bank.search("A", "login expires", record=False)
    assert bank.recall_aggregates("A") == {}
    bank.close()


# ---- dreaming ---------------------------------------------------------------

def test_dreaming_promotes_frequently_recalled(tmp_path):
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    bank.search("A", "login expires")
    bank.search("A", "weeks login")                      # 2 recalls, 2 unique queries
    out = dream(bank, "A", _Cfg(), now=None)
    assert out["promoted"] == 1
    assert [r for r in bank.rows_for("A") if r.id == "1"][0].tier == "long"
    bank.close()


def test_dreaming_merges_near_duplicates(tmp_path):
    now = 1_000_000_000.0
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="prefers dark mode", created_at=now - 10))
    bank.save(MemoryItem(id="2", agent_id="A", source="note", text="prefers dark mode", created_at=now - 5))
    out = dream(bank, "A", _Cfg(), now=now)   # fresh notes -> merge only, no stale-forget
    assert out["merged"] == 1
    survivors = [r.id for r in bank.rows_for("A")]
    assert survivors == ["2"]                            # kept the newer of the near-dup pair
    bank.close()


def test_dreaming_forgets_stale_never_recalled(tmp_path):
    now = 1_000_000_000.0
    old = now - 40 * 86400                               # 40 days old, past 30-day max_age
    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    bank.save(MemoryItem(id="1", agent_id="A", source="note", text="cat", created_at=old))
    bank.save(MemoryItem(id="2", agent_id="A", source="note", text="dog", created_at=now))  # fresh, kept
    out = dream(bank, "A", _Cfg(), now=now)
    assert out["forgotten"] == 1
    assert {r.id for r in bank.rows_for("A")} == {"2"}
    bank.close()


# ---- background write (embed off the turn) ----------------------------------

@pytest.mark.asyncio
async def test_background_embedder_fills_vector(tmp_path):
    from agentd.infrastructure.memory.background import BackgroundEmbedder

    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    mid = bank.save_pending(MemoryItem(id="1", agent_id="A", source="note", text="login expires weeks"))
    # right after the instant write: fact stored, but NO vector yet -> vector search misses it
    assert [r.embedding for r in bank.rows_for("A")] == [None]
    emb = BackgroundEmbedder(bank)
    emb.schedule(mid, "login expires weeks")
    await emb.drain()                                    # let the background embed complete
    assert [r for r in bank.rows_for("A")][0].embedding is not None
    assert bank.search("A", "login expires")[0].id == "1"   # now vector-searchable
    bank.close()


@pytest.mark.asyncio
async def test_remember_tool_defers_embed_but_stays_recallable(tmp_path):
    from agentd.application.run_context import RunContext, set_run_context
    from agentd.infrastructure.memory.background import BackgroundEmbedder
    from memory_tools import RememberTool

    bank = SqliteMemoryBank(tmp_path / "m.sqlite", embed_fn=_embed)
    emb = BackgroundEmbedder(bank)
    set_run_context(RunContext("A", "s", "interactive"))
    r = await RememberTool(bank, emb).execute("c", {"text": "prefers dark mode"}, asyncio.Event())
    assert r.is_error is False                            # returns immediately (embed not awaited)
    assert bank.rows_for("A")[0].embedding is None        # vector not filled yet
    await emb.drain()
    assert bank.rows_for("A")[0].embedding is not None     # background embed landed
    bank.close()


def test_remember_tool_without_embedder_saves_sync(tmp_path):
    # no embedder passed -> falls back to the synchronous save (keyword-only deployments/tests)
    from agentd.application.run_context import RunContext, set_run_context
    from memory_tools import RememberTool

    bank = SqliteMemoryBank(tmp_path / "m.sqlite")       # no embed_fn
    set_run_context(RunContext("A", "s", "interactive"))
    res = asyncio.run(RememberTool(bank).execute("c", {"text": "lives tokyo"}, asyncio.Event()))
    assert res.is_error is False and bank.search("A", "tokyo")[0].text == "lives tokyo"
    bank.close()


# ---- auto-recall (agent_service seam) ---------------------------------------

def _spec():
    return AgentSpec(id="main", name="main", workspace=Path("."), state_dir=Path("."),
                     tools_allow=None, tools_deny=())


class _FakeEngine:
    def __init__(self):
        self.system_prompt = None

    async def run(self, *, messages, system_prompt, tools, on_event, abort, session, model):
        self.system_prompt = system_prompt


class _FakeSession:
    def load(self):
        return []

    def append(self, _m):
        pass


class _Reg:
    def __init__(self, spec):
        self._s = spec

    def get(self, _aid):
        return self._s

    def resolve(self, _k):
        return self._s


def _svc(engine, recall):
    from agentd.application.services.agent_service import AgentService
    return AgentService(engine=engine, tools=[], registry=_Reg(_spec()),
                        make_session=lambda *a, **k: _FakeSession(),
                        build_prompt=lambda *a, **k: "BASE", recall=recall)


@pytest.mark.asyncio
async def test_auto_recall_prepends_on_interactive_turn():
    from agentd.domain.agent import RunMode

    eng = _FakeEngine()
    svc = _svc(eng, recall=lambda agent, q: "## Relevant memories\n- prior fact")
    await svc.handle_message("s", "hi", on_event=None, abort=asyncio.Event(),
                             mode=RunMode.INTERACTIVE)
    assert eng.system_prompt.startswith("## Relevant memories")
    assert "BASE" in eng.system_prompt


@pytest.mark.asyncio
async def test_auto_recall_skipped_on_heartbeat_turn():
    from agentd.domain.agent import RunMode

    eng = _FakeEngine()
    svc = _svc(eng, recall=lambda agent, q: "## Relevant memories\n- prior fact")
    await svc.handle_message("s", "hi", on_event=None, abort=asyncio.Event(),
                             mode=RunMode.HEARTBEAT)
    assert eng.system_prompt == "BASE"                   # non-user turn: no recall injection


@pytest.mark.asyncio
async def test_auto_recall_failopen_on_embed_error():
    from agentd.domain.agent import RunMode

    def _boom(agent, q):
        raise RuntimeError("embedder down")

    eng = _FakeEngine()
    svc = _svc(eng, recall=_boom)
    await svc.handle_message("s", "hi", on_event=None, abort=asyncio.Event(),
                             mode=RunMode.INTERACTIVE)
    assert eng.system_prompt == "BASE"                   # recall raised -> turn proceeds unchanged
