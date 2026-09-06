"""Render a run as a human-readable diagnosis — the thing you actually read to decide the next
instruction edit.

Four parts, in the order you reason about them:
  1. TRANSCRIPT — a compact play-by-play (user → tools → what it said), so "exactly what it did"
     is legible without opening the raw JSONL.
  2. DIAGNOSIS — the signals, grouped by severity: where it thrashed / stalled / hit a hole.
     Environment-origin findings are marked [env].
  3. TRIAGE — the agent/environment split, stated as an instruction: which findings mean "edit
     the agent" and which mean "retry / fix the resource / ask the user". This section exists so
     an agentic fix loop cannot 'repair' a working agent over a provider 429.
  4. CHECKS — the scenario's pass/fail, and an overall verdict.

Plain text on purpose: it drops into a terminal, a PR comment, or (later) the Agent Builder UI
unchanged.
"""

from __future__ import annotations

from .checks import CheckResult
from .signals import INFO, ORIGIN_ENV, PROBLEM, WARN, Finding
from .trace import Trace

# Severity bullets are ASCII so the eye can grep them; the rest of the layout uses a few UTF-8
# glyphs — the runner forces UTF-8 on stdout/stderr so legacy Windows codepages can't crash the
# print.
_BULLET = {PROBLEM: "x", WARN: "!", INFO: "-"}


def _one_line(text: str, width: int = 100) -> str:
    t = " ".join((text or "").split())
    return t if len(t) <= width else t[: width - 1] + "…"


def render(trace: Trace, findings: list[Finding], checks: list[CheckResult], goal: str = "") -> str:
    L: list[str] = []
    L.append("=" * 78)
    L.append(f"E2E RUN · {trace.scenario or '(scenario)'} · agent={trace.agent_id or '?'} · model={trace.model or '?'}")
    if goal:
        L.append(f"goal: {goal}")
    L.append("=" * 78)

    # 1. transcript
    L.append("\nTRANSCRIPT")
    for t in trace.turns:
        L.append(f"\n  ── turn {t.index} ──")
        L.append(f"  user> {_one_line(t.user)}")
        for c in t.tools:
            mark = "ok" if c.ok else "ERR"
            L.append(f"    · {c.name}({_one_line(_args(c.args), 48)}) → {mark}: {_one_line(c.result_text, 60)}")
        if t.last_assistant.strip():
            L.append(f"  bot>  {_one_line(t.last_assistant)}")
        meta = []
        if t.thinking_chars:
            meta.append(f"{t.thinking_chars} think")
        if t.artifacts:
            meta.append(f"{len(t.artifacts)} artifact(s)")
        if t.plans:
            meta.append(f"{len(t.plans)} plan-edit(s)")
        if meta:
            L.append(f"        ({' · '.join(meta)})")
    if trace.truncated:
        L.append("\n  ⚠ run truncated — no clean agent_end (wedge/timeout/kill)")

    # 2. diagnosis
    L.append("\n\nDIAGNOSIS")
    for sev in (PROBLEM, WARN, INFO):
        group = [f for f in findings if f.severity == sev]
        if not group:
            continue
        L.append(f"\n  {sev.upper()}")
        for f in group:
            where = f"turn {f.turn}" if f.turn else "run"
            env = " [env]" if f.origin == ORIGIN_ENV else ""
            L.append(f"    {_BULLET[sev]}{env} {f.code} ({where}): {_one_line(f.detail, 80)}")
    if not findings:
        L.append("  (nothing flagged)")

    # 3. triage — who owns each fix. Only real findings count; INFO is bookkeeping.
    agent_f = [f for f in findings if f.severity != INFO and f.origin != ORIGIN_ENV]
    env_f = [f for f in findings if f.severity != INFO and f.origin == ORIGIN_ENV]
    L.append("\n\nTRIAGE")
    if agent_f:
        L.append(f"  AGENT-ORIGIN ({len(agent_f)}): "
                 + ", ".join(sorted({f.code for f in agent_f}))
                 + " — fix by editing the agent (instructions / tools / agent.toml), ONE change "
                   "per iteration, re-run, confirm the finding cleared.")
    if env_f:
        L.append(f"  ENVIRONMENT ({len(env_f)}): "
                 + ", ".join(sorted({f.code for f in env_f}))
                 + " — do NOT edit the agent for these. Retry once, fix the resource, or ask "
                   "the user; an agent 'fixed' over a flaky provider is an agent broken.")
    if not agent_f and not env_f:
        L.append("  nothing to fix — now judge the OUTPUT against the goal (green checks prove "
                 "the mechanics fired, not that the deliverable is good).")

    # 4. checks
    L.append("\n\nCHECKS")
    for c in checks:
        L.append(f"  [{'PASS' if c.passed else 'FAIL'}] {c.name}: {_one_line(c.detail, 70)}")
    passed = sum(c.passed for c in checks)
    agent_p = sum(1 for f in findings if f.severity == PROBLEM and f.origin != ORIGIN_ENV)
    env_p = sum(1 for f in findings if f.severity == PROBLEM and f.origin == ORIGIN_ENV)
    L.append("\n" + "─" * 78)
    L.append(f"VERDICT: {passed}/{len(checks)} checks passed · "
             f"{agent_p} agent-origin + {env_p} environment problem(s)")
    L.append("-" * 78)
    return "\n".join(L)


def _args(args: dict) -> str:
    try:
        return ", ".join(f"{k}={_short(v)}" for k, v in args.items())
    except Exception:  # noqa: BLE001
        return str(args)


def _short(v) -> str:
    s = str(v)
    return s if len(s) <= 30 else s[:29] + "…"
