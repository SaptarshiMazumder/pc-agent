"""The diagnosis engine — pure functions over a Trace that say what the agent's behaviour MEANS.

This is the point of the whole harness (see the plan the user approved): the run's value is not
pass/fail, it is a *diagnosis* that drives tuning — where the agent thrashed (make it more
deterministic), where it stalled or was too strict (make it more flexible), and what holes it
hit. Those three families are exactly the sections below.

Every function here is PURE: `Trace -> list[Finding]`. No transport, no model, no agent-specific
knowledge — a finding is computed the same way for any agent. That is what lets the same engine
become the Agent Builder feature, and what lets a diagnosis be recomputed offline from a saved
trace after the (expensive) run is over.

A Finding is graded, not boolean:
  PROBLEM — behaviour that defeats the agent's purpose (stalled before doing the work; gave up).
  WARN    — waste or fragility (re-planned 4×; repeated an identical call; heavy think, no act).
  INFO    — worth seeing, not wrong (slow tool; long but productive turn).

The signals are tuned to the two failures the user is chasing:
  * DETERMINISM gaps — thrash: reasoning that produces no action, loops, plan churn.
  * FLEXIBILITY gaps — stall: asking for, or waiting on, something before doing any work; and
    re-asking for inputs the user explicitly deferred.
Read together they say, per turn, which way to push the instructions.

Every Finding also carries an ORIGIN — "agent" or "environment" — because most live-run failures
are NOT the agent's fault (a 429, a dropped connection, an unreachable backend, a provider out of
balance: the majority of observed live runs hit at least one). The fix loop must edit the agent
ONLY for agent-origin findings; "fixing" an agent in response to a flaky provider corrupts
something that was working. The classifier is a heuristic over the failure's own words plus one
structural rule: a failure with zero agent tool activity around it is almost always environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .trace import Trace, Turn

PROBLEM, WARN, INFO = "problem", "warn", "info"
ORIGIN_AGENT, ORIGIN_ENV = "agent", "environment"

#: The failure vocabulary of a broken ENVIRONMENT rather than a broken agent: provider throttling
#: and outages, transport drops, unreachable backends, exhausted balances, absent credentials.
#: Matched against the failure's OWN words (a tool result, an agent_end error) — never against
#: the agent's prose, which speculates.
_ENV_RX = re.compile(
    r"rate.?limit|too many requests|\b429\b|\b402\b|\b502\b|\b503\b|\b504\b|overloaded|"
    r"internal ?server ?error|service unavailable|bad gateway|"
    r"connection (?:refused|reset|error|closed|dropped|aborted)|broken pipe|"
    r"timed?.?out|unreachable|could not (?:reach|connect)|failed to connect|no route to host|"
    r"name (?:or service not known|resolution)|getaddrinfo|ssl|certificate|"
    r"insufficient (?:balance|credits|funds|quota)|out of (?:credits|balance)|quota exceeded|"
    r"payment required|billing|"
    r"missing (?:[A-Z0-9_]+ )?(?:api.?key|key|credential|token|secret)|"
    r"invalid (?:api.?key|token|credential)|unauthorized|\b401\b|"
    # A MODEL ID THAT DOES NOT RESOLVE is configuration, not conduct. `--model` naming a model
    # the key cannot see 404s before the agent is ever asked to do anything, so triaging that
    # to the agent sends the fix loop off to "improve" instructions that never executed — the
    # same misdiagnosis the throttling patterns above exist to prevent.
    #
    # KEPT NARROW ON PURPOSE. A bare "not found" would swallow a `read` of a path the agent
    # itself got wrong, and that is genuinely the agent's fault and must stay visible. These
    # four all name the PROVIDER refusing an identifier, which the agent never chose.
    r"\b404\b|notfounderror|model_not_found|unknown model|"
    r"does not exist or you do not have access|"
    # THE PROVIDER ANSWERED WITH NOTHING. A completion that streams zero tokens and stops is a
    # provider fault the runtime already names in these words ("empty response … not even an
    # error … a fault on the provider's side"), and it is invisible to every pattern above —
    # no status code, no transport verb. Left out, a whole run of empty completions reads as the
    # agent refusing to act, which is the one misdiagnosis that makes a fix loop damage a
    # working agent.
    r"empty (?:response|completion)|returned (?:an )?empty|carried nothing|"
    r"provider'?s side|no visible output|api ?error",
    re.I,
)


def looks_environmental(text: str) -> bool:
    """Do these failure words describe the WORLD failing rather than the agent? Public so the
    checks/report layers and the e2e_run tool can triage with the same single rule."""
    return bool(text and _ENV_RX.search(text))

#: Tools that are NOT doing the work — reads, probes, and BOOKKEEPING. Agent-agnostic by
#: construction: a build tool is one whose name is none of these. For a precise verdict a scenario
#: can pass its own list, but the default heuristic — "did any tool that changes something run" —
#: is enough to separate a turn that acted from a turn that only asked.
#:
#: "plan" IS IN THIS LIST, and that was a real false negative. `update_plan` writes a checklist and
#: changes nothing else, but its name carries none of the read hints, so a turn that planned and
#: then asked the user for input scored as HAVING ACTED — which suppressed `blocking_stall` on that
#: turn AND, by making it the "first build turn", suppressed the check on every turn after it. A
#: run where the agent announced a plan and then downed tools came back clean.
_NON_BUILD_HINTS = ("probe", "inventory", "status", "list", "spec", "get", "read", "research",
                    "check", "plan")

#: The same idea, matched WHOLE rather than as a substring — because the shared read tools are
#: named too short and too plainly for substring matching to reach them. `ls` cannot be a hint: it
#: is inside "tools", "models" and "controls", so adding it would silently reclassify half the
#: catalogue as read-only. These names carry no hint at all and so were all scored as BUILD WORK,
#: which is how a turn that ran `ls` + `find`, then asked the user for a URL, counted as a turn
#: that acted — and became the "first build turn" that suppressed the stall check on every turn
#: after it. Research is deliberately here too: reading the web is not building anything, and
#: "researched, then asked for the URL" is precisely the stall this detector exists to catch.
_NON_BUILD_EXACT = frozenset({
    "ls", "find", "grep", "glob", "cat", "tree", "stat",
    "web_search", "web_fetch", "browser",
    "show_files", "verify_answer",
})

#: A trailing ```suggest fence — chips, not prose. STRIPPED BEFORE ASKING "was this a question",
#: because `\?\s*$` anchors to the END of the message and the fence now sits there on every turn:
#: the agent's real closing sentence stopped being last, so an end-anchored match could never fire
#: again. The convention we added to make turns actionable quietly blinded the detector watching
#: for turns that hand work back.
_SUGGEST_FENCE_RX = re.compile(r"(?:```|~~~)\s*suggest\s*\n[\s\S]*?(?:(?:```|~~~)|\Z)", re.I)

#: A trailing ```approve fence — the paid-services gate, and THE ONE SANCTIONED STOP in this agent.
#: It is the one terminal state that must never read as a stall: the agent researched, settled the
#: design, and then deliberately stopped to ask which paid services it may bill the user for.
#:
#: NO BUILD TOOL RUNS, BY DESIGN. Emitting a workflow before that answer is the exact failure the
#: gate exists to prevent — the cost would then be discovered at run time, with the graph already
#: built around the paid node, when declining means throwing the design away.
#:
#: Without this, a PERFECT gate run scored `never_acted` ("all talk, no work") as an AGENT fault,
#: pointing the fix loop at the very behaviour the gate was built to produce.
_APPROVE_FENCE_RX = re.compile(r"(?:```|~~~)\s*approve\s*\n[\s\S]*?(?:(?:```|~~~)|\Z)", re.I)


def _ended_on_approval_gate(trace: Trace) -> bool:
    """Did the run finish waiting on the paid-services checkboxes?

    ONLY THE LAST THING SAID COUNTS. A fence emitted mid-run and then followed by a turn that did
    nothing is still a stall — the gate excuses stopping to ask, not going quiet afterwards.
    """
    for turn in reversed(trace.turns):
        text = turn.last_assistant
        if text:
            return bool(_APPROVE_FENCE_RX.search(text))
    return False

#: Phrases that mark an assistant turn as ASKING or DEFERRING rather than acting. Deliberately
#: about intent, not topic, so they generalise past ComfyUI.
#:
#: THE SECOND GROUP IS THE IMPERATIVE ASK, and it is how agents actually ask. Politeness forms
#: ("could you", "please provide") are what a detector author writes down; "Paste your instance URL
#: right here in chat" and "I need one thing from you" are what a model writes, and neither carries
#: a question mark. A stall phrased as an instruction is still a stall.
_QUESTION_RX = re.compile(r"\?\s*$|\bplease (?:provide|upload|share|tell|confirm|choose)\b|"
                          r"\bwhich (?:one|of|image)\b|\bcould you\b|\blet me know\b|"
                          r"\bI (?:need|require)\b[^.\n]{0,40}\bfrom you\b|"
                          r"\bwhat I need from you\b|\bthe one thing (?:only )?you\b|"
                          r"\b(?:paste|send|give|share|upload)\s+(?:me\s+)?(?:your|the)\b"
                          r"[^.\n]{0,40}\b(?:url|link|key|token|address|credential|file|image)\b",
                          re.I)
#: An ADVERB MAY SIT INSIDE THE ADMISSION. "I physically can't reach a ComfyUI instance" is the
#: same punt as "I can't reach it", but `\bI (?:can't…)` demands the two words be adjacent and so
#: missed it. The bounded gap allows "physically", "really", "genuinely" — without letting the
#: match run across a sentence boundary and pair an "I" with someone else's "can't".
_GIVEUP_RX = re.compile(r"\bI\b[^.\n]{0,24}?\b(?:can(?:not|'t)|am unable to|won't be able)\b|"
                        r"\byou(?:'ll| will) need to\b|\byou (?:must|should) (?:install|download|run|set up)\b|"
                        r"\bnot permitted\b|\bno (?:tool|way|access)\b", re.I)
_DEFER_RX = re.compile(r"\blater\b|\bfor now\b|\bskip\b|\bdon'?t worry about\b|\bfirst\b", re.I)


@dataclass
class Finding:
    severity: str
    code: str
    turn: int  # 0 = whole-run
    detail: str
    #: Who owns the fix. "agent" → edit instructions/tools; "environment" → retry, repair the
    #: resource, or ask the user — NEVER edit the agent over these.
    origin: str = ORIGIN_AGENT

    def __str__(self) -> str:
        where = f"turn {self.turn}" if self.turn else "run"
        env = " [env]" if self.origin == ORIGIN_ENV else ""
        return f"[{self.severity.upper():7}]{env} {self.code} ({where}): {self.detail}"


def _acted(turn: Turn) -> bool:
    """Did this turn DO something, not just read, plan and talk? A build tool = any tool whose name
    is not a read/probe/research or bookkeeping."""
    for t in turn.tools:
        if _is_build_tool(t.name):
            return True
    return False


def _is_build_tool(name: str) -> bool:
    """One definition of "this tool changes something", used by every caller — `_acted` and the
    first-build scan in `stall` must never disagree about what counts."""
    n = (name or "").lower()
    return n not in _NON_BUILD_EXACT and not any(h in n for h in _NON_BUILD_HINTS)


def _is_question(text: str) -> bool:
    """Did this message end by putting the ball back in the user's court?

    The `suggest` fence comes off first. It is chips rather than prose, it is now on EVERY turn by
    instruction, and leaving it in place means the end-anchored `\\?$` can only ever see the fence
    — so the question mark that used to end an asking turn is no longer last and never matches."""
    if not text:
        return False
    return bool(_QUESTION_RX.search(_SUGGEST_FENCE_RX.sub("", text).strip()))


# ─────────────────────────────────────────────────────────── determinism / thrash ──────────


def thrash(trace: Trace) -> list[Finding]:
    """Where the model burned reasoning without moving the task — the DeepSeek-loop signature."""
    out: list[Finding] = []

    # 1. Heavy thinking, no action, no user-facing answer. A turn that thought hard, called no
    #    tool, and did not even ask a question is pure spin.
    for t in trace.turns:
        if t.thinking_chars >= 1500 and not t.tools and not t.last_assistant.strip():
            out.append(Finding(WARN, "think_no_act", t.index,
                               f"{t.thinking_chars} chars of reasoning, no tool call, no reply — spin"))

    # 2. The same tool call, identical args, made more than once — the clearest loop tell.
    seen: dict[str, int] = {}
    for c in trace.all_tools:
        seen[c.args_key] = seen.get(c.args_key, 0) + 1
    for key, n in seen.items():
        if n >= 3:
            name = key.split("|", 1)[0]
            out.append(Finding(WARN, "repeated_call", 0,
                               f"{name} called {n}× with identical args — likely a loop"))

    # 3. Plan churn: the plan re-emitted many times, or re-emitted unchanged within one turn.
    total_plans = sum(len(t.plans) for t in trace.turns)
    if total_plans >= max(6, 2 * len(trace.turns)):
        out.append(Finding(WARN, "plan_churn", 0,
                           f"update_plan emitted {total_plans}× over {len(trace.turns)} turns — "
                           "the plan is being re-derived, not followed"))
    for t in trace.turns:
        if len(t.plans) >= 3:
            out.append(Finding(WARN, "plan_churn_turn", t.index,
                               f"plan re-emitted {len(t.plans)}× in one turn"))

    # 4. A run that never ended cleanly — the wedge itself. If the final turn shows ZERO agent
    #    activity (no tool, no text, no reasoning) the run died waiting on the world — a model
    #    call or backend that never answered — which is an environment wedge, not an agent one.
    if trace.truncated:
        last = trace.turns[-1] if trace.turns else None
        silent = last is not None and not last.tools and not last.assistant and not last.thinking_chars
        out.append(Finding(PROBLEM, "run_truncated", 0,
                           "run did not end on its own (timeout/kill) — "
                           + ("no agent activity at all before the wedge: the model call or "
                              "backend likely never answered" if silent else "the agent wedged"),
                           origin=ORIGIN_ENV if silent else ORIGIN_AGENT))
    return out


# ─────────────────────────────────────────────────────────── flexibility / stall ───────────


def stall(trace: Trace) -> list[Finding]:
    """Where the agent waited on the user instead of proceeding — the 'too strict' failure. The
    canonical case: it ends a turn asking for input before ANY build tool has run."""
    out: list[Finding] = []
    first_build = trace.first_tool_turn(
        [c.name for c in trace.all_tools if _is_build_tool(c.name)]
    )

    for t in trace.turns:
        asked = _is_question(t.last_assistant)
        # A turn that ended on a question and DID NOT act, while no build tool has run anywhere
        # up to here, is a stall — it stopped to ask before doing the work.
        if asked and not _acted(t) and (first_build is None or t.index < first_build):
            out.append(Finding(PROBLEM, "blocking_stall", t.index,
                               "ended on a question before doing any build work — should have "
                               "proceeded on a default and let the user redirect"))

    # Re-asking for something the user deferred: a user turn says "later/skip/for now", and a
    # later assistant turn asks for it again instead of using a placeholder/default.
    for i, t in enumerate(trace.turns):
        if _DEFER_RX.search(t.user or ""):
            for later in trace.turns[i + 1:]:
                if _is_question(later.last_assistant) and not _acted(later):
                    out.append(Finding(WARN, "reask_deferred", later.index,
                                       f"user deferred ('{(t.user or '')[:40]}…') but the agent "
                                       "asked again instead of defaulting"))
                    break

    # Never acted at all across the whole run, despite turns — the extreme stall.
    #
    # ORIGIN IS DECIDED BY WHETHER THE AGENT EVER GOT A TURN. When every turn ended in error the
    # model never answered — a provider outage, a dead key — and the agent had no chance to act;
    # calling that an agent fault sends the fix loop off to "improve" instructions that were never
    # executed. Only a run whose turns COMPLETED and still did nothing is the agent's stall.
    if (trace.turns and not any(_acted(t) for t in trace.turns)
            and not _ended_on_approval_gate(trace)):
        every_turn_failed = all(
            t.end_reason == "error" or bool(t.end_error) for t in trace.turns
        )
        out.append(Finding(
            PROBLEM, "never_acted", 0,
            "no turn ever completed — the model never answered, so the agent never got to act"
            if every_turn_failed else
            "no build tool ran in the entire run — all talk, no work",
            origin=ORIGIN_ENV if every_turn_failed else ORIGIN_AGENT,
        ))
    return out


# ─────────────────────────────────────────────────────────── holes ─────────────────────────


def holes(trace: Trace) -> list[Finding]:
    """Dead-ends: tool errors left unhandled, and give-ups where the agent punted to the user
    instead of using a capability it has."""
    out: list[Finding] = []

    for c in trace.all_tools:
        if not c.ok:
            # An error is only a hole if nothing after it recovered — a repair/retry that
            # followed turns it into normal operation.
            later = [x for x in trace.all_tools if x.turn > c.turn or
                     (x.turn == c.turn and trace.all_tools.index(x) > trace.all_tools.index(c))]
            recovered = any(x.name == c.name and x.ok for x in later)
            sev = WARN if recovered else PROBLEM
            tail = "recovered later" if recovered else "never recovered"
            out.append(Finding(sev, "tool_error", c.turn,
                               f"{c.name} failed ({tail}): {(c.result_text or '')[:120]}",
                               origin=ORIGIN_ENV if looks_environmental(c.result_text) else ORIGIN_AGENT))

    # Give-up language with no tool attempt in the same turn — the "you'll need to install these
    # four files" punt when a tool could have done it.
    for t in trace.turns:
        if _GIVEUP_RX.search(t.last_assistant or "") and not t.tools:
            out.append(Finding(PROBLEM, "punted_to_user", t.index,
                               f"told the user to do something, tried no tool: "
                               f"\"{_first_giveup(t.last_assistant)}\""))
    return out


def _first_giveup(text: str) -> str:
    m = _GIVEUP_RX.search(text or "")
    if not m:
        return ""
    s = max(0, m.start() - 10)
    return (text[s:m.end() + 50] or "").strip().replace("\n", " ")[:90]


# ─────────────────────────────────────────────────────────── errors ────────────────────────


def errors(trace: Trace) -> list[Finding]:
    """Runs that ENDED IN ERROR, in the runtime's own words — the agent_end that carried an error
    string (a provider 429, a crashed loop, a dead model call). Kept separate from `holes` because
    nothing the agent did produced it: it is the run's outcome, and its words are exactly what the
    origin triage should read."""
    out: list[Finding] = []
    for t in trace.turns:
        failed = t.end_reason == "error" or bool(t.end_error)
        if not failed:
            continue
        words = t.end_error or "run ended with stopReason=error and no message"
        env = looks_environmental(words)
        out.append(Finding(PROBLEM, "run_error", t.index,
                           f"turn ended in error: {words[:140]}",
                           origin=ORIGIN_ENV if env else ORIGIN_AGENT))
    return out


# ─────────────────────────────────────────────────────────── cost ──────────────────────────


def cost(trace: Trace) -> list[Finding]:
    """Efficiency, as INFO — the numbers a model-vs-model diff is read on."""
    turns = len(trace.turns)
    tools = len(trace.all_tools)
    think = sum(t.thinking_chars for t in trace.turns)
    out = [Finding(INFO, "cost", 0,
                   f"{turns} turns · {tools} tool calls · {think:,} chars reasoning")]
    for t in trace.turns:
        if t.thinking_chars >= 4000:
            out.append(Finding(INFO, "heavy_turn", t.index,
                               f"{t.thinking_chars:,} chars reasoning this turn"))
    return out


# ─────────────────────────────────────────────────────────── the whole diagnosis ───────────


def diagnose(trace: Trace) -> list[Finding]:
    """Every signal, most-severe first — the full read on one run."""
    findings = thrash(trace) + stall(trace) + holes(trace) + errors(trace) + cost(trace)
    order = {PROBLEM: 0, WARN: 1, INFO: 2}
    return sorted(findings, key=lambda f: (order.get(f.severity, 9), f.turn))
