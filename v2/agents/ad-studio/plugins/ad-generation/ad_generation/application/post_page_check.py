"""Is a designed page the page the plan asked for? — checked before a design made elsewhere (a Canva
template the agent filled in the browser) becomes a post's files.

Each page is compared with its slide: the vision model looks at the page and at the slide's photo
(a clip: its middle frame) and reads the plan's words back. A page that shows another picture, or
lacks the words, is refused with what it shows — so a design that was never filled in (a
template's own photos, an unrelated design) cannot pass as the post.
"""

from __future__ import annotations

import json

from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.domain.page_verdict import PageVerdict
from ad_generation.domain.post import Slide


class PostPageCheck:
    def __init__(self, reasoner: Reasoner, instructions: str) -> None:
        self._reasoner = reasoner
        self._instructions = instructions

    def check(self, page: int, page_image: str, slide: Slide, slide_image: str) -> PageVerdict:
        words = [" ".join(c.text.split()) for c in slide.cues]
        facts = {"page": page, "words": words}
        answer = self._reasoner.read_images(
            self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False), [page_image, slide_image]
        )
        same = bool(answer.get("same_picture"))
        missing = tuple(str(w) for w in answer.get("missing_words") or [] if str(w).strip())
        return PageVerdict(
            page=page, matches=same and not missing, same_picture=same, missing_words=missing,
            notes=str(answer.get("notes") or "").strip(),
        )
