"""SqliteTaskStore — durable TaskStore backed by a single SQLite file.

One DB at `<state_dir>/autonomy.sqlite`, every row keyed by `agent_id`. Scheduled
jobs persist here so they survive a gateway restart (the scheduler reads `due()` on
each tick + after boot). This is the "build local-first" backend; a cloud store would
implement the same TaskStore port and swap in by config.
"""

from __future__ import annotations

import sqlite3
import time
import uuid
from pathlib import Path

from agent_runtime.domain.autonomy import Goal, RunRecord, ScheduledTask
from agent_runtime.domain.commitment import Commitment
from agent_runtime.domain.notify import Notification
from agent_runtime.infrastructure import accounts
from agent_runtime.infrastructure.autonomy.schedule import next_due_after

_SCHEMA = """
CREATE TABLE IF NOT EXISTS tasks (
  id            TEXT PRIMARY KEY,
  agent_id      TEXT NOT NULL,
  session_key   TEXT NOT NULL,
  kind          TEXT NOT NULL,          -- 'at' | 'every' | 'cron'
  payload       TEXT NOT NULL,
  next_due      REAL NOT NULL,
  every_seconds REAL,
  cron_expr     TEXT,                   -- kind='cron'
  tz            TEXT,                    -- IANA timezone for the cron expr
  enabled       INTEGER NOT NULL DEFAULT 1,
  created_at    REAL NOT NULL DEFAULT 0,
  delivery      TEXT NOT NULL DEFAULT 'run',  -- 'run' | 'message'
  failure_alert INTEGER NOT NULL DEFAULT 0    -- notify after N consecutive failed runs (0=off)
);
CREATE INDEX IF NOT EXISTS idx_tasks_due ON tasks(enabled, next_due);
CREATE TABLE IF NOT EXISTS goals (
  id           TEXT PRIMARY KEY,
  agent_id     TEXT NOT NULL,
  session_key  TEXT NOT NULL,
  objective    TEXT NOT NULL,
  token_budget INTEGER,
  status       TEXT NOT NULL DEFAULT 'active',
  created_at   REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_goals_session ON goals(session_key, status);
CREATE TABLE IF NOT EXISTS runs (
  id          TEXT PRIMARY KEY,
  task_id     TEXT NOT NULL,
  agent_id    TEXT NOT NULL,
  started_at  REAL NOT NULL,
  finished_at REAL,
  status      TEXT NOT NULL DEFAULT 'running',  -- running | ok | blocked | failed | error | aborted
  outcome     TEXT,                             -- agent-declared: done | blocked | failed
  detail      TEXT NOT NULL DEFAULT ''          -- one-line reason
);
CREATE INDEX IF NOT EXISTS idx_runs_started ON runs(started_at);
CREATE TABLE IF NOT EXISTS notifications (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL,
  kind        TEXT NOT NULL,                    -- blocked | failed | info
  text        TEXT NOT NULL,
  detail      TEXT NOT NULL DEFAULT '',
  created_at  REAL NOT NULL DEFAULT 0,
  read        INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_notif_read ON notifications(read, created_at);
CREATE TABLE IF NOT EXISTS commitments (
  id          TEXT PRIMARY KEY,
  agent_id    TEXT NOT NULL,
  text        TEXT NOT NULL,
  due_at      REAL,
  status      TEXT NOT NULL DEFAULT 'open',     -- open | done | dropped
  created_at  REAL NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_commit_agent ON commitments(agent_id, status);
"""

_RUN_COLS = (
    "id",
    "task_id",
    "agent_id",
    "started_at",
    "finished_at",
    "status",
    "outcome",
    "detail",
)
_NOTIF_COLS = ("id", "agent_id", "kind", "text", "detail", "created_at", "read")

_COLS = (
    "id",
    "agent_id",
    "session_key",
    "kind",
    "payload",
    "next_due",
    "every_seconds",
    "cron_expr",
    "tz",
    "enabled",
    "created_at",
    "delivery",
    "failure_alert",
    "account_id",
)
_GOAL_COLS = ("id", "agent_id", "session_key", "objective", "token_budget", "status", "created_at")


