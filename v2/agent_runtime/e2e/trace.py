"""The trace model — what an e2e run captures, and how it is read back.

This is the raw material every diagnosis is computed from, so it is deliberately DUMB: it holds
what happened, not what it means. `signals.py` decides meaning; keeping that split is what lets
the diagnosis evolve without re-running the (expensive, real-model) agent — you re-analyse a
saved `trace.jsonl` offline.

A trace is a list of TURNS. A turn is one user message and everything the agent did in response:
the ordered `AgentEvent`s the runtime emitted (the same stream the window sees — see
domain/events.py), folded into the few things a diagnosis actually asks about:

    tool calls (name, args, result, ok)   — what it DID
    assistant text                        — what it SAID
    plan snapshots                        — how its update_plan changed
    thinking volume + token/timing cost   — how hard it worked to get there
    end reason                            — how the turn stopped

AGENT-AGNOSTIC. Nothing here mentions ComfyUI or any tool by name; a Wan install and a Gmail
send fold into the same shape. That is the groundwork requirement — the Agent Builder feature
runs this same model against any built agent.

On-disk form is JSONL, one object per line: a `{"kind":"user_turn",...}` marker opens each turn,
then one `{"kind":"event","event":{...}}` per captured AgentEvent. Append-only, so a run that
dies mid-way still leaves a readable partial trace — which is itself a finding.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable


@dataclass
class ToolCall:
    name: str
    args: dict[str, Any]
    result_text: str = ""
    ok: bool = True
    #: The turn this call happened in — carried so a signal can talk about "turn 4" without
    #: threading the index through every query.
    turn: int = 0

    @property
    def args_key(self) -> str:
        """A stable identity for "the same call again" — name + sorted args. Repeated identical
        keys are the signature of a thrashing loop, so this is what the repeat signal groups on."""
        try:
            return self.name + "|" + json.dumps(self.args, sort_keys=True, default=str)
        except (TypeError, ValueError):
            return self.name + "|<unserializable>"


@dataclass
class Turn:
    index: int
    user: str
    tools: list[ToolCall] = field(default_factory=list)
    #: Every assistant text block the turn produced, in order. A turn that only appended text
    #: and called no tool is the raw material of both "stall" (it asked and stopped) and "thrash"
    #: (it reasoned and did nothing) — which one depends on the text, which signals decide.
    assistant: list[str] = field(default_factory=list)
    #: Each `update_plan` payload seen this turn. Re-emitting a plan that is byte-identical, or
    #: several times in one turn, is plan churn — a thrash tell.
    plans: list[Any] = field(default_factory=list)
    thinking_chars: int = 0
    tokens: int = 0
    wall_ms: int = 0
    end_reason: str = ""
    #: The run's own words when it ended badly ("rate limit", "connection reset", a provider's
    #: 500). This is what origin triage reads to say "environment, don't edit the agent".
    end_error: str = ""
    #: Artifacts the runtime attributed to this turn (rendered files). What a "produced a video"
    #: check reads — kind comes from server-side detection, not a guess here.
    artifacts: list[dict] = field(default_factory=list)

    @property
    def last_assistant(self) -> str:
        return self.assistant[-1] if self.assistant else ""


@dataclass
class Trace:
    scenario: str = ""
    model: str = ""
    agent_id: str = ""
    #: The throwaway session the run used — what the e2e_run tool deletes afterwards so a test
    #: never lingers in anyone's chat list.
    session_key: str = ""
    turns: list[Turn] = field(default_factory=list)
    #: True when the run was cut off (timeout, killed, crash) rather than each turn ending on its
    #: own `agent_end`. A truncated trace is a finding, not an error to hide.
    truncated: bool = False

    # ---- all tool calls across the run, flat — most signals want the whole sequence ----
    @property
    def all_tools(self) -> list[ToolCall]:
        return [t for turn in self.turns for t in turn.tools]

    def tool_calls(self, name: str) -> list[ToolCall]:
        return [t for t in self.all_tools if t.name == name]

    def first_tool_turn(self, names: Iterable[str]) -> int | None:
        """The turn index where any of `names` was first called, or None. Used by the stall
        signal: 'did a build tool ever run, and when, relative to the questions asked'."""
        wanted = set(names)
        for t in self.all_tools:
            if t.name in wanted:
                return t.turn
        return None


# --------------------------------------------------------------------------- capture (write)


class TraceWriter:
    """Append-only JSONL sink the runner feeds live. Kept separate from `Trace` so a run streams
    to disk as it happens — if the agent wedges, the partial trace is already saved and readable,
    which is exactly the case you most want to inspect."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._f = self.path.open("w", encoding="utf-8")

    def open_turn(self, index: int, user: str) -> None:
        self._write({"kind": "user_turn", "index": index, "user": user})

    def event(self, turn: int, event: dict) -> None:
        self._write({"kind": "event", "turn": turn, "event": event})

    def meta(self, **fields) -> None:
        self._write({"kind": "meta", **fields})

    def _write(self, obj: dict) -> None:
        self._f.write(json.dumps(obj, ensure_ascii=False, default=str) + "\n")
        self._f.flush()

    def close(self) -> None:
        self._f.close()


