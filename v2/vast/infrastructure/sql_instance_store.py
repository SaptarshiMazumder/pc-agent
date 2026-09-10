"""The `vast_instances` table — one implementation of InstanceStore, on either engine.

WRITTEN ONCE FOR SQLITE AND POSTGRES, because the host passes a connection through its dialect
shim: `?` placeholders, rows indexed by column name. Only the schema differs between engines,
and that lives in the two schema modules beside this one.

THE ROW IS WRITTEN BEFORE THE RENTAL, NOT AFTER. `claim` inserts a `starting` row and returns its
id; only then does the service rent, passing that id as the marketplace label. Written the other
way round — rent, then record — a crash in between produces a GPU that is billing and that
nothing on earth knows about.
"""

from __future__ import annotations

import uuid
from typing import Any

from vast.domain.errors import SlotLost
from vast.domain.instance import LIVE_STATES, InstanceRow

_COLS = (
    "id, account_id, instance_id, machine_id, url, state, hourly_usd, "
    "created_at, last_seen_at, lease_until, dead_at, dead_reason"
)
_LIVE = ",".join("?" for _ in LIVE_STATES)


def _row(r: Any) -> InstanceRow:
    return InstanceRow(
        id=str(r["id"]),
        account_id=str(r["account_id"]),
        instance_id=int(r["instance_id"]) if r["instance_id"] is not None else None,
        machine_id=int(r["machine_id"]) if r["machine_id"] is not None else None,
        url=str(r["url"] or ""),
        state=str(r["state"]),
        hourly_usd=float(r["hourly_usd"] or 0.0),
        created_at=float(r["created_at"]),
        last_seen_at=float(r["last_seen_at"]),
        lease_until=float(r["lease_until"] or 0.0),
        dead_at=float(r["dead_at"]) if r["dead_at"] is not None else None,
        dead_reason=str(r["dead_reason"] or ""),
    )


