"""Canonical identities for hosted state. Slugs and client session IDs are not owners."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from agent_runtime.infrastructure import accounts

_owner_resolver = None
_hosted = False


def configure(*, hosted: bool, owner_resolver=None) -> None:
    global _owner_resolver, _hosted
    _hosted = hosted
    _owner_resolver = owner_resolver


def namespace(agent_id: str) -> str:
    """Resolve the definition namespace using the authenticated registry view."""
    if _owner_resolver is not None:
        owner = str(_owner_resolver(agent_id) or "")
        if owner.startswith("org_"):
            if owner not in accounts.org_ids():
                raise PermissionError("organization membership required")
            return owner
        if owner.startswith("acct_"):
            if owner != accounts.account_id():
                raise PermissionError("agent belongs to another account")
            return "personal"
        return "platform"
    return "personal"


def partition(agent_id: str) -> str:
    acct = accounts.account_id()
    if acct:
        if not _hosted:
            return f"{acct}::{agent_id}"
        return json.dumps([acct, namespace(agent_id), agent_id], separators=(",", ":"))
    if _hosted:
        raise PermissionError("authenticated account required")
    return agent_id


def namespace_root(state_dir, account_id: str) -> Path:
    from agent_runtime.infrastructure.user_state import _safe, account_root

    return account_root(state_dir, account_id) / "runtime" / _safe(accounts.current_namespace.get() or "personal")


def runtime_root(state_dir, account_id: str, agent_id: str) -> Path:
    from agent_runtime.infrastructure.user_state import _safe, account_root

    return account_root(state_dir, account_id) / "runtime" / _safe(namespace(agent_id)) / "agents" / _safe(agent_id)


def session_workspace(state_dir, account_id: str, agent_id: str, session_key: str) -> Path:
    digest = hashlib.sha256(str(session_key).encode()).hexdigest()
    return runtime_root(state_dir, account_id, agent_id) / "workspaces" / digest


def session_partition(agent_id: str) -> str:
    key = partition(agent_id)
    if not _hosted:
        return key
    from agent_runtime.application.run_context import current_run_context
    ctx = current_run_context()
    return key + "::chat:" + hashlib.sha256(str(getattr(ctx, "session_key", "") or "").encode()).hexdigest()