def _row_to_task(row) -> ScheduledTask:
    d = dict(zip(_COLS, row))
    return ScheduledTask(
        id=d["id"],
        agent_id=d["agent_id"],
        session_key=d["session_key"],
        kind=d["kind"],
        payload=d["payload"],
        next_due=d["next_due"],
        every_seconds=d["every_seconds"],
        cron_expr=d.get("cron_expr"),
        tz=d.get("tz"),
        enabled=bool(d["enabled"]),
        created_at=d["created_at"],
        delivery=d.get("delivery", "run"),
        failure_alert=int(d.get("failure_alert") or 0),
        account_id=str(d.get("account_id") or ""),
    )


def _row_to_goal(row) -> Goal:
    d = dict(zip(_GOAL_COLS, row))
    return Goal(
        id=d["id"],
        agent_id=d["agent_id"],
        session_key=d["session_key"],
        objective=d["objective"],
        token_budget=d["token_budget"],
        status=d["status"],
        created_at=d["created_at"],
    )


class SqliteTaskStore:
    def __init__(self, path):
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(self._path))
        self._db.executescript(_SCHEMA)
        # migrate older DBs that predate newer columns (each ALTER is a no-op if present)
        for table, col, ddl in (
            ("tasks", "delivery", "TEXT NOT NULL DEFAULT 'run'"),
            ("tasks", "cron_expr", "TEXT"),
            ("tasks", "tz", "TEXT"),
            ("tasks", "failure_alert", "INTEGER NOT NULL DEFAULT 0"),
            ("tasks", "account_id", "TEXT NOT NULL DEFAULT ''"),
            ("runs", "outcome", "TEXT"),  # agent-declared outcome
            ("runs", "detail", "TEXT NOT NULL DEFAULT ''"),  # one-line reason
        ):
            try:
                self._db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError:
                pass
        self._db.commit()

    def add(self, task: ScheduledTask) -> str:
        task_id = task.id or uuid.uuid4().hex[:12]
        # OWNERSHIP IS STAMPED HERE, not at the call sites. There are three of them today
        # (the gateway's schedule RPC and two in the autonomy cron tool) and there will be more;
        # a per-caller stamp is one forgotten line away from a task that runs as nobody, on the
        # shared state, forever. add() always runs in the creating connection's context, so the
        # contextvar is the right answer and there is exactly one place to get it wrong.
        # An explicit account on the task wins (restore/import paths).
        account_id = task.account_id or (accounts.account_id() or "")
        self._db.execute(
            "INSERT OR REPLACE INTO tasks "
            "(id, agent_id, session_key, kind, payload, next_due, every_seconds, "
            " cron_expr, tz, enabled, created_at, delivery, failure_alert, account_id) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                task_id,
                task.agent_id,
                task.session_key,
                task.kind,
                task.payload,
                task.next_due,
                task.every_seconds,
                task.cron_expr,
                task.tz,
                int(task.enabled),
                task.created_at,
                task.delivery,
                int(task.failure_alert),
                account_id,
            ),
        )
        self._db.commit()
        return task_id

    def get(self, task_id: str) -> ScheduledTask | None:
        row = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM tasks WHERE id=?", (task_id,)
        ).fetchone()
        return _row_to_task(row) if row else None

    def update(self, task_id: str, **fields) -> bool:
        allowed = (
            "payload",
            "next_due",
            "every_seconds",
            "cron_expr",
            "tz",
            "kind",
            "enabled",
            "delivery",
        )
        sets = {k: (int(v) if k == "enabled" else v) for k, v in fields.items() if k in allowed}
        if not sets:
            return False
        cols = ", ".join(f"{k}=?" for k in sets)
        cur = self._db.execute(f"UPDATE tasks SET {cols} WHERE id=?", (*sets.values(), task_id))
        self._db.commit()
        return cur.rowcount > 0

    def list(self, agent_id: str | None = None) -> list[ScheduledTask]:
        """This caller's tasks. Scoped to the CURRENT account whenever one is set, so a hosted
        user's schedule list is theirs alone; unscoped (every task) on desktop, unchanged."""
        where, params = [], []
        if agent_id is not None:
            where.append("agent_id=?")
            params.append(agent_id)
        account_id = accounts.account_id()
        if account_id:
            where.append("account_id=?")
            params.append(account_id)
        clause = f" WHERE {' AND '.join(where)}" if where else ""
        rows = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM tasks{clause} ORDER BY created_at DESC", params
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def remove(self, task_id: str) -> bool:
        cur = self._db.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        self._db.commit()
        return cur.rowcount > 0

    def purge_agent(self, agent_id: str) -> dict:
        """Delete every row belonging to one agent across all ledgers (tasks, goals,
        runs, notifications, commitments). Returns per-table counts. Used when an agent
        is deleted, so no orphaned cron job can fire after its definition is gone."""
        counts: dict[str, int] = {}
        for table in ("tasks", "goals", "runs", "notifications", "commitments"):
            cur = self._db.execute(f"DELETE FROM {table} WHERE agent_id=?", (agent_id,))
            counts[table] = cur.rowcount
        self._db.commit()
        return counts

    def due(self, now: float) -> list[ScheduledTask]:
        rows = self._db.execute(
            f"SELECT {','.join(_COLS)} FROM tasks WHERE enabled=1 AND next_due<=? "
            "ORDER BY next_due ASC",
            (now,),
        ).fetchall()
        return [_row_to_task(r) for r in rows]

    def advance(self, task_id: str, now: float) -> None:
        task = self.get(task_id)
        if task is None:
            return
        nd = next_due_after(task, now)  # cron expr / interval -> next fire; one-shot -> None
        if nd is None:
            self._db.execute("UPDATE tasks SET enabled=0 WHERE id=?", (task_id,))
        else:
            self._db.execute("UPDATE tasks SET next_due=?, enabled=1 WHERE id=?", (nd, task_id))
        self._db.commit()

    # ---- GoalStore ----------------------------------------------------------

    def create_goal(self, goal: Goal) -> str:
        goal_id = goal.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO goals "
            "(id, agent_id, session_key, objective, token_budget, status, created_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (
                goal_id,
                goal.agent_id,
                goal.session_key,
                goal.objective,
                goal.token_budget,
                goal.status,
                goal.created_at,
            ),
        )
        self._db.commit()
        return goal_id

    def active_goal(self, session_key: str) -> Goal | None:
        row = self._db.execute(
            f"SELECT {','.join(_GOAL_COLS)} FROM goals WHERE session_key=? AND status='active' "
            "ORDER BY created_at DESC LIMIT 1",
            (session_key,),
        ).fetchone()
        return _row_to_goal(row) if row else None

    def update_goal(self, goal_id: str, status: str) -> bool:
        cur = self._db.execute("UPDATE goals SET status=? WHERE id=?", (status, goal_id))
        self._db.commit()
        return cur.rowcount > 0

    # ---- run history (audit) ------------------------------------------------

    def record_run(self, task_id: str, agent_id: str) -> str:
        run_id = uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT INTO runs (id, task_id, agent_id, started_at, status) VALUES (?,?,?,?,'running')",
            (run_id, task_id, agent_id, time.time()),
        )
        self._db.commit()
        return run_id

    def finish_run(
        self, run_id: str, status: str, outcome: str | None = None, detail: str = ""
    ) -> None:
        self._db.execute(
            "UPDATE runs SET finished_at=?, status=?, outcome=?, detail=? WHERE id=?",
            (time.time(), status, outcome, detail or "", run_id),
        )
        self._db.commit()

    def consecutive_failures(self, task_id: str) -> int:
        """How many of this task's MOST-RECENT finished runs went wrong in a row (failed/
        error/aborted/incomplete) — for failure-alert escalation. Stops at the first
        ok/blocked run. `incomplete` counts so a job that never declares an outcome can't
        spam forever — it auto-pauses like any other repeatedly-broken job."""
        rows = self._db.execute(
            "SELECT status FROM runs WHERE task_id=? AND finished_at IS NOT NULL "
            "ORDER BY started_at DESC LIMIT 50",
            (task_id,),
        ).fetchall()
        n = 0
        for (status,) in rows:
            if status in ("failed", "error", "aborted", "incomplete"):
                n += 1
            else:
                break
        return n

    def recent_runs(
        self, agent_id: str | None = None, task_id: str | None = None, limit: int = 20
    ) -> list[RunRecord]:
        where, args = [], []
        if agent_id:
            where.append("agent_id=?")
            args.append(agent_id)
        if task_id:
            where.append("task_id=?")
            args.append(task_id)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.execute(
            f"SELECT {','.join(_RUN_COLS)} FROM runs{clause} ORDER BY started_at DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [RunRecord(*r) for r in rows]

    # ---- NotifyStore (outbound user notifications, Phase 5a) ----------------

    def save(self, n: Notification) -> str:
        notif_id = n.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO notifications "
            "(id, agent_id, kind, text, detail, created_at, read) VALUES (?,?,?,?,?,?,?)",
            (
                notif_id,
                n.agent_id,
                n.kind,
                n.text,
                n.detail,
                n.created_at or time.time(),
                int(n.read),
            ),
        )
        self._db.commit()
        return notif_id

    def notifications(
        self, agent_id: str | None = None, unread_only: bool = False, limit: int = 50
    ) -> list[Notification]:
        where, args = [], []
        if agent_id:
            where.append("agent_id=?")
            args.append(agent_id)
        if unread_only:
            where.append("read=0")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.execute(
            f"SELECT {','.join(_NOTIF_COLS)} FROM notifications{clause} "
            "ORDER BY created_at DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [
            Notification(
                id=r[0],
                agent_id=r[1],
                kind=r[2],
                text=r[3],
                detail=r[4],
                created_at=r[5],
                read=bool(r[6]),
            )
            for r in rows
        ]

    def ack(self, notif_id: str) -> bool:
        cur = self._db.execute("UPDATE notifications SET read=1 WHERE id=?", (notif_id,))
        self._db.commit()
        return cur.rowcount > 0

    # ---- commitments (open loops / follow-ups, S15) -------------------------

    def add_commitment(self, c: Commitment) -> str:
        cid = c.id or uuid.uuid4().hex[:12]
        self._db.execute(
            "INSERT OR REPLACE INTO commitments (id, agent_id, text, due_at, status, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (cid, c.agent_id, c.text, c.due_at, c.status, c.created_at or time.time()),
        )
        self._db.commit()
        return cid

    def commitments(
        self, agent_id: str | None = None, open_only: bool = True, limit: int = 50
    ) -> list[Commitment]:
        where, args = [], []
        if agent_id:
            where.append("agent_id=?")
            args.append(agent_id)
        if open_only:
            where.append("status='open'")
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        rows = self._db.execute(
            "SELECT id, agent_id, text, due_at, status, created_at FROM commitments"
            f"{clause} ORDER BY (due_at IS NULL), due_at ASC, created_at DESC LIMIT ?",
            (*args, limit),
        ).fetchall()
        return [
            Commitment(id=r[0], agent_id=r[1], text=r[2], due_at=r[3], status=r[4], created_at=r[5])
            for r in rows
        ]

    def resolve_commitment(self, commitment_id: str, status: str) -> bool:
        cur = self._db.execute(
            "UPDATE commitments SET status=? WHERE id=?", (status, commitment_id)
        )
        self._db.commit()
        return cur.rowcount > 0

    def close(self) -> None:
        try:
            self._db.close()
        except Exception:  # noqa: BLE001
            pass
