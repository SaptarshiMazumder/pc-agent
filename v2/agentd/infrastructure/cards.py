"""Tool cards — a tool's self-description, kept OUT of the code.

A *card* is a Markdown file (frontmatter + body) that carries what a tool says about itself:
the one-line ``summary`` (frontmatter) and an optional instruction ``prompt`` block (the body)
injected into the system prompt when that tool is present. This is how we kill the hardcoded
``TOOL_SUMMARIES`` dict and the ``if name == "..."`` prompt blocks: the prompt builder just
GATHERS cards generically.

WHERE a card lives encodes internal-vs-plugin, and nothing else:
  * INTERNAL tools -> ``<V2_ROOT>/tools/<name>.md`` (code stays in infrastructure/tools/; the
    card sits OUTSIDE it, in one discoverable folder),
  * EXTERNAL plugins -> the plugin folder carries its own card.

Format (same frontmatter style as SKILL.md):
    ---
    summary: Read file contents
    ---
    ## Optional instruction block shown when this tool is present
    ...markdown body...
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger("agentd")

# <V2_ROOT>/tools  -- this file is agentd/infrastructure/cards.py, so parents[2] is v2/.
_DEFAULT_TOOLS_DIR = Path(__file__).resolve().parents[2] / "tools"


@dataclass(frozen=True)
class ToolCard:
    summary: str = ""        # the one-line ## Tooling description
    prompt: str = ""         # an instruction block injected when the tool is present ("" = none)
    scripts: tuple = ()      # declared helper scripts bundled with the tool (relative to root)
    data: tuple = ()         # declared data files bundled with the tool (relative to root)
    root: Path | None = None  # the card's own folder — resolve scripts/data against this

    def resource(self, name: str) -> Path | None:
        """Absolute path to a bundled asset (script/data) declared by this card, or None."""
        return (self.root / name) if self.root is not None else None


def _parse_frontmatter(text: str) -> tuple[dict, str]:
    """(frontmatter dict, body) — the ``key: value`` block between leading ``---`` fences."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            return meta, "\n".join(lines[i + 1:])
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()
    return {}, text                                    # no closing fence -> no frontmatter


def _csv(value: str) -> tuple:
    """Parse a comma-separated frontmatter list (``scripts: a.py, b.py``) -> tuple of names."""
    return tuple(x.strip() for x in (value or "").split(",") if x.strip())


def _load_card(path: Path) -> ToolCard | None:
    try:
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
    except OSError:
        return None
    root = path.parent
    card = ToolCard(
        summary=meta.get("summary", "").strip(),
        prompt=body.strip(),
        scripts=_csv(meta.get("scripts", "")),
        data=_csv(meta.get("data", "")),
        root=root,
    )
    for rel in (*card.scripts, *card.data):          # declared-but-missing => warn (never fatal)
        if not (root / rel).exists():
            log.warning("cards: %s declares missing asset %r", path.name, rel)
    return card


def load_cards(tools_dir=None) -> dict[str, ToolCard]:
    """Map ``tool_name -> ToolCard`` from a folder of cards (``<name>.md`` or ``<name>/card.md``).
    Default folder = ``<V2_ROOT>/tools``. Absent folder -> ``{}`` (tools just fall back to their
    own ``description``). Best-effort: a bad card is skipped, never fatal."""
    d = Path(tools_dir) if tools_dir else _DEFAULT_TOOLS_DIR
    out: dict[str, ToolCard] = {}
    if not d.is_dir():
        return out
    for p in sorted(d.glob("*.md")):                   # tools/<name>.md
        card = _load_card(p)
        if card is not None:
            out[p.stem] = card
    for sub in sorted(d.iterdir()):                    # tools/<name>/card.md  (rich: + scripts/data)
        if sub.is_dir() and (sub / "card.md").is_file() and sub.name not in out:
            card = _load_card(sub / "card.md")
            if card is not None:
                out[sub.name] = card
    return out
