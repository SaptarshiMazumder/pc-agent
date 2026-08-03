"""agentd telemetry — one library, imported by every service, deployed as none of them.

    from agentd_telemetry import count, timing, timer, scope, setup_logging

    setup_logging("model-proxy")
    with scope(run_id=rid, account_id=acct):
        with timer("model_call", outcome="ok"):
            ...
        count("ledger_write", outcome="ok")

Everything ends up as one JSON line on stdout. On AWS the awslogs driver already forwards that
to CloudWatch, which turns it into metrics automatically — so this is a complete pipeline with
no new service, no new port, and nothing to fail in the request path.

The three rules this package enforces in code rather than in a style guide:
  * bounded values become DIMENSIONS (billed); everything else is a PROPERTY (free)   -> metrics.py
  * fields not on the allowlist never leave the process                               -> redact.py
  * correlation IDs ride an ambient context, not 200 function signatures               -> context.py
"""

from .context import bind, get, scope, unbind
from .emf import emit, enabled, service
from .logs import setup as setup_logging
from .metrics import count, gauge, money, timer, timing

__all__ = [
    "bind",
    "count",
    "emit",
    "enabled",
    "gauge",
    "get",
    "money",
    "scope",
    "service",
    "setup_logging",
    "timer",
    "timing",
    "unbind",
]
