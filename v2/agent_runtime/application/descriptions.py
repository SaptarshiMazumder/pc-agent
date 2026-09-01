"""Uniform description resolution for capabilities — tools, plugins, skills, agents.

ONE rule for all four: a capability self-describes from its OWN artifact, with a deterministic
fallback chain, so a description is never empty and never hand-duplicated in a separate file.
The artifact differs by kind (a tool's code attribute, a skill's frontmatter, a plugin's
`plugin.toml`/module docstring, an agent's `agent.toml`/`IDENTITY.md`) but the CONTRACT is the
same. Only the pure text helper lives here; the IO-touching resolvers live next to each loader
(they read the artifact), and every one of them ends its fallback chain in `first_meaningful_line`.
"""

from __future__ import annotations


def first_meaningful_line(text: str, limit: int = 240, skip_headings: bool = False) -> str:
    """The first non-blank line of `text`, cleaned for use as a one-line description.

    ``skip_headings=False`` (skills): a leading markdown heading has its ``#`` stripped and its
    text kept — a skill's H1 IS descriptive. ``skip_headings=True`` (agents): heading lines are
    skipped entirely and bullet/quote markers are trimmed off the first prose line — an agent's
    ``IDENTITY.md`` opens with its NAME as an H1, so we want the sentence beneath it, not the title.
    """
    for raw in (text or "").splitlines():
        s = raw.strip()
        if not s:
            continue
        if s.startswith("#"):
            if skip_headings:
                continue
            s = s.lstrip("#").strip()
        elif skip_headings:
            s = s.lstrip("-*>").strip()
        if s:
            return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"
    return ""
