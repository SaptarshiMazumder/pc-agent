"""Stories: suggested from the occasions coming up and the products picked, drafted with the agent's
model from a theme (or the user's own words), revised when the user changes their mind, edited
directly from the window, and approved before anything is made.

The model writes the story's words — the hook, what each shot shows and why, the caption angle.
Counts, shapes and lengths are the user's: the model only SUGGESTS them (`suggested_*`), and fills
a shot's own count/shape/length only with what the user said. With the story's Auto on, approval
fills the rest from the suggestions.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, timedelta
from typing import Callable

from ad_generation.application.interfaces.brand_store import BrandStore
from ad_generation.application.interfaces.occasion_library import OccasionLibrary
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.application.interfaces.shop_product_store import ShopProductStore
from ad_generation.application.interfaces.story_store import StoryStore
from ad_generation.application.interfaces.story_theme_library import StoryThemeLibrary
from ad_generation.domain.occasion import Occasion
from ad_generation.domain.story import Story
from ad_generation.domain.story_shot import StoryShot

# What the window and the agent may change directly (free, no model).
_EDITABLE = (
    "title", "occasion", "products", "audience", "goal", "offer", "tone", "cast", "language", "hook", "format",
    "caption_angle", "cta", "notes", "auto_pick", "collection",
)
_SHOT_FIELDS = ("shows", "purpose", "kind", "product", "text", "count", "aspect", "seconds", "start_from")
_HORIZON_DAYS = 75  # occasions this close are suggested


class StoryService:
    def __init__(
        self,
        reasoner: Reasoner | None,
        stories: StoryStore,
        themes: StoryThemeLibrary,
        occasions: OccasionLibrary,
        products: ShopProductStore,
        brand: BrandStore,
        instructions: str,
        clock: Callable[[], float],
        today: Callable[[], date],
    ) -> None:
        self._reasoner = reasoner
        self._stories = stories
        self._themes = themes
        self._occasions = occasions
        self._products = products
        self._brand = brand
        self._instructions = instructions
        self._clock = clock
        self._today = today

    # ---- what to tell -----------------------------------------------------------------------

    def suggest(self, product_keys: list[str]) -> dict:
        """The occasions coming up (in their posting window first), and the themes that suit them
        and the products' kinds, best first. No model call."""
        today = self._today()
        kinds = {self._products.get(k).kind for k in product_keys} - {"other"}
        upcoming = sorted(
            (o for o in self._occasions.all() if (n := o.next_date(today)) and n <= today + timedelta(days=_HORIZON_DAYS)),
            key=lambda o: (not o.is_on(today), o.next_date(today)),
        )
        ranked: list[tuple[tuple, str, str]] = []
        for theme in self._themes.all():
            if kinds and not kinds & set(theme.kinds):
                continue
            hits = [o for o in upcoming if theme.key in o.themes or o.key in theme.occasions]
            if hits:
                # The nearest occasion it suits; among equals, where the occasion ranks this theme.
                best = min(hits, key=lambda o: (not o.is_on(today), o.next_date(today), _rank(o, theme.key)))
                order = (0 if best.is_on(today) else 1, best.next_date(today).toordinal(), _rank(best, theme.key))
                reason = f"for {best.name} ({best.next_date(today).isoformat()})"
            else:
                order, reason = (2, 0, 0), "any time"
            ranked.append((order, theme.key, reason))
        ranked.sort(key=lambda r: r[0])
        return {
            "today": today.isoformat(),
            "occasions": [{**o.to_dict(today), "posting_now": o.is_on(today)} for o in upcoming],
            "themes": [{**self._themes.get(k).to_dict(), "why_now": why} for _, k, why in ranked],
        }

    # ---- drafting (the agent's model) -------------------------------------------------------

    def draft(
        self, theme_key: str, words: str, product_keys: list[str], occasion: str, answers: dict, auto_pick: bool,
        session: str = "",
    ) -> tuple[Story, list[str]]:
        """A new story from a theme and/or the user's own words. -> (the draft, questions the
        model needs answered — ask them; the draft stands until then)."""
        if not theme_key and not words.strip():
            raise ValueError("give a theme or the user's own words for the story")
        facts = {
            "theme": self._themes.get(theme_key).to_dict() if theme_key else None,
            "users_words": words, "answers": answers, "occasion": self._occasion_facts(occasion),
            **self._common_facts(product_keys),
        }
        answer = self._think(facts)
        now = self._clock()
        story = self._story_from(answer, Story(
            id=self._stories.new_id(str(answer.get("title") or theme_key or "story")), title="", theme=theme_key,
            products=tuple(product_keys), auto_pick=auto_pick, created=now, updated=now, session=session,
            sources=self._themes.get(theme_key).sources if theme_key else (),
        ))
        self._stories.save(story)
        return story, [str(q) for q in answer.get("questions") or []]

    def revise(self, story_id: str, change: str, answers: dict) -> tuple[Story, list[str]]:
        """The story rewritten with the user's change (or their answers) — shots keep their ids."""
        current = self._stories.get(story_id)
        facts = {
            "current_story": current.to_dict(), "users_change": change, "answers": answers,
            "theme": self._themes.get(current.theme).to_dict() if current.theme else None,
            "occasion": self._occasion_facts(current.occasion), **self._common_facts(list(current.products)),
        }
        answer = self._think(facts)
        story = self._story_from(answer, current, revising=True)
        story = replace(story, version=current.version + 1, status="draft", updated=self._clock())
        self._stories.save(story)
        return story, [str(q) for q in answer.get("questions") or []]

    # ---- direct edits (free) ----------------------------------------------------------------

    def update(self, story_id: str, changes: dict) -> Story:
        """Change the story directly: its fields; one shot (`shot`: {id, …fields}); a new shot
        (`add_shot`); `remove_shot` (an id); `order` (the shot ids in their new order)."""
        story = self._stories.get(story_id)
        unknown = [k for k in changes if k not in (*_EDITABLE, "shot", "add_shot", "remove_shot", "order")]
        if unknown:
            raise ValueError(f"a story has no {', '.join(unknown)} to change (fields: {', '.join(_EDITABLE)}; shots: shot, add_shot, remove_shot, order)")
        fields = {k: (tuple(v) if k == "products" else bool(v) if k == "auto_pick" else str(v)) for k, v in changes.items() if k in _EDITABLE}
        if "products" in fields:
            for key in fields["products"]:
                self._products.get(key)
        shots = list(story.shots)
        if changes.get("shot"):
            row = dict(changes["shot"])
            target = story.shot(str(row.pop("id", "")))
            bad = [k for k in row if k not in _SHOT_FIELDS]
            if bad:
                raise ValueError(f"a shot has no {', '.join(bad)} (fields: {', '.join(_SHOT_FIELDS)})")
            shots = [target.with_(**_shot_values(row)) if s.id == target.id else s for s in shots]
        if changes.get("add_shot"):
            row = dict(changes["add_shot"])
            shots.append(StoryShot.from_dict({**row, "id": _next_id(shots)}))
        if changes.get("remove_shot"):
            story.shot(str(changes["remove_shot"]))
            shots = [s for s in shots if s.id != str(changes["remove_shot"])]
        if changes.get("order"):
            order = [str(i) for i in changes["order"]]
            if sorted(order) != sorted(s.id for s in shots):
                raise ValueError(f"order must name every shot exactly once: {', '.join(s.id for s in shots)}")
            shots = [next(s for s in shots if s.id == i) for i in order]
        if set(changes) <= {"auto_pick", "collection"}:  # neither changes anything made
            updated = replace(story, **fields, updated=self._clock())
        else:
            updated = story.changed(self._clock(), **fields, shots=tuple(shots))
        self._check_products(updated)
        self._stories.save(updated)
        return updated

    def approve(self, story_id: str) -> Story:
        """The user's go: the shot list is final for this version. With Auto on, unchosen counts,
        shapes and lengths take their suggestions; with Auto off, every one must be chosen."""
        story = self._stories.get(story_id)
        if story.auto_pick:
            story = replace(story, shots=tuple(s.with_suggestions() for s in story.shots))
        missing = story.unmade()
        if missing:
            raise ValueError(
                "choose these first (or switch the story's Auto on): "
                + "; ".join(f"{sid}: {', '.join(m)}" for sid, m in missing.items())
            )
        approved = replace(story, status="approved", updated=self._clock())
        self._stories.save(approved)
        return approved

    def get(self, story_id: str) -> Story:
        return self._stories.get(story_id)

    def all(self) -> list[Story]:
        return self._stories.all()

    # ---- pieces -----------------------------------------------------------------------------

    def _think(self, facts: dict) -> dict:
        if self._reasoner is None:
            raise ValueError("drafting a story needs the agent's model")
        return self._reasoner.think(self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False))

    def _common_facts(self, product_keys: list[str]) -> dict:
        brand = self._brand.get()
        return {
            "today": self._today().isoformat(),
            "products": [
                {"key": p.key, "title": p.title, "kind": p.kind, "description": p.description, "price": p.price,
                 "photos": len(p.all_photos())}
                for p in (self._products.get(k) for k in product_keys)
            ],
            "brand": {"name": brand.name, "voice": brand.voice, "tagline": brand.tagline},
        }

    def _occasion_facts(self, occasion: str) -> dict | None:
        if not occasion:
            return None
        found: Occasion | None = next((o for o in self._occasions.all() if o.key == occasion), None)
        return found.to_dict(self._today()) if found else {"name": occasion}

    def _story_from(self, answer: dict, base: Story, revising: bool = False) -> Story:
        rows = answer.get("shots") or []
        if not rows:
            raise ValueError("the story came back with no shots")
        kept = {s.id: s for s in base.shots} if revising else {}
        shots: list[StoryShot] = []
        for row in rows:
            sid = str(row.get("id") or "") if revising and str(row.get("id") or "") in kept else _next_id(shots + list(kept.values()))
            prior = kept.get(sid)
            shots.append(StoryShot.from_dict({
                "id": sid, "shows": row.get("shows"), "purpose": row.get("purpose"), "kind": row.get("kind", "image"),
                "product": row.get("product", ""), "text": row.get("text", ""),
                # The user's choices: kept from before, or what they said now — never the model's guess.
                "count": row.get("count") or (prior.count if prior else 0),
                "aspect": row.get("aspect") or (prior.aspect if prior else ""),
                "seconds": row.get("seconds") or (prior.seconds if prior else 0),
                "start_from": row.get("start_from") or (prior.start_from if prior else ""),
                "suggested_count": row.get("suggested_count", 0), "suggested_aspect": row.get("suggested_aspect", ""),
                "suggested_seconds": row.get("suggested_seconds", 0),
            }))
        story = replace(
            base,
            title=str(answer.get("title") or base.title), occasion=str(answer.get("occasion") or base.occasion),
            audience=str(answer.get("audience") or ""), goal=str(answer.get("goal") or ""), offer=str(answer.get("offer") or ""),
            tone=str(answer.get("tone") or ""), language=str(answer.get("language") or ""), hook=str(answer.get("hook") or ""),
            format=str(answer.get("format") or base.format), caption_angle=str(answer.get("caption_angle") or ""),
            cta=str(answer.get("cta") or ""), notes=str(answer.get("notes") or base.notes), shots=tuple(shots),
        )
        self._check_products(story)
        return story

    def _check_products(self, story: Story) -> None:
        allowed = {"", "all", *story.products}
        wrong = [f"{s.id}: '{s.product}'" for s in story.shots if s.product not in allowed]
        if wrong:
            raise ValueError(f"shots name products the story does not have ({', '.join(wrong)}); its products: {', '.join(story.products) or 'none'}")


def _rank(occasion: Occasion, theme_key: str) -> int:
    """Where the occasion lists the theme (its best first); unlisted themes after the listed ones."""
    return occasion.themes.index(theme_key) if theme_key in occasion.themes else len(occasion.themes)


def _next_id(shots: list[StoryShot]) -> str:
    n = 1
    taken = {s.id for s in shots}
    while f"s{n}" in taken:
        n += 1
    return f"s{n}"


def _shot_values(row: dict) -> dict:
    return {k: (int(v or 0) if k in ("count", "seconds") else str(v)) for k, v in row.items()}
