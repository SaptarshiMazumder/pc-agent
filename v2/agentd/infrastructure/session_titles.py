"""Auto-titling for chat sessions — the LM-Studio / ChatGPT-style name a client shows
instead of the opaque session key (``desk-a1b2c3d4``).

SERVER-side on purpose: a title is data about the CONVERSATION (which lives on the
daemon), and generating it is an LLM call — clients never touch a model. So every
client (terminal, desktop, future web) gets the same name for free.

Two tiers, both here:
  * ``snippet_title`` — the first user message, trimmed. Instant, free, always available;
    used as the immediate placeholder AND as the fallback when the model call fails.
  * ``generate_title`` — a cheap one-shot LLM summary into 3–6 words (what modern
    ChatGPT does). Async on the daemon, so it never slows a turn.

A user rename always wins over both (stored with ``manual=True``; see local_store).
"""

from __future__ import annotations

import logging
import re

from agentd.infrastructure.llm.oneshot import text_complete

log = logging.getLogger("agentd")

_PROMPT = (
    "Write a short, specific title (3 to 6 words) summarizing this conversation. "
    "Output ONLY the title text — no quotes, no surrounding punctuation, no 'Title:' prefix.\n\n"
    "User: {user}\n"
    "Assistant: {assistant}\n\n"
    "Title:"
)

MAX_TITLE_CHARS = 60


def snippet_title(text: str, limit: int = 48) -> str:
    """The instant fallback: the first user message on one line, trimmed."""
    collapsed = " ".join((text or "").split())
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[:limit].rstrip() + "…"


def clean_title(raw: str) -> str:
    """Normalize a model's title output: one line, no wrapping quotes, no trailing
    punctuation, length-capped. A small model sometimes adds 'Title:' or quotes."""
    title = " ".join((raw or "").splitlines()).strip()
    title = re.sub(r"^(title|chat title)\s*[:\-]\s*", "", title, flags=re.IGNORECASE)
    title = title.strip().strip("\"'").strip()
    title = re.sub(r"[.\s]+$", "", title)              # drop a trailing period/space
    return title[:MAX_TITLE_CHARS].rstrip()


def generate_title(first_user: str, first_assistant: str, model: str) -> str:
    """A concise title for the exchange. Falls back to the message snippet on any error
    or an empty model response — this NEVER raises, so a titling failure can't affect a run."""
    if not (first_user or "").strip():
        return ""
    try:
        raw = text_complete(
            model=model,
            prompt=_PROMPT.format(user=first_user[:1500], assistant=(first_assistant or "")[:1500]),
            timeout=30,
        )
        return clean_title(raw) or snippet_title(first_user)
    except Exception as e:  # noqa: BLE001 — titling is best-effort, snippet is the safety net
        log.info("session title generation failed (%s); using the message snippet", e)
        return snippet_title(first_user)
