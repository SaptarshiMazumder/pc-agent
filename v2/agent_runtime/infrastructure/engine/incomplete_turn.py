"""Incomplete-turn classifiers and verbatim retry instructions.

Port of OpenClaw's src/agents/embedded-agent-runner/run/incomplete-turn.ts.
After an assistant turn that produced NO tool calls, these detect whether the
turn is actually a finished answer or an incomplete one that should be retried
with a typed instruction injected as the next user message.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

from agent_runtime.domain.messages import AssistantMessage, TextContent, ThinkingContent

# --- Verbatim instruction strings (incomplete-turn.ts) -----------------------

PLANNING_ONLY_RETRY_INSTRUCTION = (
    "The previous assistant turn only described the plan. Do not restate the plan. "
    "Act now: take the first concrete tool action you can. If a real blocker prevents "
    "action, reply with the exact blocker in one sentence."
)
REASONING_ONLY_RETRY_INSTRUCTION = (
    "The previous assistant turn recorded reasoning but did not produce a user-visible "
    "answer. Continue from that partial turn and produce the visible answer now. Do not "
    "restate the reasoning or restart from scratch."
)
EMPTY_RESPONSE_RETRY_INSTRUCTION = (
    "The previous attempt did not produce a user-visible answer. Continue from the current "
    "state and produce the visible answer now. Do not restart from scratch."
)
BEFORE_AGENT_FINALIZE_RETRY_PROMPT_PREFIX = (
    "Before accepting the previous final answer, apply this revision request and produce the "
    "revised final answer. Do not repeat completed work or rerun tools unless the request "
    "explicitly requires it."
)
INCOMPLETE_TURN_FALLBACK_TEXT = "⚠️ Agent couldn't generate a response."

# What each failure mode MEANS, in the words a user can act on. "Please try again" was the
# entire message here once, which is advice to repeat something that will fail identically —
# a dead API key looked like flakiness for days because of it.
_EMPTY_RUN_CAUSE = {
    "reasoning_only": (
        "The model kept returning its reasoning without ever producing an answer or calling "
        "a tool. This usually means the model is too small for the size of this request."
    ),
    "planning_only": (
        "The model described a plan but never carried it out, on every attempt."
    ),
    "empty_response": ("The model returned an entirely empty response on every attempt."),
}


def describe_empty_run(kind: str | None, served_by: tuple[str, str] | None) -> str:
    """One honest paragraph about why a run ended with nothing to show.

    ``kind`` is the last incomplete-turn classification; ``served_by`` is ``(from, to)`` when
    model failover put a different model in charge. The pair is what makes the failure
    diagnosable: "reasoning only" alone reads as a model quirk, but "reasoning only, and your
    configured model was swapped out because it errored" names the actual thing to fix.
    """
    parts = []
    if kind:
        parts.append(_EMPTY_RUN_CAUSE.get(kind, f"The run ended incomplete ({kind})."))
    if served_by:
        origin, took_over = served_by
        parts.append(
            f"Note: `{origin}` could not serve this request, so `{took_over}` answered instead. "
            f"Fix `{origin}` — the fallback is not equivalent."
        )
    if not parts:
        return ""
    parts.append("Re-sending the same message will most likely fail the same way.")
    return " ".join(parts)

# --- Verbatim limits ---------------------------------------------------------

DEFAULT_PLANNING_ONLY_RETRY_LIMIT = 1
DEFAULT_REASONING_ONLY_RETRY_LIMIT = 2
DEFAULT_EMPTY_RESPONSE_RETRY_LIMIT = 1
MAX_BEFORE_AGENT_FINALIZE_REVISIONS = 3

# --- Detection regexes & data — ported verbatim from OpenClaw incomplete-turn.ts ------
# These mirror the reference exactly; firing is then GATED (see the guard functions below)
# so the "stop planning, act now" nudge only applies to agentic task-runners on an actionable
# prompt — NOT to a conversational turn that legitimately hands back to the user.

PLANNING_ONLY_PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|let me|i(?:'m| am)\s+going to|first[, ]+i(?:'ll| will)|"
    r"next[, ]+i(?:'ll| will)|i can do that)\b",
    re.IGNORECASE,
)
PLANNING_ONLY_ACTION_VERB_RE = re.compile(
    r"\b(?:inspect|investigate|check|look(?:\s+into|\s+at)?|read|search|find|debug|fix|patch|"
    r"update|change|edit|write|implement|run|test|verify|review|analy(?:s|z)e|summari(?:s|z)e|"
    r"explain|answer|show|share|report|prepare|capture|take|refactor|restart|deploy|ship)\b",
    re.IGNORECASE,
)
# Markers that show the turn is DELIVERING a result (or stating a real blocker), not promising.
PLANNING_ONLY_COMPLETION_RE = re.compile(
    r"\b(?:done|finished|implemented|updated|fixed|changed|ran|verified|found|"
    r"here(?:'s| is) what|blocked by|the blocker is)\b",
    re.IGNORECASE,
)
PLANNING_ONLY_HEADING_RE = re.compile(r"^(?:plan|steps?|next steps?)\s*:", re.IGNORECASE)
PLANNING_ONLY_BULLET_RE = re.compile(r"^(?:[-*•]\s+|\d+[.)]\s+)")
PLANNING_ONLY_MAX_CHARS = 700

# Whether the USER's prompt is actually a request to ACT (else there's nothing to nudge toward).
ACTIONABLE_PROMPT_DIRECTIVE_RE = re.compile(
    r"^\s*(?:please\s+)?(?:check|look(?:\s+into|\s+at)?|read|write|edit|update|fix|investigate|"
    r"debug|run|search|find|implement|add|remove|refactor|explain|summari(?:s|z)e|analy(?:s|z)e|"
    r"review|tell|show|make|restart|deploy|prepare)\b",
    re.IGNORECASE,
)
ACTIONABLE_PROMPT_REQUEST_RE = re.compile(
    r"\b(?:can|could|would|will)\s+you\b|\b(?:please|pls)\b|\b(?:help|explain|summari(?:s|z)e|"
    r"analy(?:s|z)e|review|investigate|debug|fix|check|look(?:\s+into|\s+at)?|read|write|edit|"
    r"update|run|search|find|implement|add|remove|refactor|show|tell me|walk me through)\b",
    re.IGNORECASE,
)

# Which provider/models get planning-only recovery at all (OpenClaw: the Gemini family — it
# tends to emit plan-only turns. litellm names them "gemini/..." / "google/...").
_GEMINI_PROVIDER_IDS = frozenset(
    {"gemini", "google", "google-vertex", "vertex_ai", "google-antigravity", "google-gemini-cli"}
)
_GEMINI_MODEL_ID_RE = re.compile(r"^gemini(?:[.-]|$)", re.IGNORECASE)

# Short approval prompts ("go ahead", multilingual) that mean "just do it" — ported set.
ACK_EXECUTION_NORMALIZED_SET = frozenset(
    {
        "ok",
        "okay",
        "ok do it",
        "okay do it",
        "do it",
        "go ahead",
        "please do",
        "sounds good",
        "sounds good do it",
        "ship it",
        "fix it",
        "make it so",
        "yes do it",
        "yep do it",
        "تمام",
        "حسنا",
        "حسنًا",
        "امض قدما",
        "نفذها",
        "mach es",
        "leg los",
        "los geht s",
        "weiter",
        "やって",
        "進めて",
        "そのまま進めて",
        "allez y",
        "vas y",
        "fais le",
        "continue",
        "hazlo",
        "adelante",
        "sigue",
        "faz isso",
        "vai em frente",
        "pode fazer",
        "해줘",
        "진행해",
        "계속해",
    }
)
_ACK_PUNCT_RE = re.compile(r"[^\w\s]+", re.UNICODE)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class PlanningContext:
    """The signals OpenClaw gates planning-only retries on (besides the assistant text): the
    user's prompt (must be an actual request to act), the model, and the agent's execution
    contract. All-empty (the default) => the regex-only legacy behaviour."""

    user_prompt: str = ""
    model: str = ""
    execution_contract: str = ""


def _visible_text(m: AssistantMessage) -> str:
    return "".join(b.text for b in m.content if isinstance(b, TextContent)).strip()


def _has_thinking(m: AssistantMessage) -> bool:
    return any(isinstance(b, ThinkingContent) and b.thinking.strip() for b in m.content)


def _has_structured_planning_format(text: str) -> bool:
    """A 'Plan:' heading, or a bulleted list, paired with a planning-cue line (OpenClaw)."""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        return False
    bullets = sum(1 for ln in lines if PLANNING_ONLY_BULLET_RE.match(ln))
    has_cue = any(PLANNING_ONLY_PROMISE_RE.search(ln) for ln in lines)
    has_heading = bool(PLANNING_ONLY_HEADING_RE.match(lines[0]))
    return (has_heading and has_cue) or (bullets >= 2 and has_cue)


def is_planning_only(m: AssistantMessage) -> bool:
    """Visible text that PROMISES to act but took no action and delivered no result. This is
    the REGEX core only (faithful to OpenClaw). The firing GUARDS — the agent must be an
    agentic task-runner AND the user's prompt must be a request to act — are applied in
    ``classify_incomplete_turn`` via ``PlanningContext`` (no ctx => regex alone, legacy)."""
    if m.tool_calls:
        return False
    text = _visible_text(m)
    if not text or len(text) > PLANNING_ONLY_MAX_CHARS or "```" in text:
        return False
    if PLANNING_ONLY_COMPLETION_RE.search(text):  # delivering a result / stating a blocker
        return False
    structured = _has_structured_planning_format(text)
    if not PLANNING_ONLY_PROMISE_RE.search(text) and not structured:
        return False
    if not structured and not PLANNING_ONLY_ACTION_VERB_RE.search(text):
        return False
    return True


def is_incomplete_turn_recovery_supported_provider_model(model: str) -> bool:
    """Port of OpenClaw's provider/model gate: the Gemini family gets planning-only recovery.
    Other providers opt in via the execution contract (see below)."""
    provider, sep, model_id = (model or "").partition("/")
    provider = provider.strip().lower()
    if not sep:  # bare model id (no "provider/" prefix)
        return bool(_GEMINI_MODEL_ID_RE.match(provider))
    return provider in _GEMINI_PROVIDER_IDS and bool(_GEMINI_MODEL_ID_RE.match(model_id))


def should_apply_planning_only_guard(model: str = "", execution_contract: str = "") -> bool:
    """Port of OpenClaw shouldApplyPlanningOnlyRetryGuard: only agentic task-runners get the
    'stop planning, act now' nudge — a strict-agentic contract, or a supported (Gemini) model.
    A plain conversational agent on another model is exempt."""
    if execution_contract == "strict-agentic":
        return True
    return is_incomplete_turn_recovery_supported_provider_model(model)


def _normalize_ack(text: str) -> str:
    s = unicodedata.normalize("NFKC", text).strip()
    s = _ACK_PUNCT_RE.sub(" ", s)
    return _WS_RE.sub(" ", s).strip().lower()


def is_likely_execution_ack_prompt(text: str) -> bool:
    """A short multilingual approval like 'go ahead' / 'やって' meaning 'just do it'."""
    t = text.strip()
    if not t or len(t) > 80 or "\n" in t or "?" in t:
        return False
    return _normalize_ack(t) in ACK_EXECUTION_NORMALIZED_SET


def is_likely_actionable_user_prompt(text: str) -> bool:
    """Port of OpenClaw isLikelyActionableUserPrompt: did the user actually ask the agent to
    DO something — a directive, a request, a question, or a short approval? If not, there is
    nothing for the planning-only nudge to push toward, so it must not fire."""
    t = text.strip()
    if not t:
        return False
    if is_likely_execution_ack_prompt(t) or "?" in t:
        return True
    return bool(ACTIONABLE_PROMPT_DIRECTIVE_RE.search(t) or ACTIONABLE_PROMPT_REQUEST_RE.search(t))


def is_reasoning_only(m: AssistantMessage) -> bool:
    """Recorded thinking but produced no user-visible text."""
    if m.tool_calls or m.stop_reason == "error":
        return False
    return _has_thinking(m) and not _visible_text(m)


def is_empty_response(m: AssistantMessage) -> bool:
    """No text, no thinking, no tool calls; a clean but empty stop."""
    if m.tool_calls or m.stop_reason != "stop":
        return False
    return not _visible_text(m) and not _has_thinking(m)


def _planning_only_guards_pass(ctx: PlanningContext | None) -> bool:
    """OpenClaw's planning-only firing gates (beyond the regex): the agent must be an agentic
    task-runner (model/contract), and the user's prompt must be a request to act. No ctx =>
    legacy regex-only behaviour (unchanged for callers that don't pass context)."""
    if ctx is None:
        return True
    if not should_apply_planning_only_guard(ctx.model, ctx.execution_contract):
        return False
    if ctx.user_prompt and not is_likely_actionable_user_prompt(ctx.user_prompt):
        return False
    return True


def is_injected_prompt(text: str) -> bool:
    """Was this 'user' message written by the RUNTIME rather than the user?

    Retry nudges and liveness steering are persisted as UserMessages — that is how the model
    receives them. The cost is that they then look like things the user said, and the
    planning-only guard decides whether to nudge by reading the last user message. So one
    nudge made the next one look unwarranted, and the recovery layer switched itself off for
    the rest of the session. (A real transcript had five of them stacked up, and had not been
    able to nudge since the first.)

    Matched against the instruction constants themselves, not copies, so editing one keeps
    this correct — and so transcripts ALREADY on disk are recognised without a migration.
    """
    s = (text or "").strip()
    if not s:
        return False
    if s.startswith("[liveness]"):  # steering from a liveness observer
        return True
    if s.startswith(BEFORE_AGENT_FINALIZE_RETRY_PROMPT_PREFIX):
        return True
    return any(s == instruction.strip() for instruction in RETRY_INSTRUCTIONS.values())


def classify_incomplete_turn(m: AssistantMessage, ctx: PlanningContext | None = None) -> str | None:
    """Return the retry kind for an incomplete no-tool-call turn, else None. ``planning_only``
    is GATED via ``ctx`` (OpenClaw): it fires only for an agentic task-runner on an actionable
    prompt. ``reasoning_only`` / ``empty_response`` recover genuinely lost answers and are not
    gated this way (every agent wants them)."""
    if is_planning_only(m) and _planning_only_guards_pass(ctx):
        return "planning_only"
    if is_reasoning_only(m):
        return "reasoning_only"
    if is_empty_response(m):
        return "empty_response"
    return None


RETRY_INSTRUCTIONS = {
    "planning_only": PLANNING_ONLY_RETRY_INSTRUCTION,
    "reasoning_only": REASONING_ONLY_RETRY_INSTRUCTION,
    "empty_response": EMPTY_RESPONSE_RETRY_INSTRUCTION,
}
RETRY_LIMITS = {
    "planning_only": DEFAULT_PLANNING_ONLY_RETRY_LIMIT,
    "reasoning_only": DEFAULT_REASONING_ONLY_RETRY_LIMIT,
    "empty_response": DEFAULT_EMPTY_RESPONSE_RETRY_LIMIT,
}


def resolve_max_run_loop_iterations(profile_count: int = 1) -> int:
    """OpenClaw resolveMaxRunRetryIterations: min(160, max(32, 24 + 8*profiles))."""
    scaled = 24 + max(1, profile_count) * 8
    return min(160, max(32, scaled))


def build_before_finalize_retry_prompt(reason: str) -> str:
    return f"{BEFORE_AGENT_FINALIZE_RETRY_PROMPT_PREFIX}\n\n{reason}"
