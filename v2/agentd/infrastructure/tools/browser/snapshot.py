"""Snapshot planning + formatting for the browser tool.

We use Playwright's NATIVE AI aria-snapshot (`locator.aria_snapshot(mode="ai")`),
which emits durable `[ref=eN]` markers that resolve via the `aria-ref=eN` selector
(no fragile role+name+nth bookkeeping). `boxes=True` adds `[box=x,y,w,h]` for
coordinate clicks; `/url:` lines carry link targets. This module only PLANS the
call and POST-PROCESSES the text (urls/labels/depth/char-cap).
"""

from __future__ import annotations

import re

# constants.ts
DEFAULT_AI_SNAPSHOT_MAX_CHARS = 40_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS = 8_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH = 6

# snapshot-roles.ts (verbatim) — kept for reference / other callers.
INTERACTIVE_ROLES = {
    "button", "checkbox", "combobox", "link", "listbox", "menuitem",
    "menuitemcheckbox", "menuitemradio", "option", "radio", "searchbox",
    "slider", "spinbutton", "switch", "tab", "textbox", "treeitem",
}
CONTENT_ROLES = {
    "article", "cell", "columnheader", "gridcell", "heading", "listitem",
    "main", "navigation", "region", "rowheader",
}
STRUCTURAL_ROLES = {
    "application", "directory", "document", "generic", "grid", "group",
    "ignored", "list", "menu", "menubar", "none", "presentation", "row",
    "rowgroup", "table", "tablist", "toolbar", "tree", "treegrid",
}

_NETWORKIDLE_TIMEOUT_MS = 8_000

# A line in mode="ai" output: `  - button "Submit" [ref=e2] [box=...]:`
_INDENT_RE = re.compile(r"^(\s*)-\s")
# An unnamed structural container line (drop when compact): `- generic [ref=e1]:`
_BARE_STRUCTURAL_RE = re.compile(
    r"^\s*-\s+(generic|none|presentation|group)\b(?!\s+\")(?:\s+\[[^\]]*\])*:?\s*$"
)


def resolve_snapshot_plan(params: dict) -> dict:
    """Translate the tool's snapshot params into the provider's snapshot kwargs.

    Active levers: depth + max_chars (efficient mode lowers both), plus the
    presentation toggles urls/labels and the scope selectors selector/frame.
    """
    mode = params.get("mode")
    efficient = mode == "efficient"
    return {
        "interactive": params.get("interactive", efficient),
        "compact": params.get("compact", efficient),
        "depth": params.get("depth", DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH if efficient else None),
        "max_chars": params.get(
            "max_chars",
            DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS if efficient else DEFAULT_AI_SNAPSHOT_MAX_CHARS,
        ),
        "urls": params.get("urls", False),
        "labels": params.get("labels", False),
        "selector": params.get("selector"),
        "frame": params.get("frame"),
    }


def format_ai_snapshot(
    raw: str,
    *,
    depth: int | None = None,
    compact: bool = False,
    urls: bool = False,
    max_chars: int = DEFAULT_AI_SNAPSHOT_MAX_CHARS,
) -> str:
    """Post-process Playwright's mode="ai" snapshot text.

    - drop `/url:` lines unless `urls` (keeps snapshots lean by default)
    - drop unnamed structural container lines when `compact`
    - cap tree depth by indentation when `depth` is set
    - char-cap with a clear truncation marker
    `[box=...]` markers (only present when boxes=True) are left intact — they are
    what `labels=true` is for.
    """
    out: list[str] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not urls and stripped.startswith("/url:"):
            continue
        if compact and _BARE_STRUCTURAL_RE.match(line):
            continue
        if depth is not None:
            m = _INDENT_RE.match(line)
            if m and len(m.group(1)) // 2 > depth:
                continue
        out.append(line)
    text = "\n".join(out)
    if len(text) > max_chars:
        text = text[:max_chars] + (
            "\n\n[...TRUNCATED - page too large; use mode=efficient, a lower depth, "
            "a selector scope, or a higher max_chars]"
        )
    return text
