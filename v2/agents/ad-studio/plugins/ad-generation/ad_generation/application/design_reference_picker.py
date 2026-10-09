"""Chooses which design reference each slide of a post follows — one family across the post, so a
carousel reads as one designed set — from the references offered (the ones named, or the whole
library). One text call to the agent's model; the references' readings are what it chooses by."""

from __future__ import annotations

import json

from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.domain.design_reference import DesignReference
from ad_generation.domain.post import Post


class DesignReferencePicker:
    def __init__(self, reasoner: Reasoner, prompt: str) -> None:
        self._reasoner = reasoner
        self._prompt = prompt

    def pick(self, post: Post, numbers: list[int], offered: list[DesignReference], brief: str = "") -> dict[int, DesignReference]:
        """Slide number -> the reference it follows. One offered -> every slide follows it; none
        offered -> no references. `brief`: the user's wishes, in their words — references that go
        against it are not chosen."""
        if len(offered) <= 1:
            return {n: offered[0] for n in numbers} if offered else {}
        facts = {
            "brief": brief or None,
            "format": post.format,
            "slides": [
                {"slide": n, "purpose": post.slides[n - 1].note, "kind": post.slides[n - 1].kind,
                 "words": [c.text for c in post.slides[n - 1].cues]}
                for n in numbers
            ],
            "references": [{"slug": r.slug, "name": r.name, "suits": list(r.suits), "spec": r.spec} for r in offered],
        }
        answer = self._reasoner.think(self._prompt + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False))
        by_slug = {r.slug: r for r in offered}
        chosen: dict[int, DesignReference] = {}
        for row in answer.get("slides") or []:
            n, slug = int(row.get("slide") or 0), str(row.get("reference") or "")
            if n in numbers and slug in by_slug:
                chosen[n] = by_slug[slug]
        missing = [n for n in numbers if n not in chosen]
        if missing:
            raise ValueError(f"the reference choice left out slides {missing} (answer: {json.dumps(answer)[:300]})")
        return chosen
