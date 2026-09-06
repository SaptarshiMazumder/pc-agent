"""Per-account config overlays — one daemon, one config per USER.

THE PROBLEM. ``config.set`` wrote the daemon's own ``config.json`` and then ``setattr``-ed the
result onto the live ``Config`` object. On a desktop that is exactly right: the person editing
settings owns the machine. On a hosted daemon it meant any signed-in tenant could change the
brain model, the fallbacks, the tool models and the enabled-tool set **for every other tenant, on
the next message, without a restart**. Storage was never the issue — the shared mutable object was.

THE SHAPE. Three layers, resolved per run:

    master config.json        the DEPLOYMENT's. Read-only wherever an account is present.
      ⊕ accounts/<acct>/config.json     that user's overlay — only the keys they changed
        ⊕ agents.<id> block             that user's per-agent overrides (domain/agent_config)

``effective(config)`` returns the merge for whoever the current connection belongs to, and the
account comes from the same contextvar every other per-account path already uses
(``accounts.current_account``, pinned for the life of a socket). **No account => the master
object itself, unchanged and not copied** — so signed-out desktop and the CLI keep the identical
object identity they had before this file existed. That is the property that makes this safe to
put on the hot path: the single-user case is not merely equivalent, it is the same object.

WHY AN OVERLAY OF CHANGED KEYS, not a copy of the master taken at signup: "seeded from the latest
master" then stays true forever. Raise a default and every user who never touched that key moves
with you; a snapshot would freeze each account on its registration date and could never be fixed
centrally again.

SECURITY PROPERTIES, each one deliberate:

* ``PER_USER_KEYS`` is an allowlist, and it is enforced on READ as well as on write. A hand-planted
  or corrupted overlay file cannot move a port, a path, a sandbox flag or the tenant root — the
  merge simply ignores those keys. Write-side validation alone would trust the filesystem.
* The master is never opened for writing on this path, and the live ``Config`` is never mutated.
  A user's Save cannot reach another user's run even transiently.
* Account ids are run through ``user_state._safe`` (they are ``acct_<hex>`` already; that is
  defence, not decoration), so an id can never escape the account root.
* A corrupt overlay degrades to "no overlay" — never to another account's values, and never to an
  exception on somebody's turn.
* The cache is keyed by account AND by the master it was derived from, so two accounts, or one
  account under two different masters (tests, a config reload), can never read each other's entry.
"""

from __future__ import annotations

import copy
import json
import logging
import time
from pathlib import Path

from agent_runtime.infrastructure import user_state

log = logging.getLogger("agentd")

# ---------------------------------------------------------------------------------------------
# What a USER may decide for themselves.
#
# The test for membership is "does this describe how MY agents behave?" — not "is it harmless".
# Everything absent here describes the MACHINE (ports, paths, storage roots, the sandbox, the
# distribution identity, hosted plumbing), and on a shared daemon those belong to whoever deployed
# it. `agents` is in, and it is the key that carries per-agent overrides — so a user's per-agent
# settings are per-user too, which is the whole point of letting agents override anything.
PER_USER_KEYS = frozenset(
    {
        # the brain
        "model",
        "model_fallbacks",
        "cost_efficiency",
        "reasoning_effort",
        "max_turns",
        "model_defaults",
        "model_catalog",
        "llm_idle_timeout_seconds",
        "run_idle_timeout_seconds",
        "llm_request_timeout_seconds",
        "execution_contract",
        "context_max_messages",
        # tools + plugins (this is where a tool's model lives: plugins.<p>.tools.<t>.model)
        "plugins",
        "tools_enabled",
        "tools_disabled",
        "tool_timeout_default",
        "tool_retries_default",
        "tool_loop_max_repeats_default",
        "tool_loop_warn_after_errors_default",
        "verify_tool",
        "completeness_check",
        "parallel_search_enabled",
        # behaviour the user owns
        "agent_name",
        "memory_enabled",
        "memory_auto_recall",
        "memory_auto_recall_limit",
        "subagents_enabled",
        "subagent_max",
        "subagent_max_depth",
        "agent_messaging_enabled",
        "skills_relevance_enabled",
        "skill_workshop",
        "mcp_workshop",
        "notify_enabled",
        "safe_to_send_check",
        "autonomy_enabled",
        "heartbeat_default_interval",
        "heartbeat_active_hours",
        "workspace_index_enabled",
        "resource_manager_enabled",
        "resource_vision_enabled",
        "resource_summarize_enabled",
        "scratch_ttl_hours",
        "google_account",
        # PER-AGENT overrides, {agent_id: {...}} — layered by domain/agent_config.resolve
        "agents",
    }
)

