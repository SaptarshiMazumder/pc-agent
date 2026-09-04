"""Checks — a scenario's named expectations, resolved into pass/fail over a trace.

A check is the scenario author saying "this run must have this property." They are thin: most
read the trace directly, a couple lean on `signals` (a stall check IS "did the stall signal
fire"). Kept as a small named registry so a scenario JSON stays declarative — `{"name":
"produced_artifact", "kind": "video"}` — and so the Agent Builder feature can offer the same
vocabulary to whoever authors scenarios for a built agent.

Checks are the PASS/FAIL layer; `signals` is the DIAGNOSIS layer. A run can pass every check and
still surface warnings worth acting on — that is intended: the checks say "good enough to ship",
the signals say "here is where it is still clumsy".
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from . import signals
from .trace import Trace


@dataclass
class CheckResult:
    name: str
    passed: bool
    detail: str


_REGISTRY: dict[str, Callable[[Trace, dict], CheckResult]] = {}


def _check(name: str):
    def deco(fn: Callable[[Trace, dict], CheckResult]):
        _REGISTRY[name] = fn
        return fn
    return deco


def run_checks(trace: Trace, checks: list) -> list[CheckResult]:
    out: list[CheckResult] = []
    for c in checks:
        fn = _REGISTRY.get(c.name)
        if fn is None:
            out.append(CheckResult(c.name, False, f"unknown check '{c.name}'"))
            continue
        try:
            out.append(fn(trace, c.args))
        except Exception as e:  # noqa: BLE001 — a check bug must not sink the whole report
            out.append(CheckResult(c.name, False, f"check errored: {type(e).__name__}: {e}"))
    return out


# --------------------------------------------------------------------------- the vocabulary


@_check("tool_called")
def _tool_called(trace: Trace, args: dict) -> CheckResult:
    """A given tool ran at least `min` times (default 1) — did it even attempt the capability."""
    name = str(args.get("tool") or args.get("name") or "")
    need = int(args.get("min") or 1)
    n = len(trace.tool_calls(name))
    return CheckResult("tool_called", n >= need,
                       f"{name} called {n}× (need ≥{need})")


@_check("call_order")
def _call_order(trace: Trace, args: dict) -> CheckResult:
    """Tool `first`'s first call precedes tool `then`'s first call — protocol-order enforcement
    (e.g. design-first: comfy_emit before comfy_install). Vacuously true if `then` never ran."""
    first = str(args.get("first") or "")
    then = str(args.get("then") or "")
    order: list[str] = [c.name for t in trace.turns for c in t.tools]
    a = order.index(first) if first in order else None
    b = order.index(then) if then in order else None
    if b is None:
        return CheckResult("call_order", True, f"{then} never ran — order moot")
    if a is None:
        return CheckResult("call_order", False, f"{then} ran but {first} never did")
    return CheckResult("call_order", a < b,
                       f"{first} first at call #{a}, {then} at call #{b}")


@_check("tool_succeeded")
def _tool_succeeded(trace: Trace, args: dict) -> CheckResult:
    """A given tool ran AND at least one call came back ok — attempted is not the same as worked."""
    name = str(args.get("tool") or args.get("name") or "")
    calls = trace.tool_calls(name)
    ok = any(c.ok for c in calls)
    return CheckResult("tool_succeeded", ok,
                       f"{name}: {sum(c.ok for c in calls)}/{len(calls)} ok")


@_check("produced_artifact")
def _produced_artifact(trace: Trace, args: dict) -> CheckResult:
    """The run yielded a deliverable of a given kind (image/video/file) — the end-to-end proof."""
    kind = str(args.get("kind") or "")
    arts = [a for t in trace.turns for a in t.artifacts]
    matched = [a for a in arts if not kind or str(a.get("kind") or "") == kind]
    return CheckResult("produced_artifact", bool(matched),
                       f"{len(matched)} {kind or 'any'} artifact(s) of {len(arts)} total")


@_check("no_blocking_stall")
def _no_blocking_stall(trace: Trace, args: dict) -> CheckResult:
    """The agent never stopped to ask before doing the work — the flexibility gate. Reads the
    stall signal directly, so 'the check' and 'the diagnosis' can never disagree."""
    stalls = [f for f in signals.stall(trace) if f.code in ("blocking_stall", "never_acted")]
    return CheckResult("no_blocking_stall", not stalls,
                       "no blocking stall" if not stalls else
                       f"{len(stalls)} stall(s): " + "; ".join(f"turn {f.turn}" for f in stalls))


@_check("no_punt_to_user")
def _no_punt(trace: Trace, args: dict) -> CheckResult:
    """The agent never told the user to do a thing a tool could do (the 'install these yourself'
    failure). Reads the holes signal."""
    punts = [f for f in signals.holes(trace) if f.code == "punted_to_user"]
    return CheckResult("no_punt_to_user", not punts,
                       "no punts" if not punts else f"{len(punts)} punt(s) to the user")


@_check("max_turns")
def _max_turns(trace: Trace, args: dict) -> CheckResult:
    n = int(args.get("n") or args.get("max") or 0)
    return CheckResult("max_turns", len(trace.turns) <= n,
                       f"{len(trace.turns)} turns (cap {n})")


@_check("no_repeated_question")
def _no_repeated_question(trace: Trace, args: dict) -> CheckResult:
    """The agent didn't ask the user the same thing twice — a thrash/over-strict tell."""
    reasked = [f for f in signals.stall(trace) if f.code == "reask_deferred"]
    return CheckResult("no_repeated_question", not reasked,
                       "no re-asks" if not reasked else f"{len(reasked)} re-ask(s) of deferred input")


@_check("no_unrecovered_error")
def _no_unrecovered_error(trace: Trace, args: dict) -> CheckResult:
    bad = [f for f in signals.holes(trace) if f.code == "tool_error" and f.severity == signals.PROBLEM]
    return CheckResult("no_unrecovered_error", not bad,
                       "no unrecovered tool errors" if not bad else f"{len(bad)} unrecovered error(s)")


@_check("completed")
def _completed(trace: Trace, args: dict) -> CheckResult:
    """The run ended on its own rather than wedging."""
    return CheckResult("completed", not trace.truncated,
                       "ran to completion" if not trace.truncated else "run was truncated/wedged")
