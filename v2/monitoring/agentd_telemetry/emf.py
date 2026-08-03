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

# stdout is shared with normal logging; serialise so a metric line is never interleaved with
# a half-written log line (which would make the JSON unparseable at the other end).
_lock = threading.Lock()


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
            sys.stdout.write(line + "\n")
            sys.stdout.flush()
        except Exception:  # noqa: BLE001 — a closed/broken stdout is not our problem to raise
            pass
        if _FILE:
            try:
                with open(_FILE, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except Exception:  # noqa: BLE001
                pass


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
                    "Dimensions": [sorted(dims.keys())],
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
