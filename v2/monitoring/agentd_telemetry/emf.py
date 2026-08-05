"""The one place a metric or log line actually leaves the process: stdout.

WHY PRINTING. Every service already has its container stdout forwarded to CloudWatch by the
awslogs driver (infra/modules/services.tf). CloudWatch parses lines in Embedded Metric Format
(EMF) and extracts real metrics from them — so `print()` is a complete metrics pipeline with
zero new infrastructure, zero new failure modes, and no network call in the hot path.

That last property is the important one. A monitoring SERVICE that applications call adds a
dependency that fails exactly when you need it most: during an outage, the thing that reports
the outage is on the far side of it. Printing cannot fail, cannot block, and cannot be slow.

LOCAL DEV. Set AGENTD_TELEMETRY_FILE to ALSO append every line to a file, which
monitoring/dev_dashboard.py tails to render a live view on your laptop. Unset in production —
there, stdout is the only path.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time

_NAMESPACE = os.environ.get("AGENTD_TELEMETRY_NAMESPACE", "agentd").strip() or "agentd"
_SERVICE = os.environ.get("AGENTD_SERVICE", "").strip() or "unknown"
_VERSION = os.environ.get("AGENTD_VERSION", "").strip() or "dev"
_FILE = os.environ.get("AGENTD_TELEMETRY_FILE", "").strip()
_ENABLED = os.environ.get("AGENTD_TELEMETRY", "1").strip() not in ("0", "false", "no")

#: Dimension keys that ALARMS are allowed to bind to, coarsest first. See `_dimension_sets`.
#: Overridable because which keys are alarm-worthy is a deployment decision, not a code one.
_ROLLUP_KEYS = tuple(
    k.strip()
    for k in os.environ.get("AGENTD_TELEMETRY_ROLLUP_KEYS", "service,outcome").split(",")
    if k.strip()
)

# stdout is shared with normal logging; serialise so a metric line is never interleaved with
# a half-written log line (which would make the JSON unparseable at the other end).
_lock = threading.Lock()

#: When set, `emit` writes HERE instead of resolving `sys.stdout`.
#:
#: Exists for exactly one caller: the plugin stdout capture (agent_runtime .../stdout_capture.py)
#: redirects `sys.stdout` process-wide for the duration of an untrusted tool call, and
#: `contextlib.redirect_stdout` cannot distinguish the plugin's `print` from ours. Without this
#: pin, every metric emitted while a plugin tool ran would be swallowed into that plugin's capture
#: buffer and never reach CloudWatch — telemetry disappearing precisely during third-party code.
#: Default None keeps today's late-binding behaviour, which is what lets pytest's capsys see
#: emitted lines.
_direct_stream = None


def pin_stream(stream):
    """Point telemetry's own output at `stream` (None = resolve sys.stdout per write).

    Returns the PREVIOUS value so callers can restore rather than assume they were the outermost.
    """
    global _direct_stream
    previous, _direct_stream = _direct_stream, stream
    return previous


def service() -> str:
    return _SERVICE


def enabled() -> bool:
    return _ENABLED


def emit(record: dict) -> None:
    """Write one JSON line. Never raises — telemetry must not be able to break the caller."""
    if not _ENABLED:
        return
    try:
        line = json.dumps(record, separators=(",", ":"), default=str)
    except Exception:  # noqa: BLE001 — an unserialisable field must not kill a request
        return
    with _lock:
        try:
            stream = _direct_stream if _direct_stream is not None else sys.stdout
            stream.write(line + "\n")
            stream.flush()
        except Exception:  # noqa: BLE001 — a closed/broken stdout is not our problem to raise
            pass
        if _FILE:
            try:
                with open(_FILE, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # noqa: BLE001
                pass
    # THE MAIL PATH (5.1). Inert unless the machine is one whose stdout we cannot read — i.e. a
    # desktop that opted in. Deliberately OUTSIDE the stdout lock: the uploader only appends to a
    # bounded deque, but holding the print lock across anything a future backend might make slower
    # would put telemetry in the way of the thing it measures.
    from . import uploader as _uploader  # local import: keeps a cycle impossible at import time

    _uploader.uploader.offer(record)


def _dimension_sets(dims: dict) -> list[list[str]]:
    """Which dimension combinations this metric is published under.

    CloudWatch creates ONE METRIC PER EXACT DIMENSION SET, and a call site is free to add a
    dimension the next call site omits: `ledger_write_total` carries {outcome, reason, service}
    when a write fails and {outcome, service} when it succeeds. Publishing only the exact set
    would therefore mean NO fixed set an alarm could name — `dimensions = { outcome = "fail" }`
    matches neither and the alarm sits in INSUFFICIENT_DATA forever, which looks installed and
    can never fire. (Alarms cannot escape this with SEARCH() the way dashboards can: metric
    alarms reject SEARCH outright, because an alarm must bind to a fixed set of time series.)

    So alongside the exact set we publish progressively coarser ROLLUPS over _ROLLUP_KEYS —
    {service} and {service, outcome} — which are present regardless of what else a call site
    tagged. Those are what alarms bind to. Both rollups are needed and they answer different
    questions: {service} gives "p99 resolve latency, all outcomes", {service, outcome} gives
    "failed ledger writes, all reasons".

    A rollup is emitted only when every key in it is present, and never when it duplicates the
    exact set — a repeated set would publish (and bill for) the same metric twice.
    """
    exact = sorted(dims)
    sets = [exact]
    for depth in range(1, len(_ROLLUP_KEYS) + 1):
        rollup = _ROLLUP_KEYS[:depth]
        if all(key in dims for key in rollup):
            candidate = sorted(rollup)
            if candidate not in sets:
                sets.append(candidate)
    return sets


def metric_record(
    name: str, value: float, unit: str, dimensions: dict, properties: dict
) -> dict:
    """Build one EMF record.

    The dimensions/properties split IS the cost model, and getting it wrong is expensive:
      * DIMENSIONS are indexed by CloudWatch and BILLED per unique combination. Bounded values
        only — outcome=ok|error, source=builtin|plugin. A marketplace tool NAME here would mean
        one custom metric per tool ever published.
      * PROPERTIES are plain JSON on the log line. FREE, still queryable with Logs Insights.
        This is where run_id, account_id, agent_id and tool names belong.
    """
    dims = {k: str(v) for k, v in dimensions.items() if v is not None}
    dims.setdefault("service", _SERVICE)
    record = {
        "_aws": {
            "Timestamp": int(time.time() * 1000),
            "CloudWatchMetrics": [
                {
                    "Namespace": _NAMESPACE,
                    "Dimensions": _dimension_sets(dims),
                    "Metrics": [{"Name": name, "Unit": unit}],
                }
            ],
        },
        name: value,
        "version": _VERSION,
    }
    record.update(dims)
    record.update(properties)
    return record
