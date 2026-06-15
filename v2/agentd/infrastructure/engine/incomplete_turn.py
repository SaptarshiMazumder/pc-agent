"""Incomplete-turn classifiers and verbatim retry instructions.

Port of OpenClaw's src/agents/embedded-agent-runner/run/incomplete-turn.ts.
After an assistant turn that produced NO tool calls, these detect whether the
turn is actually a finished answer or an incomplete one that should be retried
with a typed instruction injected as the next user message.
"""

from __future__ import annotations

import re

from agentd.domain.messages import AssistantMessage, TextContent, ThinkingContent

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
INCOMPLETE_TURN_FALLBACK_TEXT = "⚠️ Agent couldn't generate a response. Please try again."

# --- Verbatim limits ---------------------------------------------------------

DEFAULT_PLANNING_ONLY_RETRY_LIMIT = 1
DEFAULT_REASONING_ONLY_RETRY_LIMIT = 2
DEFAULT_EMPTY_RESPONSE_RETRY_LIMIT = 1
MAX_BEFORE_AGENT_FINALIZE_REVISIONS = 3

# --- Detection regexes (ported) ----------------------------------------------

PLANNING_ONLY_PROMISE_RE = re.compile(
    r"\b(?:i(?:'ll| will)|let me|i(?:'m| am)\s+going to|first[, ]+i(?:'ll| will)|"
    r"next[, ]+i(?:'ll| will)|i can do that)\b",
    re.IGNORECASE,
)
PLANNING_ONLY_ACTION_VERB_RE = re.compile(
    r"\b(?:inspect|read|open|check|examine|review|search|look|find|fetch|browse|navigate|"
    r"gather|collect|fix|implement|create|write|edit|update|add|build|run|execute|install|"
    r"test|verify|analyze|list|scan|query|call)\b",
    re.IGNORECASE,
)
# Markers that indicate the text is actually delivering results, not a promise.
_COMPLETION_MARKER_RE = re.compile(
    r"\b(?:done|finished|completed|here(?:'s| is| are)|the result|in summary|to summarize)\b",
    re.IGNORECASE,
)
_URL_RE = re.compile(r"https?://", re.IGNORECASE)

PLANNING_ONLY_MAX_CHARS = 700


def _visible_text(m: AssistantMessage) -> str:
    return "".join(b.text for b in m.content if isinstance(b, TextContent)).strip()


def _has_thinking(m: AssistantMessage) -> bool:
    return any(isinstance(b, ThinkingContent) and b.thinking.strip() for b in m.content)


def is_planning_only(m: AssistantMessage) -> bool:
    """Visible text that promises to act but took no action and delivered no result."""
    if m.tool_calls:
        return False
    text = _visible_text(m)
    if not text or len(text) > PLANNING_ONLY_MAX_CHARS:
        return False
    if "```" in text or _URL_RE.search(text) or _COMPLETION_MARKER_RE.search(text):
        return False
    return bool(PLANNING_ONLY_PROMISE_RE.search(text) and PLANNING_ONLY_ACTION_VERB_RE.search(text))


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


def classify_incomplete_turn(m: AssistantMessage) -> str | None:
    """Return the retry kind for an incomplete no-tool-call turn, else None."""
    if is_planning_only(m):
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
