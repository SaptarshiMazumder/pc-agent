"""SqliteMemoryBank — durable, keyword-searchable long-term memory (Phase 3 / S4).

One sqlite file (`<state_dir>/memory.sqlite`), rows keyed by agent_id. FTS5 powers search;
if FTS5 isn't compiled in, it falls back to a LIKE scan so it works everywhere. Embeddings /
semantic search can later be added behind the same `MemoryBank` port without touching callers.
"""

from __future__ import annotations

import re
import sqlite3
import time
import uuid
from pathlib import Path

from agentd.domain.memory import MemoryItem

_COLS = ("id", "agent_id", "source", "text", "created_at")


def _fts_query(q: str) -> str:
    """Safe FTS5 query: OR the bare terms (quoted) so punctuation can't break the match."""
    terms = [re.sub(r'"', "", t) for t in re.split(r"\s+", q.strip()) if t]
    return " OR ".join(f'"{t}"' for t in terms) or '""'


class SqliteMemoryBank:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS memory ("
            " id TEXT PRIMARY KEY, agent_id TEXT NOT NULL, source TEXT NOT NULL,"
            " text TEXT NOT NULL, created_at REAL NOT NULL DEFAULT 0)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_mem_agent ON memory(agent_id, created_at)")
        self._fts = self._init_fts()
        self._db.commit()

    def _init_fts(self) -> bool:
        try:
            self._db.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5("
                "text, content='memory', content_rowid='rowid')")
            self._db.execute(
                "CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN"
                " INSERT INTO memory_fts(rowid, text) VALUES (new.rowid, new.text); END")
            self._db.execute(
                "CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN"
                " INSERT INTO memory_fts(memory_fts, rowid, text) VALUES('delete', old.rowid, old.text); END")
            return True
        except sqlite3.OperationalError:
            return False

    def _row(self, r) -> MemoryItem:
        return MemoryItem(**dict(zip(_COLS, r)))

    def save(self, item: MemoryItem) -> str:
        mid = item.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO memory (id, agent_id, source, text, created_at) VALUES (?,?,?,?,?)",
            (mid, item.agent_id, item.source, item.text, item.created_at or time.time()))
        self._db.commit()
        return mid

    def search(self, agent_id, query, limit=5) -> list[MemoryItem]:
        query = (query or "").strip()
        if not query:
            return []
        cols = ",".join("m." + c for c in _COLS)
        agent_sql = " AND m.agent_id=?" if agent_id else ""
        if self._fts:
            sql = (f"SELECT {cols} FROM memory_fts f JOIN memory m ON m.rowid=f.rowid "
                   f"WHERE memory_fts MATCH ?{agent_sql} ORDER BY rank LIMIT ?")
            params = [_fts_query(query)] + ([agent_id] if agent_id else []) + [limit]
            try:
                return [self._row(r) for r in self._db.execute(sql, params).fetchall()]
            except sqlite3.OperationalError:
                pass  # fall through to LIKE
        terms = [t for t in re.split(r"\s+", query) if t]
        like = " AND ".join("m.text LIKE ?" for _ in terms) or "1"
        sql = (f"SELECT {cols} FROM memory m WHERE ({like}){agent_sql} "
               "ORDER BY m.created_at DESC LIMIT ?")
        params = [f"%{t}%" for t in terms] + ([agent_id] if agent_id else []) + [limit]
        return [self._row(r) for r in self._db.execute(sql, params).fetchall()]

    def get(self, item_id) -> MemoryItem | None:
        row = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM memory WHERE id=?", (item_id,)).fetchone()
        return self._row(row) if row else None

    def recent(self, agent_id=None, limit=20) -> list[MemoryItem]:
        if agent_id:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM memory WHERE agent_id=? "
                "ORDER BY created_at DESC LIMIT ?", (agent_id, limit)).fetchall()
        else:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM memory ORDER BY created_at DESC LIMIT ?",
                (limit,)).fetchall()
        return [self._row(r) for r in rows]

    def delete(self, item_id) -> bool:
        cur = self._db.execute("DELETE FROM memory WHERE id=?", (item_id,))
        self._db.commit()
        return cur.rowcount > 0

    def purge_agent(self, agent_id: str) -> int:
        """Delete ALL of one agent's memory (FTS kept in sync by the delete trigger).
        Returns the number of rows removed. Used when an agent is deleted."""
        cur = self._db.execute("DELETE FROM memory WHERE agent_id=?", (agent_id,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass
