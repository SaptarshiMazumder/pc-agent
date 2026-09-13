"""What a CHECKPOINT message looks like — the turn that stops to ask before anything is spent.

An agent that follows the studio protocol ends its design turn with one of two fences the
window renders as controls: an ``approve`` block (paid services, one line each) or a ``suggest``
block whose FIRST chip is the "you decide" answer. Either one ending a turn means "the user has
been shown the work and asked"; a turn that ends without one has not asked, whatever its prose
says. The tools that spend money read the stamp this recognition produces
(infrastructure/checkpoint_marker.py), which is what makes the protocol a mechanism rather than
a request.

Pure: text in, verdict out. No agent names, no tool names.
"""

from __future__ import annotations

import re

_APPROVE_FENCE = re.compile(r"(?:```|~~~)[ \t]*approve[ \t]*\n", re.I)
_SUGGEST_FENCE = re.compile(r"(?:```|~~~)[ \t]*suggest[ \t]*\n([\s\S]*?)(?:```|~~~|\Z)", re.I)
_YOU_DECIDE = re.compile(r"^[ \t]*you decide", re.I)


def is_checkpoint_message(text: str) -> bool:
    """Does this assistant message end a turn by ASKING — an approve block, or a suggest block
    whose first chip is "You decide …"?"""
    body = text or ""
    if _APPROVE_FENCE.search(body):
        return True
    for m in _SUGGEST_FENCE.finditer(body):
        lines = [ln for ln in m.group(1).splitlines() if ln.strip()]
        if lines and _YOU_DECIDE.match(lines[0]):
            return True
    return False