class SqlInstanceStore:
    """Reads and writes for one table. A live connection per call — the caller owns the
    transaction, matching identity and payments."""

    # ------------------------------------------------------------------ reads

    def live_for(self, c: Any, account_id: str) -> InstanceRow | None:
        r = c.execute(
            f"SELECT {_COLS} FROM vast_instances "
            f"WHERE account_id=? AND state IN ({_LIVE})",
            (account_id, *LIVE_STATES),
        ).fetchone()
        return _row(r) if r else None

    def by_id(self, c: Any, row_id: str) -> InstanceRow | None:
        r = c.execute(f"SELECT {_COLS} FROM vast_instances WHERE id=?", (row_id,)).fetchone()
        return _row(r) if r else None

    def live_rows(self, c: Any) -> list[InstanceRow]:
        rows = c.execute(
            f"SELECT {_COLS} FROM vast_instances WHERE state IN ({_LIVE}) "
            "ORDER BY last_seen_at ASC",
            tuple(LIVE_STATES),
        ).fetchall()
        return [_row(r) for r in rows]

    def count_live(self, c: Any) -> int:
        """How many machines are running right now, across every account."""
        r = c.execute(
            f"SELECT COUNT(*) AS n FROM vast_instances WHERE state IN ({_LIVE})",
            tuple(LIVE_STATES),
        ).fetchone()
        return int(r["n"] or 0)

    def spend_since(self, c: Any, account_id: str, since: float, now: float) -> float:
        """Dollars this account has run up since `since`.

        COMPUTED FROM THE ROWS, not from a running total, because a counter can drift and these
        rows are the record anyway. A dead row costs its rate times the time it existed; a LIVE
        row is charged up to `now`, so an instance running right this second already counts
        against the cap rather than becoming visible only once it dies.

        `created_at` is used as the start — the row is written just before the rental — so the
        slow first boot is billed, which is correct: the marketplace charges for it too.
        """
        rows = c.execute(
            "SELECT hourly_usd, created_at, dead_at, state FROM vast_instances "
            "WHERE account_id=? AND created_at>=?",
            (account_id, since),
        ).fetchall()
        total = 0.0
        for r in rows:
            rate = float(r["hourly_usd"] or 0.0)
            if not rate:
                continue
            end = float(r["dead_at"]) if r["dead_at"] is not None else now
            total += rate * max(0.0, end - float(r["created_at"])) / 3600.0
        return total

    # ------------------------------------------------------------------ claim

    def claim(self, c: Any, account_id: str, now: float) -> tuple[InstanceRow, bool]:
        """Take the one live slot for this account, or report who already holds it.

        HOW THE RACE IS SETTLED. The insert carries its own guard (`WHERE NOT EXISTS`), which
        closes the ordinary case in a single statement. Two transactions can still pass that
        guard at once under READ COMMITTED, and there the partial unique index refuses one of
        them — so that failure is turned into a re-read, but ONLY after confirming a live row
        now exists. If none does, the original error propagates: a genuine database fault must
        never be laundered into "someone else won".
        """
        new_id = uuid.uuid4().hex
        try:
            c.execute(
                "INSERT INTO vast_instances "
                "  (id, account_id, state, hourly_usd, created_at, last_seen_at, lease_until) "
                "SELECT ?, ?, 'starting', 0, ?, ?, 0 "
                "WHERE NOT EXISTS ("
                f"  SELECT 1 FROM vast_instances WHERE account_id=? AND state IN ({_LIVE})"
                ")",
                (new_id, account_id, now, now, account_id, *LIVE_STATES),
            )
        except Exception:
            existing = self.live_for(c, account_id)
            if existing is None:
                raise
            return existing, False
        row = self.live_for(c, account_id)
        if row is None:
            raise SlotLost(f"the slot for {account_id} vanished immediately after being claimed")
        return row, row.id == new_id

    # ------------------------------------------------------------------ writes

    def mark_running(
        self, c: Any, row_id: str, *, instance_id: int, machine_id: int,
        hourly_usd: float, now: float,
    ) -> None:
        c.execute(
            "UPDATE vast_instances SET instance_id=?, machine_id=?, hourly_usd=?, "
            "last_seen_at=? WHERE id=?",
            (int(instance_id), int(machine_id), float(hourly_usd), now, row_id),
        )

    def mark_ready(self, c: Any, row_id: str, *, url: str, now: float) -> None:
        c.execute(
            "UPDATE vast_instances SET state='running', url=?, last_seen_at=? WHERE id=?",
            (url, now, row_id),
        )

    def heartbeat(self, c: Any, account_id: str, *, now: float, lease_until: float = 0.0) -> bool:
        """The lease moves FORWARD ONLY, in SQL rather than in a read-modify-write, so two
        concurrent heartbeats cannot lose one another's extension."""
        cur = c.execute(
            "UPDATE vast_instances SET last_seen_at=?, "
            "lease_until=CASE WHEN ?>lease_until THEN ? ELSE lease_until END "
            f"WHERE account_id=? AND state IN ({_LIVE})",
            (now, lease_until, lease_until, account_id, *LIVE_STATES),
        )
        return bool(getattr(cur, "rowcount", 0))

    def mark_dead(self, c: Any, row_id: str, *, reason: str, now: float) -> None:
        c.execute(
            "UPDATE vast_instances SET state='dead', dead_at=?, dead_reason=? WHERE id=?",
            (now, str(reason)[:200], row_id),
        )

    # ------------------------------------------------------------------ reaper liveness

    def record_sweep(self, c: Any, *, now: float) -> None:
        """Stamp that a sweep completed. One row, upserted — the history is not interesting,
        only whether the most recent one is recent."""
        c.execute(
            "INSERT INTO vast_reaper_state (id, last_sweep_at) VALUES (1, ?) "
            "ON CONFLICT (id) DO UPDATE SET last_sweep_at = EXCLUDED.last_sweep_at",
            (now,),
        )

    def last_sweep(self, c: Any) -> float:
        r = c.execute("SELECT last_sweep_at FROM vast_reaper_state WHERE id=1").fetchone()
        return float(r["last_sweep_at"]) if r else 0.0
