"""The parent<->child wire format for the subprocess sandbox. Pure functions, no I/O.

ONE JSON object goes in (the job, on the child's stdin), a stream of JSON LINES comes back (progress
updates, then exactly one result). It lives in its own module because both sides must agree on it and
only one side is ever under test at a time — a parent test can build a job and assert on a payload
without spawning anything, and the worker can be exercised with a hand-written job.

TWO THINGS DELIBERATELY DO NOT TRAVEL IN THE JOB:

  * ``grant.secrets`` — those are handed over as the child's ENVIRONMENT instead. The job payload is
    the thing a debug log or a crash dump is most likely to capture verbatim; the environment is not.
    Same values either way, different blast radius when something goes wrong.
  * The ``Config`` object — the child gets a REDACTED PROJECTION built by ``config_projection`` (an
    allowlist of harmless fields plus the plugin's own settings block). In-process, ``ctx.config`` is
    the live Config, which carries every provider key this daemon holds; handing that to untrusted
    code across a process boundary would make the boundary pointless.

``details`` is best-effort: a tool may attach arbitrary Python there, and arbitrary Python does not
cross a pipe. Anything not JSON-encodable is dropped with a marker rather than failing the call —
``details`` is out-of-band data for the client, never the tool's answer, so losing it degrades the
result instead of destroying it.
"""

from __future__ import annotations

import json
from typing import Any

from agent_runtime.application.interfaces.tool import ToolResult
from agent_runtime.domain.messages import content_from_dict, content_to_dict
from agent_runtime.domain.sandbox import CapabilityGrant

#: The allowlisted config fields the child may see. A SEED, not a hardcoding: an operator overrides
#: it with ``config.sandbox_config_fields``. Everything absent from this list raises AttributeError
#: in the child, so ``getattr(config, "openai_api_key", "")`` returns the caller's default and
#: ``config.openai_api_key`` fails loudly — a plugin cannot quietly read what it was not given.
DEFAULT_CONFIG_FIELDS: tuple[str, ...] = (
    "workspace",
    "log_level",
    "model_defaults",
    "model_catalog",
    "agent_name",
)


def config_projection(config, plugin_id: str, fields: tuple[str, ...] = ()) -> dict:
    """The subset of Config the child is allowed to see, as a plain dict.

    ``plugins`` is narrowed to THIS plugin's own entry: a plugin's settings are its own business,
    and the full map would name every other plugin installed for this account.
    """
    names = tuple(fields) or DEFAULT_CONFIG_FIELDS
    out: dict[str, Any] = {}
    for name in names:
        if not hasattr(config, name):
            continue
        value = getattr(config, name)
        try:
            json.dumps(value)
        except (TypeError, ValueError):
            continue  # not transportable => not projected (silently: it was never promised)
        out[name] = value
    own = (getattr(config, "plugins", None) or {}).get(plugin_id)
    out["plugins"] = {plugin_id: own} if isinstance(own, dict) else {}
    return out


def grant_payload(grant: CapabilityGrant) -> dict:
    """Everything about the grant EXCEPT secrets (those go in the environment)."""
    return {
        "fs_paths": list(grant.fs_paths),
        "net_allowlist": list(grant.net_allowlist),
        "cpu_ms": int(grant.cpu_ms),
        "mem_mb": int(grant.mem_mb),
        "timeout_s": float(grant.timeout_s),
    }


def ctx_payload(ctx) -> dict:
    """The run's identity, carried explicitly — contextvars do not survive a process boundary, so
    without this the child's work would show up in the logs as an orphan."""
    if ctx is None:
        return {}
    return {
        "agent_id": getattr(ctx, "agent_id", ""),
        "session_key": getattr(ctx, "session_key", ""),
        "mode": getattr(ctx, "mode", ""),
        "workspace": getattr(ctx, "workspace", ""),
        "run_id": getattr(ctx, "run_id", ""),
        "turn_id": getattr(ctx, "turn_id", ""),
    }


def result_payload(result: ToolResult, kind: str = "result") -> dict:
    """ToolResult -> a JSON-safe line the child writes."""
    payload: dict[str, Any] = {
        "t": kind,
        "content": [content_to_dict(b) for b in (result.content or [])],
        "isError": bool(result.is_error),
        "artifacts": list(result.artifacts or []),
    }
    if result.details is not None:
        try:
            json.dumps(result.details)
            payload["details"] = result.details
        except (TypeError, ValueError):
            payload["details"] = {"_dropped": "details were not JSON-encodable"}
    return payload


def payload_result(payload: dict) -> ToolResult:
    """The inverse — tolerant, because a malformed line from the child must surface as a failed
    tool call and not as an exception inside the engine."""
    blocks = []
    for raw in payload.get("content") or []:
        try:
            blocks.append(content_from_dict(raw))
        except (ValueError, AttributeError, TypeError):
            continue
    return ToolResult(
        content=blocks,
        details=payload.get("details"),
        is_error=bool(payload.get("isError")),
        artifacts=[str(a) for a in (payload.get("artifacts") or [])],
    )


def encode_job(job: dict) -> bytes:
    return (json.dumps(job) + "\n").encode("utf-8")


def decode_line(line: str) -> dict | None:
    """One protocol line -> its payload, or None if it is not one of ours (never raises)."""
    line = line.strip()
    if not line:
        return None
    try:
        payload = json.loads(line)
    except ValueError:
        return None
    return payload if isinstance(payload, dict) and payload.get("t") else None
