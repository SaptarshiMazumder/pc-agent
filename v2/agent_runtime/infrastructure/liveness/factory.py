"""build_observers — assemble the liveness observers from config.

Default = [] (off => today's behavior). `config.liveness` (env AGENTD_LIVENESS,
comma-separated) selects which observers run, e.g. "callrate,noprogress". Unknown
names are ignored (forward-compatible)."""

from __future__ import annotations

import logging

from agent_runtime.application.interfaces.run_observer import RunObserver
from agent_runtime.infrastructure.liveness.call_rate import CallRateBrake
from agent_runtime.infrastructure.liveness.no_progress import NoProgressDetector

log = logging.getLogger("agentd")

_REGISTRY = {
    "callrate": lambda: CallRateBrake(),
    "noprogress": lambda: NoProgressDetector(),
}


def build_observers(config) -> list[RunObserver]:
    names = getattr(config, "liveness", None) or []
    observers = [_REGISTRY[n]() for n in names if n in _REGISTRY]
    if observers:
        log.info("liveness observers: %s", [n for n in names if n in _REGISTRY])
    return observers
