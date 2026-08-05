"""Allowlist scrubbing — the gate that makes it impossible to leak content into telemetry.

This is an ALLOWLIST, not a denylist, and that is the whole point. A denylist ("strip anything
called password") fails the first time someone invents a new field name. An allowlist fails
closed: a field nobody has explicitly permitted simply never leaves the process.

What must never appear in a metric or log line: message text, tool arguments, tool output,
file paths, workspace contents, provider keys, session tokens. Telemetry is metadata only.
That discipline is the same one the trust-boundary design applies to model calls, and it
matters more here because these lines leave the user's machine.

Extend ALLOWED deliberately, one field at a time, with a reason.
"""

from __future__ import annotations

import os

#: Fields permitted on a telemetry record. Identifiers and bounded facts only.
ALLOWED: set[str] = {
    # correlation — the tracking number and its family
    "run_id", "turn_id", "parent_run_id", "trace_id", "request_id",
    # who / what — identifiers, never names or content
    "account_id", "agent_id", "creator_id", "agent_version", "plugin_id",
    # classification — bounded vocabularies
    "service", "version", "env", "trigger", "source", "outcome", "reason",
    "model", "model_tier", "provider", "funding_source", "credit_class", "stop_reason",
    # measurements
    "in_tokens", "out_tokens", "cached_tokens", "cost_usd", "credits",
    "duration_ms", "model_ms", "tool_ms", "turns", "status_code", "attempt",
    # names that are unbounded but SAFE (they are identifiers, not user content).
    # These live in properties, never in dimensions — see emf.metric_record.
    "tool", "event", "message",
    # The ONE deliberate exception to "identifiers only": text a third-party plugin printed to
    # stdout inside our process. It is admitted here because the alternative is worse, not
    # because it is safe — without capture that same text reaches CloudWatch RAW, having never
    # passed through this module at all (the allowlist was never in `print`'s path). By the time
    # it arrives it has been secret-masked, line-capped and size-capped by
    # agent_runtime/infrastructure/tools/sandbox/stdout_capture.py, and it is tagged with the
    # plugin that produced it. Never populate this field from our own code.
    "plugin_output",
}

_EXTRA = {
    f.strip()
    for f in os.environ.get("AGENTD_TELEMETRY_EXTRA_FIELDS", "").split(",")
    if f.strip()
}
ALLOWED |= _EXTRA

#: Belt and braces: even an allowed field is truncated, so a "reason" carrying a stack trace
#: or a model's error text can't turn a log line into a payload.
MAX_VALUE_CHARS = 200


def scrub(fields: dict) -> dict:
    """Drop anything not explicitly allowed; truncate what remains."""
    out: dict = {}
    for key, value in fields.items():
        if key not in ALLOWED or value is None:
            continue
        if isinstance(value, str) and len(value) > MAX_VALUE_CHARS:
            value = value[:MAX_VALUE_CHARS] + "…"
        out[key] = value
    return out
