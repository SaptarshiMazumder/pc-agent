"""Per-account state roots (M2) — route a user's chats + files under their OWN subtree.

When an account is active (hosted/accounts mode), that account's transcripts and workspace live
under ``<state_dir>/accounts/<account_id>/agents/<agent_id>/`` — the SAME single-user layout, one
level down. So plain local/desktop use is just the degenerate one-account case, and desktop paths
never change: callers fold this in ONLY when ``accounts.account_id()`` is set, otherwise they use
the agent's own dirs exactly as before.

Deliberately NOT covered here (bigger, separate changes): cron/notify (single sqlite files
partitioned by an ``agent_id`` column). Semantic memory IS partitioned, via
``accounts.memory_partition``.

INSTALLED AGENTS ARE NOW PARTITIONED TOO (``account_agents_dir`` / ``account_plugins_dir``).
The original note here said "the agent CATALOG stays shared", and that was right while the only
agents were the ones WE shipped: a shared catalogue of curated agents is a feature. It stopped
being right the moment the marketplace let a user install one, because installs went to the
daemon-global ``agents_dir`` — so on a hosted daemon one visitor's install appeared for everyone,
and their uninstall removed it from everyone.

The split is now: the CURATED catalogue (whatever the deployment ships in ``config.agents_dir``)
stays shared and read-only, and each account gets an OVERLAY of the agents it installed itself.
FileAgentRegistry unions the two per connection, overlay winning on an id collision, so a user who
installs their own build of a curated agent gets theirs without affecting anybody else's.
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


def account_agents_dir(state_dir, account_id: str) -> Path:
    """Agents this account INSTALLED itself: ``<state_dir>/accounts/<acct>/installed/agents``.

    An overlay on the deployment's curated ``config.agents_dir``, not a replacement — see the
    module docstring. Named ``installed/`` rather than ``agents/`` on purpose: ``agents/`` next to
    it already means "this account's per-agent DATA" (transcripts, workspace), and two sibling
    directories called agents, one holding definitions and one holding data, is a trap for whoever
    reads this next.
    """
    return account_root(state_dir, account_id) / "installed" / "agents"


def account_plugins_dir(state_dir, account_id: str) -> Path:
    """Plugins vendored by this account's installs. Same overlay idea as ``account_agents_dir``.

    Per-account rather than shared because a bundle carries its own plugins: two accounts on
    different versions of the same agent would otherwise fight over one plugin directory, and the
    loser would be whoever installed first.
    """
    return account_root(state_dir, account_id) / "installed" / "plugins"
