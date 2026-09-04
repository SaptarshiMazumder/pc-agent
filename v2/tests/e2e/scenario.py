"""A scenario — the declaration an e2e run executes. Pure data, no logic, no transport.

This is the contract the whole harness (and, later, the Agent Builder feature) is built around:
a scenario names an agent, the settings/connection it runs under, the user turns to send in
order, and the checks its trace must satisfy. Everything else — driving the daemon, capturing
the trace, diagnosing it — is machinery around this one declarative object.

Kept agent-agnostic on purpose: `settings` is an opaque bag handed to the agent, `turns` are
plain strings, `checks` are named predicates resolved in `checks.py`. A ComfyUI scenario and a
Gmail scenario differ only in their values, never their shape.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class Check:
    """One expectation over the resulting trace. `name` resolves to a predicate in checks.py;
    `args` are its parameters. A human `describe` is filled in by the resolver for the report."""

    name: str
    args: dict[str, Any] = field(default_factory=dict)


@dataclass
class Scenario:
    id: str
    agent_id: str
    #: What the user is trying to get — one line, printed atop the report so a trace is readable
    #: without re-reading the turns.
    goal: str = ""
    #: Declared settings for the agent (per-account values: a ComfyUI URL, tokens). Applied
    #: before the first turn. An opaque map — the runner writes it to the agent's settings store.
    settings: dict[str, str] = field(default_factory=dict)
    #: Ordered user messages. The runner sends turn N only after turn N-1's run has ended, so the
    #: trace's turn boundaries line up with these.
    turns: list[str] = field(default_factory=list)
    checks: list[Check] = field(default_factory=list)
    #: Optional per-scenario cap the runner enforces so a wedged agent cannot run forever.
    max_turns: int = 40

    @staticmethod
    def load(path: str | Path) -> "Scenario":
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return Scenario(
            id=str(data.get("id") or Path(path).stem),
            agent_id=str(data["agent_id"]),
            goal=str(data.get("goal") or ""),
            settings={str(k): str(v) for k, v in (data.get("settings") or {}).items()},
            turns=[str(t) for t in (data.get("turns") or [])],
            checks=[
                Check(name=str(c["name"]), args={k: v for k, v in c.items() if k != "name"})
                for c in (data.get("checks") or [])
            ],
            max_turns=int(data.get("max_turns") or 40),
        )
