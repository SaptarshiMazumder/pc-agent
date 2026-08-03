"""Structured JSON logging — same context, same redaction, same stdout as metrics.

Replaces `logging.basicConfig(...)` in each service. Two things it buys over the plain format:

  1. Every line automatically carries the ambient run_id / account_id / agent_id, so a support
     question ("what happened to this person?") is one query instead of an archaeology project.
  2. Every field passes the allowlist, so a stray `log.info("prompt=%s", prompt)` cannot put
     user content into CloudWatch.

Free-text messages still pass through (they are the point of a log), so the discipline is:
put IDENTIFIERS in the message, never CONTENT.
"""

from __future__ import annotations

import json
import logging
import os

from . import context, emf, redact

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", None, None).__dict__) | {
    "asctime", "message", "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "service": emf.service(),
            "message": record.getMessage(),
        }
        payload.update(redact.scrub(context.get()))
        # extras passed as logging's `extra={...}` — allowlisted like everything else
        extras = {k: v for k, v in record.__dict__.items() if k not in _RESERVED}
        payload.update(redact.scrub(extras))
        if record.exc_info:
            # type + location only. A formatted traceback can contain argument values.
            exc_type = record.exc_info[0]
            payload["error_type"] = getattr(exc_type, "__name__", "Exception")
        return json.dumps(payload, separators=(",", ":"), default=str)


def setup(service: str | None = None, level: str | None = None) -> None:
    """Install JSON logging on the root logger. Idempotent."""
    if service:
        os.environ.setdefault("AGENTD_SERVICE", service)
    lvl = (level or os.environ.get("AGENTD_LOG_LEVEL", "INFO")).upper()
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    for existing in list(root.handlers):
        root.removeHandler(existing)
    root.addHandler(handler)
    root.setLevel(getattr(logging, lvl, logging.INFO))
