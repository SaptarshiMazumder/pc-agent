"""SqliteResourceStore — the durable resource index (one row per agent file).

One sqlite file (`<state_dir>/resources.sqlite`), rows keyed by (agent_id, rel_path).
Caches the cheap change-signature + description so descriptions survive restarts and are
not recomputed every turn. Behind the ResourceStore port, so a different index backend
can replace it without touching the manager or tools.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

from agentd.domain.resource import Resource

_COLS = ("rel_path", "kind", "size", "sig", "description", "updated_at")


class SqliteResourceStore:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS resources ("
            " agent_id TEXT NOT NULL, rel_path TEXT NOT NULL, kind TEXT NOT NULL,"
            " size INTEGER NOT NULL, sig TEXT NOT NULL DEFAULT '',"
            " description TEXT NOT NULL DEFAULT '', updated_at REAL NOT NULL DEFAULT 0,"
            " PRIMARY KEY (agent_id, rel_path))")
        self._db.commit()

    def _row(self, r) -> Resource:
        return Resource(rel_path=r[0], kind=r[1], size=r[2], sig=r[3],
                        description=r[4], updated_at=r[5])

    def get(self, agent_id: str, rel_path: str) -> Resource | None:
        row = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM resources WHERE agent_id=? AND rel_path=?",
            (agent_id, rel_path)).fetchone()
        return self._row(row) if row else None

    def put(self, agent_id: str, res: Resource) -> None:
        self._db.execute(
            "INSERT OR REPLACE INTO resources"
            " (agent_id, rel_path, kind, size, sig, description, updated_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (agent_id, res.rel_path, res.kind, res.size, res.sig,
             res.description, res.updated_at or time.time()))
        self._db.commit()

    def delete(self, agent_id: str, rel_path: str) -> bool:
        cur = self._db.execute(
            "DELETE FROM resources WHERE agent_id=? AND rel_path=?", (agent_id, rel_path))
        self._db.commit()
        return cur.rowcount > 0

    def list(self, agent_id: str) -> list[Resource]:
        rows = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM resources WHERE agent_id=? ORDER BY rel_path",
            (agent_id,)).fetchall()
        return [self._row(r) for r in rows]

    def purge_agent(self, agent_id: str) -> int:
        cur = self._db.execute("DELETE FROM resources WHERE agent_id=?", (agent_id,))
        self._db.commit()
        return cur.rowcount

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass
