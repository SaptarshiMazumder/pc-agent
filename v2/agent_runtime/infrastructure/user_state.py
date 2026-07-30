"""Per-account state roots (M2) — route a user's chats + files under their OWN subtree.

When an account is active (hosted/accounts mode), that account's transcripts and workspace live
under ``<state_dir>/accounts/<account_id>/agents/<agent_id>/`` — the SAME single-user layout, one
level down. So plain local/desktop use is just the degenerate one-account case, and desktop paths
never change: callers fold this in ONLY when ``accounts.account_id()`` is set, otherwise they use
the agent's own dirs exactly as before.

Deliberately NOT covered here (bigger, separate changes): semantic memory + cron/notify (single
sqlite files partitioned by an ``agent_id`` column) and projects (rooted at the daemon state_dir).
The agent CATALOG stays shared — only a user's data (sessions, workspace) is partitioned.
"""

from __future__ import annotations

import re
from pathlib import Path

_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _safe(part: str) -> str:
    """Filesystem-safe path segment (account ids are ``acct_<hex>`` already; this is defensive)."""
    cleaned = _UNSAFE.sub("_", str(part or "")).strip("._")
    return cleaned or "unknown"


def account_root(state_dir, account_id: str) -> Path:
    """One account's root: ``<state_dir>/accounts/<acct>``. Per-agent data, projects.json, and
    project workspaces all hang under here (the whole single-user world, one level down)."""
    return Path(state_dir) / "accounts" / _safe(account_id)


def account_agent_dir(state_dir, account_id: str, agent_id: str | None) -> Path:
    """The per-account root for one agent's data: ``<state_dir>/accounts/<acct>/agents/<agent>``."""
    return account_root(state_dir, account_id) / "agents" / _safe(agent_id or "main")


def account_state_dir(state_dir, account_id: str, agent_id: str | None) -> Path:
    """Where this account's transcripts for ``agent_id`` live (SessionStore appends ``/sessions``)."""
    return account_agent_dir(state_dir, account_id, agent_id)


def account_workspace(state_dir, account_id: str, agent_id: str | None) -> Path:
    """Where this account's file/exec/shell tools for ``agent_id`` bind."""
    return account_agent_dir(state_dir, account_id, agent_id) / "workspace"