# Keys whose values are dicts and must be MERGED rather than replaced. Without this, a user who
# sets one tool's model would drop every other plugin entry the master defines — the settings page
# sends one key, not the whole tree.
_DEEP_KEYS = frozenset({"plugins", "agents", "cost_efficiency", "model_defaults"})

# How long a cached overlay is trusted before its file is stat()ed again. Writes through
# `write_overlay` invalidate immediately, so this only bounds staleness for a change made by
# ANOTHER process (a second daemon instance on the same EFS). Small enough to be invisible,
# large enough that a chatty tool-model lookup does not stat a network filesystem per call.
_TTL_SECONDS = 0.5

# account_id -> (master_key, mtime_ns, size, checked_at, effective_config)
_cache: dict[str, tuple[object, int, int, float, object]] = {}


def machine_only(keys) -> list[str]:
    """The subset of ``keys`` a user may NOT set. The gateway refuses these by name rather than
    dropping them silently: a Save that reports success and changes nothing is the worst outcome
    of the three."""
    return sorted(k for k in keys if k not in PER_USER_KEYS)


def overlay_path(config, account_id: str) -> Path:
    return user_state.account_config_file(config.state_dir, account_id)


def _master_key(config) -> object:
    """Identifies the master an entry was derived from. `config_path` alone is not enough — two
    tests build different Configs with the same (often empty) path — so the object's identity
    joins it. Cheap, and wrong-sharing here would be a cross-account read."""
    return (str(getattr(config, "config_path", "")), id(config))


