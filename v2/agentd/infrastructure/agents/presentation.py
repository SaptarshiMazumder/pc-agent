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

import colorsys
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

# Avatar-colour policy: mid-bright so the fixed DARK avatar text stays legible on
# every hue. Saturation/lightness are shared with the client fallback (see
# renderer/src/lib/agentPresentation.ts) so a server colour and a client-computed
# fallback are visually identical for the same hue.
COLOR_SAT = 0.62
COLOR_LIGHT = 0.58
_MIN_HUE_SEP = 24.0  # degrees two agents' hues must stay apart to read as distinct
_GOLDEN = 137.508  # golden-angle walk spreads collisions evenly around the wheel

# main is the brand generalist — it always wears the product lime, and no other agent
# is allowed to (that hue is reserved so lime == "the default agent" stays a signal).
MAIN_COLOR = "#a3e635"

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
    """Persist the generated presentation next to the definition (atomic, replaces)."""
    path = Path(agent_dir) / SIDECAR
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)


def update_sidecar(agent_dir: Path, **fields) -> dict:
    """Merge fields into the sidecar (colour and tagline are written independently)."""
    data = read_sidecar(agent_dir)
    data.update(fields)
    write_sidecar(agent_dir, data)
    return data


# --- avatar colour: unique, stable, deterministic-ish -------------------------------
# Assigned ONCE per agent and persisted, so an agent keeps its colour forever even as
# others are added/removed. The base hue is a hash of the id (so it MATCHES the
# client's fallback for non-colliding agents — no colour flip when the server value
# arrives); a golden-angle walk resolves any clash with an already-taken hue, which is
# the whole reason this lives server-side: only the daemon sees every agent at once.


def _hue_from_id(agent_id: str) -> float:
    h = 0
    for ch in agent_id:
        h = (h * 31 + ord(ch)) & 0xFFFFFFFF
    return float(h % 360)


def _hue_distance(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return min(d, 360 - d)


def hsl_to_hex(hue: float, sat: float = COLOR_SAT, light: float = COLOR_LIGHT) -> str:
    r, g, b = colorsys.hls_to_rgb((hue % 360) / 360.0, light, sat)
    return f"#{round(r * 255):02x}{round(g * 255):02x}{round(b * 255):02x}"


def hex_to_hue(hex_color: str) -> float | None:
    """Hue (0-360) of a #rrggbb colour, so an AUTHORED colour joins the 'taken' set."""
    try:
        s = hex_color.lstrip("#")
        r, g, b = (int(s[i : i + 2], 16) / 255 for i in (0, 2, 4))
    except (ValueError, IndexError):
        return None
    h, _, _ = colorsys.rgb_to_hls(r, g, b)
    return h * 360


def assign_hue(agent_id: str, taken: list[float]) -> float:
    """A hue for this agent at least _MIN_HUE_SEP from every taken hue. Starts at the
    id's hash (stable + matches the client fallback), walks the golden angle on clash."""
    hue = _hue_from_id(agent_id)
    for _ in range(64):  # bounded; 64 golden steps cover the wheel
        if all(_hue_distance(hue, t) >= _MIN_HUE_SEP for t in taken):
            break
        hue = (hue + _GOLDEN) % 360
    return hue


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
            prompt=_PROMPT.format(
                name=name or "?", description=description or "-", identity=identity[:4000] or "-"
            ),
        )
        match = re.search(r"\{.*\}", out or "", re.DOTALL)
        if not match:
            return {}
        return clean_presentation(json.loads(match.group(0)))
    except Exception:  # noqa: BLE001 — presentation is décor; degrade to nothing
        log.debug("presentation generation failed for %s", name, exc_info=True)
        return {}
