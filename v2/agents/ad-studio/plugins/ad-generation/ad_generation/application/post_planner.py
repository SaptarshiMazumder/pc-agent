"""The post's plan, written by the agent's model from the collection and the playbook: the slides in
order, each one's words and timing, how each clip is cut, the caption. Checked before it is kept —
an item not in the collection, or a cue that cannot be drawn, goes back to the writer once with
the reason, then fails loudly."""

from __future__ import annotations

import json

from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.domain.collection import Collection
from ad_generation.domain.post import Slide


class PostPlanner:
    def __init__(self, reasoner: Reasoner, instructions: str, playbook: str) -> None:
        self._reasoner = reasoner
        self._prompt = instructions.strip() + "\n\n" + playbook.strip()

    def plan(self, facts: dict, collection: Collection, images: list[str], tagline: str = "") -> tuple[dict, list[Slide]]:
        """`images`: one picture per collection item, in order (a clip's middle frame) — the planner
        SEES what it writes about."""
        prompt = self._prompt + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False)
        answer = self._ask(prompt, images)
        try:
            return answer, self._slides(answer, collection, tagline)
        except (ValueError, KeyError, TypeError) as e:
            answer = self._ask(prompt + f"\n\nYOUR LAST PLAN WAS REFUSED: {e}. Answer again, complete, fixing that.", images)
            return answer, self._slides(answer, collection, tagline)

    def _ask(self, prompt: str, images: list[str]) -> dict:
        return self._reasoner.read_images(prompt, images) if images else self._reasoner.think(prompt)

    @staticmethod
    def _slides(answer: dict, collection: Collection, tagline: str = "") -> list[Slide]:
        kinds = {i.path: i.kind for i in collection.items}
        slides = []
        for raw in answer.get("slides") or []:
            item = str(raw.get("item") or "")
            if item not in kinds:
                raise ValueError(f"slide item '{item}' is not in the collection")
            slides.append(Slide.from_dict({**raw, "kind": kinds[item]}))
        if not slides:
            raise ValueError("the plan has no slides")
        PostPlanner._check_sign_off(slides[-1], tagline)
        return slides

    @staticmethod
    def _check_sign_off(last: Slide, tagline: str) -> None:
        """The last slide signs off with the brand's tagline, and its words STAY: the line before
        it moves up to make room, it never fades away."""
        if not tagline.strip():
            return
        if not any(c.text.strip().rstrip(".") == tagline.strip().rstrip(".") for c in last.cues):
            raise ValueError(f"the last slide must sign off with the brand's tagline, exactly: '{tagline}'")
        gone = [c.text for c in last.cues if c.end]
        if gone:
            raise ValueError(f"on the last slide every line stays to the end (end 0) — these fade away: {gone}")