# --------------------------------------------------------------------------- reconstruct (read)


def load_trace(path: str | Path) -> Trace:
    """Fold a JSONL trace back into Turn/ToolCall objects. The one place raw `AgentEvent`s become
    the structured facts signals query — so a change in how we read events touches only here."""
    trace = Trace()
    cur: Turn | None = None
    # A tool call spans a start event (name+args) and an end event (result+ok); hold the open one
    # by its call id so the end can complete it.
    pending: dict[str, ToolCall] = {}

    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except ValueError:
            continue
        kind = obj.get("kind")
        if kind == "meta":
            trace.scenario = obj.get("scenario", trace.scenario)
            trace.model = obj.get("model", trace.model)
            trace.agent_id = obj.get("agent_id", trace.agent_id)
            trace.session_key = obj.get("session_key", trace.session_key)
            if obj.get("truncated"):
                trace.truncated = True
            continue
        if kind == "user_turn":
            cur = Turn(index=int(obj.get("index", len(trace.turns))), user=str(obj.get("user", "")))
            trace.turns.append(cur)
            pending.clear()
            continue
        if kind == "event" and cur is not None:
            _fold_event(cur, obj.get("event") or {}, pending)

    return trace


def _fold_event(turn: Turn, ev: dict, pending: dict[str, ToolCall]) -> None:
    et = ev.get("type") or ev.get("event") or ""
    if et == "tool_execution_start":
        call = ToolCall(
            name=str(ev.get("toolName") or ev.get("name") or ""),
            args=ev.get("args") or ev.get("input") or {},
            turn=turn.index,
        )
        cid = str(ev.get("toolCallId") or ev.get("id") or f"anon{len(turn.tools)}")
        pending[cid] = call
        turn.tools.append(call)
    elif et == "tool_execution_end":
        cid = str(ev.get("toolCallId") or ev.get("id") or "")
        call = pending.pop(cid, None) or (turn.tools[-1] if turn.tools else None)
        if call is not None:
            call.result_text = _result_text(ev)
            call.ok = not bool(ev.get("isError") or ev.get("error"))
        for a in ev.get("artifacts") or []:
            if isinstance(a, dict):
                turn.artifacts.append(a)
    elif et == "message_update":
        k = ev.get("kind")
        if k == "text_delta":
            _append_text(turn, str(ev.get("text") or ev.get("delta") or ""))
        elif k == "thinking_delta":
            turn.thinking_chars += len(str(ev.get("text") or ev.get("delta") or ""))
    elif et == "message_end":
        # A finalized assistant message may carry its whole text (not just deltas) + artifacts.
        if (ev.get("role") or ev.get("kind")) in ("assistant", "bot") and ev.get("text"):
            _append_text(turn, str(ev["text"]), replace_if_empty=True)
        # THE FAILURE'S OWN WORDS, wherever the runtime put them. A model call that fails names
        # its reason here (`errorMessage` on the finalized message) while `agent_end` often
        # carries only `stopReason=error` — so reading agent_end alone left the origin triage
        # with "no message" and it defaulted every provider outage to the AGENT's fault.
        msg = ev.get("message") if isinstance(ev.get("message"), dict) else ev
        err = str((msg or {}).get("errorMessage") or "").strip()
        if err and not turn.end_error:
            turn.end_error = err
        for a in ev.get("artifacts") or []:
            if isinstance(a, dict):
                turn.artifacts.append(a)
    elif et == "agent_end":
        turn.end_reason = str(ev.get("stopReason") or ev.get("stop_reason") or "")
        err = str(ev.get("error") or "").strip()
        if err:
            turn.end_error = err
    elif et in ("plan", "update_plan", "plan_update"):
        turn.plans.append(ev.get("plan") or ev.get("steps") or ev.get("payload") or ev)
    elif et == "context_usage":
        turn.tokens = int(ev.get("used") or ev.get("tokens") or turn.tokens or 0)
    elif et == "tool_progress":
        # incremental tool chatter — captured only as a liveness hint, not a fact a signal reads
        pass


def _append_text(turn: Turn, text: str, replace_if_empty: bool = False) -> None:
    if not text:
        return
    # Deltas accumulate into the current (last) assistant block; a message_end with full text
    # replaces an empty accumulator rather than duplicating.
    if turn.assistant and (turn.assistant[-1] == "" or not replace_if_empty):
        turn.assistant[-1] = turn.assistant[-1] + text if not replace_if_empty else text
    else:
        turn.assistant.append(text)


def _result_text(ev: dict) -> str:
    r = ev.get("result")
    if isinstance(r, str):
        return r
    if isinstance(r, dict):
        c = r.get("content")
        if isinstance(c, list):
            return "".join(b.get("text", "") for b in c if isinstance(b, dict))
        return str(r.get("text") or "")
    return str(ev.get("text") or "")
