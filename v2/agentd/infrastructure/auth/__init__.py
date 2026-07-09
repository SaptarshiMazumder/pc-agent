"""SqliteAuthProfileStore — durable multi-account credential rotation (S16).

One sqlite file (`<state_dir>/auth.sqlite`). `available()` does least-recently-used
rotation over the enabled, not-cooled profiles for a provider; failures cool a profile down
(so traffic shifts to a healthy one) and successes clear it. NOTE: this is the selection
state + rotation logic — wiring it into the actual MCP/model-provider auth is the next step.
"""

from __future__ import annotations

import sqlite3
import uuid
from pathlib import Path

from agentd.domain.auth_profile import AuthProfile

_COLS = ("id", "provider", "label", "enabled", "cooldown_until", "failures", "last_used")


class SqliteAuthProfileStore:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path))
        self._db.execute(
            "CREATE TABLE IF NOT EXISTS auth_profiles ("
            " id TEXT PRIMARY KEY, provider TEXT NOT NULL, label TEXT NOT NULL,"
            " enabled INTEGER NOT NULL DEFAULT 1, cooldown_until REAL NOT NULL DEFAULT 0,"
            " failures INTEGER NOT NULL DEFAULT 0, last_used REAL NOT NULL DEFAULT 0)"
        )
        self._db.commit()

    def _row(self, r) -> AuthProfile:
        d = dict(zip(_COLS, r))
        return AuthProfile(
            id=d["id"],
            provider=d["provider"],
            label=d["label"],
            enabled=bool(d["enabled"]),
            cooldown_until=d["cooldown_until"],
            failures=d["failures"],
            last_used=d["last_used"],
        )

    def add(self, profile: AuthProfile) -> str:
        pid = profile.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO auth_profiles "
            "(id, provider, label, enabled, cooldown_until, failures, last_used) VALUES (?,?,?,?,?,?,?)",
            (
                pid,
                profile.provider,
                profile.label,
                int(profile.enabled),
                profile.cooldown_until,
                profile.failures,
                profile.last_used,
            ),
        )
        self._db.commit()
        return pid

    def available(self, provider: str, now: float) -> AuthProfile | None:
        row = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM auth_profiles "
            "WHERE provider=? AND enabled=1 AND cooldown_until<=? "
            "ORDER BY last_used ASC LIMIT 1",
            (provider, now),
        ).fetchone()
        return self._row(row) if row else None

    def record_success(self, profile_id: str, now: float) -> None:
        self._db.execute(
            "UPDATE auth_profiles SET last_used=?, failures=0, cooldown_until=0 WHERE id=?",
            (now, profile_id),
        )
        self._db.commit()

    def record_failure(self, profile_id: str, now: float, cooldown_sec: float) -> None:
        self._db.execute(
            "UPDATE auth_profiles SET failures=failures+1, last_used=?, cooldown_until=? WHERE id=?",
            (now, now + cooldown_sec, profile_id),
        )
        self._db.commit()

    def list(self, provider: str | None = None) -> list[AuthProfile]:
        if provider:
            rows = self._db.execute(
                f"SELECT {','.join(_COLS)} FROM auth_profiles WHERE provider=?", (provider,)
            ).fetchall()
        else:
            rows = self._db.execute(f"SELECT {','.join(_COLS)} FROM auth_profiles").fetchall()
        return [self._row(r) for r in rows]

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass
