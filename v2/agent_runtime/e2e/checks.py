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
    ok = any(c.completed and c.ok for c in calls)
    return CheckResult("tool_succeeded", ok,
                       f"{name}: {sum(c.completed and c.ok for c in calls)}/{len(calls)} completed successfully")


@_check("produced_artifact")
def _produced_artifact(trace: Trace, args: dict) -> CheckResult:
    """The run yielded a deliverable of a given kind (image/video/file) — the end-to-end proof.

    `file` narrows it to artifacts whose file name matches a regex — for a deliverable that is
    one specific file among many of its kind (an `install_*.py` beside the workflow it belongs
    to), where "some file exists" would pass on the workflow alone.
    """
    import re as _re

    kind = str(args.get("kind") or "")
    name = str(args.get("file") or "")
    try:
        rx = _re.compile(name, _re.I) if name else None
    except _re.error as e:
        return CheckResult("produced_artifact", False, f"bad file pattern: {e}")
    arts = [a for t in trace.turns for a in t.artifacts]
    matched = [
        a for a in arts
        if (not kind or str(a.get("kind") or "") == kind)
        and (rx is None or rx.search(str(a.get("name") or a.get("path") or "")))
    ]
    what = " ".join(x for x in (kind or "any", f"named /{name}/" if name else "") if x)
    return CheckResult("produced_artifact", bool(matched),
                       f"{len(matched)} {what} artifact(s) of {len(arts)} total")


@_check("no_blocking_stall")
def _no_blocking_stall(trace: Trace, args: dict) -> CheckResult:
    """The agent never stopped to ask before doing the work — the flexibility gate. Reads the
    stall signal directly, so 'the check' and 'the diagnosis' can never disagree."""
    # ORIGIN IS RESPECTED HERE, or the two layers contradict each other. `never_acted` fires for
    # BOTH "the agent talked and did nothing" and "the provider never answered, so it never got a
    # turn" — signals already separates those by origin, and says loudly that an agent must never
    # be edited over the second. A check that ignores that distinction reports a 402 or a 502 as a
    # failed behaviour check, and the check is the half a person acts on: seen today, where triage
    # printed "0 agent-origin problems" directly above "[FAIL] no_blocking_stall".
    stalls = [
        f
        for f in signals.stall(trace)
        if f.code in ("blocking_stall", "never_acted") and f.origin != signals.ORIGIN_ENV
    ]
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


@_check("message_says")
def _message_says(trace: Trace, args: dict) -> CheckResult:
    """An assistant message matched a pattern — how an OUTPUT CONVENTION is asserted.

    Some capabilities are not a tool call: ending a turn with a `suggest` block, carrying a
    required disclaimer, answering in a named format. Those live entirely in what the agent SAYS,
    so no tool-shaped check can see them — and an instruction the model quietly never follows is
    the commonest way a shipped feature does nothing at all.

    `pattern` is a regex (case-insensitive). `where`: 'any' turn (default), or 'last' for the
    final answer only — the difference between "it did this once" and "it does this every time".
    """
    import re as _re

    pattern = str(args.get("pattern") or "")
    where = str(args.get("where") or "any").lower()
    if not pattern:
        return CheckResult("message_says", False, "no pattern given")
    try:
        rx = _re.compile(pattern, _re.I | _re.S)
    except _re.error as e:
        return CheckResult("message_says", False, f"bad pattern: {e}")

    turns = trace.turns[-1:] if where == "last" else trace.turns
    # Every assistant utterance in scope, not just the closing one: an agent may put the block on
    # the turn that finished the work rather than the turn that said goodbye.
    hits = [t.index for t in turns if any(rx.search(m or "") for m in (t.assistant or []))]
    scope = "last turn" if where == "last" else f"{len(turns)} turn(s)"
    return CheckResult(
        "message_says", bool(hits),
        f"matched in turn(s) {hits}" if hits else f"never matched {pattern!r} across {scope}",
    )


@_check("tool_says")
def _tool_says(trace: Trace, args: dict) -> CheckResult:
    """A call to `tool` carried arguments matching a pattern — what the agent PUT IN a call.

    The message check sees prose; this sees the structured half of the conversation. A price
    quoted in an ask panel, a model named in an emitted graph, a plan step worded a required
    way — none of those are text the agent said, all of them are arguments it sent, and a
    scenario that could only assert prose would pass an agent that quoted nothing in the panel
    and fail one that put the number exactly where the window shows it.

    `tool` (required) names the call; `pattern` (required) is a regex, case-insensitive, run
    over the call's arguments serialised as JSON.
    """
    import json as _json
    import re as _re

    name = str(args.get("tool") or "")
    pattern = str(args.get("pattern") or "")
    if not name or not pattern:
        return CheckResult("tool_says", False, "tool and pattern required")
    try:
        rx = _re.compile(pattern, _re.I | _re.S)
    except _re.error as e:
        return CheckResult("tool_says", False, f"bad pattern: {e}")
    calls = trace.tool_calls(name)
    hits = [
        i for i, c in enumerate(calls, 1)
        if rx.search(_json.dumps(c.args, ensure_ascii=False, sort_keys=True, default=str))
    ]
    if not calls:
        return CheckResult("tool_says", False, f"{name} never ran")
    return CheckResult(
        "tool_says", bool(hits),
        f"{name} call(s) {hits} matched" if hits
        else f"none of {len(calls)} {name} call(s) matched {pattern!r}",
    )


@_check("no_unrecovered_error")
def _no_unrecovered_error(trace: Trace, args: dict) -> CheckResult:
    bad = [f for f in signals.holes(trace) if f.code == "tool_error" and f.severity == signals.PROBLEM]
    return CheckResult("no_unrecovered_error", not bad,
                       "no unrecovered tool errors" if not bad else f"{len(bad)} unrecovered error(s)")


@_check("completed")
def _completed(trace: Trace, args: dict) -> CheckResult:
    """The run ended on its own rather than wedging."""
    completed = bool(trace.turns) and not trace.truncated and all(
        turn.ended and not turn.end_error
        and turn.end_reason not in ("aborted", "cancelled", "error", "lost")
        and all(call.completed for call in turn.tools)
        for turn in trace.turns
    )
    return CheckResult("completed", completed,
                       "ran to completion" if completed else "run aborted, failed, or has unfinished work")


# --------------------------------------------------------------------------- the vocabulary, described

#: Args per check, for `vocabulary()` — kept beside the registry so a new check and its arg doc
#: land in one review. A check with no entry takes no args.
_ARGS: dict[str, dict[str, str]] = {
    "tool_called": {"tool": "tool name (required)", "min": "minimum call count (default 1)"},
    "call_order": {"first": "tool that must run first (required)",
                   "then": "tool that must not run before it (required)"},
    "tool_succeeded": {"tool": "tool name (required)"},
    "tool_says": {"tool": "tool name (required)",
                  "pattern": "regex the call's arguments (as JSON) must match (required)"},
    "produced_artifact": {"kind": "artifact kind: image / video / file (default: any)",
                          "file": "regex the artifact's file name must match (default: any)"},
    "max_turns": {"n": "maximum turn count (required)"},
}


def vocabulary() -> list[dict]:
    """Every check name, its args, and what it asserts — the exact list a scenario author may use.
    This is what the Agent Builder's `e2e_checks` tool prints, so scenarios never carry an
    invented check name that fails as 'unknown check' at run time."""
    out = []
    for name, fn in _REGISTRY.items():
        doc = " ".join((fn.__doc__ or "").split())
        out.append({"name": name, "args": _ARGS.get(name, {}), "asserts": doc})
    return out
