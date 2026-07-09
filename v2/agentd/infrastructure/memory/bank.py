"""SqliteMemoryBank — durable long-term memory with optional semantic (vector) search.

One sqlite file (`<state_dir>/memory.sqlite`), rows keyed by agent_id. Two search modes behind
one `MemoryBank` port, chosen by whether an ``embed_fn`` is wired:

  * keyword (default): FTS5, or a LIKE scan where FTS5 isn't compiled in — works everywhere.
  * semantic (embed_fn set): notes are embedded on write; ``search`` embeds the query and ranks
    by cosine (brute-force over the agent's rows — fine to thousands; sqlite-vec is the drop-in
    upgrade behind this same method when a corpus outgrows it).

Callers never change: they call ``save`` / ``search`` / ``recent`` / ``get`` / ``delete`` as
before; the vector path is transparent. Extra bookkeeping powers "dreaming" (consolidation):
every recall bumps ``recall_count`` / ``last_recalled`` and appends to a ``memory_recalls``
ledger (query + score); a ``tier`` column tracks short- vs long-term. See ``dreaming.py``.
"""

from __future__ import annotations

import logging
import re
import sqlite3
import time
import uuid
from array import array
from collections import namedtuple
from pathlib import Path

from agentd.domain.memory import MemoryItem

log = logging.getLogger("agentd")

_COLS = ("id", "agent_id", "source", "text", "created_at")  # the MemoryItem projection (unchanged)

# a fuller row for consolidation/ranking (never leaves the infrastructure layer)
MemRow = namedtuple("MemRow", "id text created_at tier recall_count last_recalled embedding")
RecallAgg = namedtuple("RecallAgg", "count unique_queries unique_days max_score")


def _fts_query(q: str) -> str:
    """Safe FTS5 query: OR the bare terms (quoted) so punctuation can't break the match."""
    terms = [re.sub(r'"', "", t) for t in re.split(r"\s+", q.strip()) if t]
    return " OR ".join(f'"{t}"' for t in terms) or '""'


def _pack(vec) -> bytes:
    return array("f", vec).tobytes()


def _unpack(blob) -> list[float]:
    a = array("f")
    a.frombytes(blob)
    return a.tolist()


def _cosine(a, b) -> float:
    # local copy so the bank has no cross-layer import; identical to infrastructure.embeddings.cosine
    import math

    num = sum(x * y for x, y in zip(a, b))
    da = math.sqrt(sum(x * x for x in a))
    db = math.sqrt(sum(y * y for y in b))
    return num / (da * db) if da and db else 0.0