def read_overlay(config, account_id: str) -> dict:
    """This account's stored overlay, ALREADY filtered to ``PER_USER_KEYS``.

    Filtering on read is the load-bearing half: it means the merge cannot be widened by anything
    that reaches the filesystem — a stale file written by an older build, a hand-edited volume, a
    restored backup. Missing or unreadable file => ``{}``.
    """
    path = overlay_path(config, account_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        # Never raise on a user's turn, and never fall back to anything but "no overrides".
        log.warning("account config: ignoring unreadable overlay %s (%s)", path, e)
        return {}
    if not isinstance(raw, dict):
        log.warning("account config: overlay %s is not a JSON object — ignoring", path)
        return {}
    return {k: v for k, v in raw.items() if k in PER_USER_KEYS}


def merge_value(key: str, base, over):
    """One key's merged value. Dict-valued keys merge one level deep (per-plugin, per-agent), so
    setting one entry never erases its siblings; everything else replaces wholesale.

    NULL MEANS REMOVE, and the merge is why it has to. Merging preserves every key the patch does
    not name — which is the point for siblings, and was a trap for the key you meant to CLEAR: a
    settings page that "hands a value back to the daemon" by omitting it wrote a patch that
    changed nothing, reported success, and left the override in place forever (found live: a
    per-agent model that could be set but never unset). Omission cannot mean deletion here
    without also meaning "erase every sibling", so deletion needs a value of its own, and `null`
    is the one JSON spelling that is unambiguous — no setting legitimately stores it.
    """
    if key in _DEEP_KEYS and isinstance(base, dict) and isinstance(over, dict):
        merged = dict(base)
        for k, v in over.items():
            cur = merged.get(k)
            if v is None:
                merged.pop(k, None)
            elif isinstance(cur, dict) and isinstance(v, dict):
                inner = {**cur, **v}
                for ik, iv in v.items():
                    if iv is None:
                        inner.pop(ik, None)
                merged[k] = inner
            else:
                merged[k] = v
        return merged
    return over


def apply_overlay(config, overlay: dict):
    """``config`` with ``overlay`` applied, as a NEW object. The input is never mutated — it is
    the daemon-wide instance every other connection is using."""
    if not overlay:
        return config
    out = copy.copy(config)  # shallow: we only ever REBIND attributes, never mutate them in place
    for key, value in overlay.items():
        if key not in PER_USER_KEYS:
            continue  # belt to read_overlay's braces: apply_overlay is called directly in tests
        try:
            setattr(out, key, merge_value(key, getattr(config, key, None), value))
        except Exception:  # noqa: BLE001 — a bad value in one key never breaks the whole config
            log.warning("account config: could not apply key %r — keeping the master value", key)
    return out


def for_account(config, account_id: str | None):
    """The effective config for a NAMED account (or the master, for ``None``). Cached."""
    if not account_id:
        return config
    key = _master_key(config)
    path = overlay_path(config, account_id)
    now = time.monotonic()
    hit = _cache.get(account_id)
    if hit is not None and hit[0] == key and (now - hit[3]) < _TTL_SECONDS:
        return hit[4]
    try:
        st = path.stat()
        mtime_ns, size = st.st_mtime_ns, st.st_size
    except OSError:
        mtime_ns, size = -1, -1  # no overlay file: still cached, so the miss is not re-stat'ed
    if hit is not None and hit[0] == key and hit[1] == mtime_ns and hit[2] == size:
        _cache[account_id] = (key, mtime_ns, size, now, hit[4])
        return hit[4]
    effective_cfg = apply_overlay(config, read_overlay(config, account_id))
    _cache[account_id] = (key, mtime_ns, size, now, effective_cfg)
    return effective_cfg


def current_account_id() -> str | None:
    """The account whose config applies to this connection — or ``None`` when the answer is "the
    machine's".

    BOTH halves are load-bearing. ``account_id()`` alone would be wrong on a DESKTOP that has
    signed into the platform: that connection carries an account, but the machine belongs to the
    person at the keyboard, and their Settings must keep writing the real config file and the real
    .env (their own provider keys!) exactly as before. ``enabled()`` is the daemon saying "every
    connection here must present an account" — which is only ever true where the machine is
    SHARED, and shared is the only place a per-account overlay is the right answer.
    """
    from agent_runtime.infrastructure import accounts

    return accounts.account_id() if accounts.enabled() else None


def effective(config):
    """The effective config for the CURRENT connection.

    THE hot-path entry point: called by ``tool_models`` on every brain-model and tool-model
    resolution. Anywhere the machine is one person's — desktop signed in or out, the CLI — it
    returns the master object itself: same identity, no copy, no stat, byte-for-byte the old path.
    """
    return for_account(config, current_account_id())


def write_overlay(config, account_id: str, patch: dict) -> tuple[bool, str]:
    """Merge ``patch`` into this account's overlay, preserving keys it does not name.

    Returns ``(ok, path)``. Refuses nothing here — the CALLER validates and reports, because a
    refusal a user can read ("ports are not yours to set") only exists at the API edge. What this
    does guarantee is that only ``PER_USER_KEYS`` are ever persisted, whatever it is handed.
    """
    path = overlay_path(config, account_id)
    clean = {k: v for k, v in patch.items() if k in PER_USER_KEYS}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(current, dict):
                current = {}
        except (FileNotFoundError, ValueError):
            current = {}
        except OSError:
            return False, str(path)
        for key, value in clean.items():
            current[key] = merge_value(key, current.get(key), value)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(current, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(path)  # atomic: a reader never sees a half-written config
    except OSError as e:
        log.warning("account config: could not write %s (%s)", path, e)
        return False, str(path)
    _cache.pop(account_id, None)  # this instance is authoritative immediately
    return True, str(path)


def clear_cache(account_id: str | None = None) -> None:
    """Drop cached overlays — one account's, or all of them."""
    if account_id is None:
        _cache.clear()
    else:
        _cache.pop(account_id, None)


def replace_overlay(config, account_id: str, document: dict) -> tuple[bool, str]:
    """Replace this account's overlay wholesale (the Advanced editor), filtered to
    ``PER_USER_KEYS``.

    The master equivalent of this operation rewrites the daemon's config file verbatim — which is
    precisely why an account may not have it. Here "replace" is bounded: whatever the document
    says about ports, paths or the sandbox is dropped, and the machine's own file is never opened.
    """
    path = overlay_path(config, account_id)
    clean = {k: v for k, v in (document or {}).items() if k in PER_USER_KEYS}
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(clean, indent=2, default=str) + "\n", encoding="utf-8")
        tmp.replace(path)
    except OSError as e:
        log.warning("account config: could not write %s (%s)", path, e)
        return False, str(path)
    _cache.pop(account_id, None)
    return True, str(path)
