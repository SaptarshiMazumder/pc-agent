"""count / timing / timer / gauge — the entire public metrics surface.

    count("ledger_write", outcome="ok")
    timing("resolve_latency", 42.0)
    with timer("model_call", outcome="ok"):
        ...

Keyword arguments become DIMENSIONS (indexed, billed per unique combination), so they must be
bounded vocabularies. The ambient context (run_id, account_id, agent_id) is attached as
PROPERTIES, which are free. See emf.metric_record for why that split is load-bearing.

CARDINALITY GUARD. The rule "only bounded values as dimensions" is the kind of rule that holds
until someone writes `count("tool_call", tool=name)` on a Friday. With a public marketplace
that single line becomes one billed custom metric per tool ever published. So the guard is in
CODE, not in a style guide: once a dimension key has been seen with too many distinct values,
its value is replaced with a placeholder and a warning is logged once. The graph degrades; the
bill does not explode.
"""

from __future__ import annotations

import logging
import time
from contextlib import contextmanager

from . import context, emf, redact

log = logging.getLogger("agentd.telemetry")

#: Distinct values allowed per dimension key before it is collapsed. Generous enough for a real
#: vocabulary (model names, tool sources), tight enough to catch an unbounded one quickly.
MAX_DIMENSION_VALUES = 50
_OVERFLOW = "__high_cardinality__"

_seen: dict[str, set[str]] = {}
_warned: set[str] = set()


def _guard(dimensions: dict) -> dict:
    safe = {}
    for key, value in dimensions.items():
        if value is None:
            continue
        text = str(value)
        values = _seen.setdefault(key, set())
        if text not in values:
            if len(values) >= MAX_DIMENSION_VALUES:
                if key not in _warned:
                    _warned.add(key)
                    log.warning(
                        "telemetry: dimension %r exceeded %d distinct values — collapsing. "
                        "Unbounded names belong in PROPERTIES (free), not dimensions (billed).",
                        key,
                        MAX_DIMENSION_VALUES,
                    )
                safe[key] = _OVERFLOW
                continue
            values.add(text)
        safe[key] = text
    return safe


def _emit(name: str, value: float, unit: str, dimensions: dict, extra: dict | None) -> None:
    props = context.get()
    if extra:
        props.update(extra)
    emf.emit(emf.metric_record(name, value, unit, _guard(dimensions), redact.scrub(props)))


def count(name: str, value: float = 1, _props: dict | None = None, **dimensions) -> None:
    """Something happened. `_props` carries unbounded facts (tool name, model) for free."""
    _emit(name, value, "Count", dimensions, _props)


def timing(name: str, ms: float, _props: dict | None = None, **dimensions) -> None:
    """Something took this long, in milliseconds."""
    _emit(name, round(float(ms), 2), "Milliseconds", dimensions, _props)


def gauge(name: str, value: float, unit: str = "None", _props: dict | None = None, **dimensions) -> None:
    """A level right now — balance, queue depth, connections."""
    _emit(name, float(value), unit, dimensions, _props)


def money(name: str, usd: float, _props: dict | None = None, **dimensions) -> None:
    """Dollars. Separated from `gauge` so cost is greppable and never accidentally a Count."""
    _emit(name, round(float(usd), 6), "None", dimensions, _props)


@contextmanager
def timer(name: str, _props: dict | None = None, **dimensions):
    """Time a block. Records even when the block raises, tagging outcome=error.

    Uses perf_counter, not wall clock: immune to NTP steps and clock adjustments, which
    otherwise produce negative durations that quietly poison a percentile.
    """
    started = time.perf_counter()
    outcome = "ok"
    try:
        yield
    except BaseException:
        outcome = "error"
        raise
    finally:
        dimensions.setdefault("outcome", outcome)
        timing(name, (time.perf_counter() - started) * 1000.0, _props, **dimensions)
