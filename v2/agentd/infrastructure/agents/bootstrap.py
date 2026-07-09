"""load_bootstrap — read an agent's identity/instruction files into prompt text.

An agent directory may contain editable markdown that shapes the agent's persona +
rules: IDENTITY.md (who it is), AGENTS.md (operating rules), USER.md (user/context),
MEMORY.md (long-term learned facts). They are concatenated — each under its own
heading, size-capped — into one block injected near the top of the system prompt.

HEARTBEAT.md is intentionally NOT loaded here: it's only for autonomous heartbeat
runs (Phase 2), not normal turns.
"""

from __future__ import annotations

from pathlib import Path

# (filename, heading) in injection order: persona first, then rules, then context.
_BOOTSTRAP_FILES = (
    ("IDENTITY.md", "Identity"),
    ("AGENTS.md", "Operating rules"),
    ("USER.md", "User & context"),
    ("MEMORY.md", "Long-term memory"),
)

_PER_FILE_CAP = 20_000
_TOTAL_CAP = 100_000


def load_bootstrap(
    agent_dir: Path, per_file_cap: int = _PER_FILE_CAP, total_cap: int = _TOTAL_CAP
) -> str:
    """Concatenate an agent's bootstrap files into one prompt block ("" if none)."""
    parts: list[str] = []
    total = 0
    for filename, heading in _BOOTSTRAP_FILES:
        f = agent_dir / filename
        if not f.is_file():
            continue
        try:
            content = f.read_text(encoding="utf-8").strip()
        except OSError:
            continue
        if not content:
            continue
        if len(content) > per_file_cap:
            content = content[:per_file_cap].rstrip() + "\n…[truncated]"
        block = f"## {heading} ({filename})\n{content}"
        if parts and total + len(block) > total_cap:
            break
        parts.append(block)
        total += len(block)
    if not parts:
        return ""
    return "# Agent Definition\n" + "\n\n".join(parts)


def load_heartbeat(agent_dir: Path, per_file_cap: int = _PER_FILE_CAP) -> str:
    """Read HEARTBEAT.md — the agent's autonomous-tick checklist ("" if none).

    Loaded separately from the normal bootstrap because it's injected ONLY on a
    heartbeat run, never in interactive chat.
    """
    f = agent_dir / "HEARTBEAT.md"
    if not f.is_file():
        return ""
    try:
        content = f.read_text(encoding="utf-8").strip()
    except OSError:
        return ""
    if not content:
        return ""
    if len(content) > per_file_cap:
        content = content[:per_file_cap].rstrip() + "\n…[truncated]"
    return f"# Heartbeat checklist (HEARTBEAT.md)\n{content}"
