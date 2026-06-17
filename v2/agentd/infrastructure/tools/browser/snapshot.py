"""Snapshot planning + ARIA role sets (shared by the Playwright provider and the
browser tool). Moved verbatim from the old browser.py."""

from __future__ import annotations

import re

# constants.ts
DEFAULT_AI_SNAPSHOT_MAX_CHARS = 40_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS = 8_000
DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH = 6

# snapshot-roles.ts (verbatim)
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

# aria_snapshot lines: `  - button "Submit"` / `- link "Home":` / `- heading "Hi" [level=1]`
_NODE_RE = re.compile(r"^(\s*)-\s+([a-z]+)(?:\s+\"((?:[^\"\\]|\\.)*)\")?(.*)$")
_NETWORKIDLE_TIMEOUT_MS = 8_000


def resolve_snapshot_plan(params: dict) -> dict:
    mode = params.get("mode")
    if mode == "efficient":
        return {
            "interactive": params.get("interactive", True),
            "compact": params.get("compact", True),
            "depth": params.get("depth", DEFAULT_AI_SNAPSHOT_EFFICIENT_DEPTH),
            "max_chars": params.get("max_chars", DEFAULT_AI_SNAPSHOT_EFFICIENT_MAX_CHARS),
        }
    return {
        "interactive": params.get("interactive", False),
        "compact": params.get("compact", False),
        "depth": params.get("depth"),
        "max_chars": params.get("max_chars", DEFAULT_AI_SNAPSHOT_MAX_CHARS),
    }
