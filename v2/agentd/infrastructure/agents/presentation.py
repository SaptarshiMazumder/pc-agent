"""Agent display presentation — tagline + starter suggestions, generated once.

The empty-chat screen and agent pickers need a short "what is this agent" line and
a few starter prompts. Those are DISPLAY data, owned by the server so every client
shows the same thing (same principle as session auto-titles):

  precedence:  agent.toml (authored)  >  presentation.json sidecar (generated)  >  ""

The sidecar is written by the daemon the first time it sees an agent without a
tagline (gateway `_maybe_generate_presentations`) — one cheap LLM call over the
agent's own identity. Authored fields always win, so hand-tuning is just editing
agent.toml. Everything here is best-effort and never raises into a caller.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from agentd.infrastructure.llm.oneshot import text_complete

log = logging.getLogger("agentd")

SIDECAR = "presentation.json"
MAX_TAGLINE_CHARS = 48
MAX_SUGGESTIONS = 3
MAX_SUGGESTION_CHARS = 64

_PROMPT = """You write launcher-UI copy for an AI agent. Based on the agent's identity below, return STRICT JSON, nothing else:

{{"tagline": "...", "suggestions": ["...", "...", "..."]}}

Rules:
- tagline: 2-5 words, lowercase, telegraphic — the agent's specialty at a glance (e.g. "finance · gmail", "scientific figures", "front desk").
- suggestions: exactly 3 starter prompts a user of THIS agent would actually click. Imperative, specific to its job, at most 8 words each, no quotes inside.

Agent name: {name}
Description: {description}

Identity:
{identity}
"""


def read_sidecar(agent_dir: Path | None) -> dict:
    """The generated presentation for an agent dir ({} if none). Never raises."""
    if agent_dir is None:
        return {}
    try:
        data = json.loads((Path(agent_dir) / SIDECAR).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def write_sidecar(agent_dir: Path, data: dict) -> None:
    """Persist the generated presentation next to the definition (atomic)."""
    path = Path(agent_dir) / SIDECAR
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def clean_presentation(raw: dict) -> dict:
    """Validate + trim a candidate {tagline, suggestions} — {} if unusable."""
    if not isinstance(raw, dict):
        return {}
    tagline = " ".join(str(raw.get("tagline") or "").split()).strip(" .\"'")
    tagline = tagline[:MAX_TAGLINE_CHARS].rstrip()
    suggestions = []
    for s in raw.get("suggestions") or []:
        s = " ".join(str(s).split()).strip(" .\"'")
        if s:
            suggestions.append(s[:MAX_SUGGESTION_CHARS].rstrip())
        if len(suggestions) >= MAX_SUGGESTIONS:
            break
    if not tagline:
        return {}
    return {"tagline": tagline, "suggestions": suggestions}


def generate_presentation(name: str, description: str, identity: str, model: str) -> dict:
    """One-shot generate {tagline, suggestions} from the agent's own identity.
    Returns {} when the identity is empty or anything fails — NEVER raises."""
    identity = (identity or "").strip()
    if not identity and not (description or "").strip():
        return {}
    try:
        out = text_complete(
            model=model,
            prompt=_PROMPT.format(name=name or "?", description=description or "-",
                                  identity=identity[:4000] or "-"),
        )
        match = re.search(r"\{.*\}", out or "", re.DOTALL)
        if not match:
            return {}
        return clean_presentation(json.loads(match.group(0)))
    except Exception:  # noqa: BLE001 — presentation is décor; degrade to nothing
        log.debug("presentation generation failed for %s", name, exc_info=True)
        return {}