class SqliteMemoryBank:
    def __init__(self, path, embed_fn=None):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._embed_fn = embed_fn  # None => keyword-only
        self._db = sqlite3.connect(str(self._path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memory ("
            " id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, source TEXT NOT NULL,"
            " text TEXT NOT NULL, created_at REAL NOT NULL DEFAULT 0,"
            " embedding BLOB, tier TEXT NOT NULL DEFAULT 'short',"
            " recall_count INTEGER NOT NULL DEFAULT 0, last_recalled REAL NOT NULL DEFAULT 0)"
        )
        self._migrate()  # add new columns to a pre-existing DB
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_mem_agent ON memory(agent_id, created_at)")
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memory_recalls ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, memory_id TEXT NOT NULL, query TEXT NOT NULL,"
            " score REAL NOT NULL DEFAULT 0, created_at REAL NOT NULL DEFAULT 0)"
        )
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_recall_mem ON memory_recalls(memory_id)")
        self._fts = self._init_fts()
        self._db.commit()

    def _migrate(self) -> None:
        have = {r[1] for r in self._db.execute("PRAGMA table_info(memory)").fetchall()}
        for col, decl in (
            ("embedding", "BLOB"),
            ("tier", "TEXT NOT NULL DEFAULT 'short'"),
            ("recall_count", "INTEGER NOT NULL DEFAULT 0"),
            ("last_recalled", "REAL NOT NULL DEFAULT 0"),
        ):
            if col not in have:
                self._db.execute(f"ALTER TABLE memory ADD COLUMN {col} {decl}")

    def _init_fts(self) -> bool:
        try:
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "text, content='memory', content_rowid='rowid')"
            )
            self._db.execute(
                "CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN"
                " INSERT INTO memory_fts(rowid, text) VALUES (new.rowid, new.text); END"
            )
            self._db.execute(
                "CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN"
                " INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.rowid, old.text); END"
            )
            return True
        except sqlite3.OperationalError:
            return False

    def _row(self, r) -> MemoryItem:
        return MemoryItem(**dict(zip(_COLS, r)))

    # ---- write -------------------------------------------------------------

    def _embed(self, text: str):
        if self._embed_fn is None:
            return None
        try:
            return _pack(self._embed_fn([text])[0])
        except Exception as e:  # noqa: BLE001 — embeddings are best-effort; fall back to keyword
            log.warning("memory: embed failed, storing note without a vector: %s", e)
            return None

    @property
    def embedder_ready(self) -> bool:
        """True when semantic mode is live (an embedder is wired)."""
        return self._embed_fn is not None

    def save(self, item: MemoryItem, embed: bool = True) -> str:
        """Persist a note. ``embed`` embeds inline (default — direct/test callers get an
        immediately vector-searchable row). Pass ``embed=False`` for the fast path: the row lands
        with ``embedding=NULL`` and the vector is filled asynchronously (see ``save_pending`` +
        BackgroundEmbedder), so the caller never waits on the network."""
        mid = item.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO memory"
            " (id, agent_id, source, text, created_at, embedding, tier, recall_count, last_recalled)"
            " VALUES (?,?,?,?,?,?,'short',0,0)",
            (
                mid,
                item.agent_id,
                item.source,
                item.text,
                item.created_at or time.time(),
                self._embed(item.text) if embed else None,
            ),
        )
        self._db.commit()
        return mid

    def save_pending(self, item: MemoryItem) -> str:
        """Instant local write with no vector yet (``embedding=NULL``). Keyword-searchable at
        once; a BackgroundEmbedder fills the vector off the turn via ``store_embedding``."""
        return self.save(item, embed=False)

    def embed_vector(self, text: str):
        """The raw embedding of ``text`` (list[float]); raises if no embedder is wired. Called
        from a worker thread (asyncio.to_thread) — touches no DB, so it's thread-safe."""
        if self._embed_fn is None:
            raise RuntimeError("no embedder configured")
        return self._embed_fn([text])[0]

    def store_embedding(self, item_id: str, vec) -> None:
        """Fill in a row's vector after a background embed. Runs on the loop thread (same thread
        as the sqlite connection). Text is untouched, so FTS stays consistent (no trigger needed)."""
        self._db.execute("UPDATE memory SET embedding=? WHERE id=?", (_pack(vec), item_id))
        self._db.commit()

    # ---- read --------------------------------------------------------------

    def _vector_search(self, agent_id, query, limit):
        """Top-``limit`` (item, score) pairs by cosine over the agent's embedded rows. Returns
        [] on any embedding failure so ``search`` can fall through to keyword."""
        try:
            qv = self._embed_fn([query])[0]
        except Exception as e:  # noqa: BLE001
            log.warning("memory: query embed failed, using keyword search: %s", e)
            return []
        cols = ",".join(_COLS)
        rows = self._db.execute(
            f"SELECT {cols}, embedding FROM memory WHERE agent_id=? AND embedding IS NOT NULL",
            (agent_id,),
        ).fetchall()
        scored = [(self._row(r[: len(_COLS)]), _cosine(qv, _unpack(r[-1]))) for r in rows]
        scored.sort(key=lambda p: p[1], reverse=True)
        return scored[:limit]

    def search(self, agent_id, query, limit=5, min_score=0.0, record=True) -> list[MemoryItem]:
        """Recall the ``limit`` most relevant memories. Semantic when an embedder is wired (with
        an optional cosine ``min_score`` floor), else keyword. When ``record`` is set, each hit's
        recall is logged (bumps recall_count/last_recalled + appends to the ledger) so dreaming
        can later promote durably-useful notes — this is the signal auto-recall feeds."""
        query = (query or "").strip()
        if not query:
            return []
        if self._embed_fn is not None:
            scored = self._vector_search(agent_id, query, limit)
            if scored:
                hits = [(it, sc) for it, sc in scored if sc >= min_score]
                if record and hits:
                    self._record_recalls(query, hits)
                return [it for it, _ in hits]
            # no embedded rows yet (e.g. saved before embeddings were on) -> keyword fallback
        cols = ",".join("m." + c for c in _COLS)
        agent_sql = " AND m.agent_id=?" if agent_id else ""
        items: list[MemoryItem] = []
        if self._fts:
            sql = (
                f"SELECT {cols} FROM memory_fts f JOIN memory m ON m.rowid=f.rowid "
                f"WHERE memory_fts MATCH ?{agent_sql} ORDER BY rank LIMIT ?"
            )
            params = [_fts_query(query)] + ([agent_id] if agent_id else []) + [limit]
            try:
                items = [self._row(r) for r in self._db.execute(sql, params).fetchall()]
            except sqlite3.OperationalError:
                items = []
        if not items:
            terms = [t for t in re.split(r"\s+", query) if t]
            like = " AND ".join("m.text LIKE ?" for _ in terms) or "1"
            sql = (
                f"SELECT {cols} FROM memory m WHERE ({like}){agent_sql} "
                "ORDER BY m.created_at DESC LIMIT ?"
            )
            params = [f"%{t}%" for t in terms] + ([agent_id] if agent_id else []) + [limit]
            items = [self._row(r) for r in self._db.execute(sql, params).fetchall()]
        if record and items:
            self._record_recalls(query, [(it, 0.0) for it in items])
        return items

    def _record_recalls(self, query, hits) -> None:
        now = time.time()
        for item, score in hits:
            self._db.execute(
                "UPDATE memory SET recall_count = recall_count + 1, last_recalled=? WHERE id=?",
                (now, item.id),
            )
            self._db.execute(
                "INSERT INTO memory_recalls (memory_id, query, score, created_at) VALUES (?,?,?,?)",
                (item.id, query, float(score), now),
            )
        self._db.commit()

    def get(self, item_id) -> MemoryItem | None:
        row = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM memory WHERE id=?", (item_id,)
        ).fetchone()
        return self._row(row) if row else None

    def recent(self, agent_id=None, limit=20) -> list[MemoryItem]:
        if agent_id:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM memory WHERE agent_id=? "
                "ORDER BY created_at DESC LIMIT ?",
                (agent_id, limit),
            ).fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM memory ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [self._row(r) for r in rows]

    # ---- consolidation support (used by dreaming.py) -----------------------

    def agent_ids(self) -> list[str]:
        return [r[0] for r in self._db.execute("SELECT DISTINCT agent_id FROM memory").fetchall()]

    def rows_for(self, agent_id) -> list[MemRow]:
        """Full rows (with unpacked embeddings) for one agent — for merge/promote/decay passes."""
        out = []
        for r in self._db.execute(
            "SELECT id, text, created_at, tier, recall_count, last_recalled, embedding "
            "FROM memory WHERE agent_id=?",
            (agent_id,),
        ).fetchall():
            out.append(
                MemRow(
                    r[0], r[1], r[2], r[3], r[4], r[5], _unpack(r[6]) if r[6] is not None else None
                )
            )
        return out

    def recall_aggregates(self, agent_id) -> dict[str, RecallAgg]:
        """Per-memory recall stats from the ledger: total recalls, distinct queries, distinct
        days recalled, and the best cosine score seen — the inputs to promotion."""
        rows = self._db.execute(
            "SELECT r.memory_id, r.query, r.score, r.created_at FROM memory_recalls r "
            "JOIN memory m ON m.id = r.memory_id WHERE m.agent_id=?",
            (agent_id,),
        ).fetchall()
        buckets: dict[str, dict] = {}
        for mem_id, q, score, ts in rows:
            b = buckets.setdefault(mem_id, {"n": 0, "q": set(), "d": set(), "max": 0.0})
            b["n"] += 1
            b["q"].add((q or "").strip().lower())
            b["d"].add(int(ts // 86400))
            b["max"] = max(b["max"], float(score))
        return {
            k: RecallAgg(v["n"], len(v["q"]), len(v["d"]), v["max"]) for k, v in buckets.items()
        }

    def set_tier(self, item_id, tier) -> None:
        self._db.execute("UPDATE memory SET tier=? WHERE id=?", (tier, item_id))
        self._db.commit()

    def delete(self, item_id) -> bool:
        self._db.execute("DELETE FROM memory_recalls WHERE memory_id=?", (item_id,))
        cur = self._db.execute("DELETE FROM memory WHERE id=?", (item_id,))
        self._db.commit()
        return cur.rowcount > 0

    def purge_agent(self, agent_id: str) -> int:
        """Delete ALL of one agent's memory (FTS kept in sync by the delete trigger) plus its
        recall ledger rows. Returns the number of memory rows removed. Used when an agent is
        deleted."""
        ids = [
            r[0]
            for r in self._db.execute(
                "SELECT id FROM memory WHERE agent_id=?", (agent_id,)
            ).fetchall()
        ]
        if ids:
            self._db.executemany(
                "DELETE FROM memory_recalls WHERE memory_id=?", [(i,) for i in ids]
            )
        cur = self._db.execute("DELETE FROM memory WHERE agent_id=?", (agent_id,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass
