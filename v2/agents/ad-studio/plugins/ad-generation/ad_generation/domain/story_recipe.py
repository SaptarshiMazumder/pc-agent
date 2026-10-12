"""A story's shots made with a template: the template's own preparation stages (a shoot sheet, a
product sheet) and then ONE stage per shot of the story — its scene written from what the shot
shows and why it is in the story, with the shot's own count, shape and length. A video shot gets
its first frame made in a stage of its own, then the clip from it.

The result is an ordinary Recipe, so the brief, the stages, the checks and the window work on a
story campaign exactly as on any other. Many stories share the same templates; this is the join.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.recipe import Recipe
from ad_generation.domain.shot_template import ShotTemplate
from ad_generation.domain.story import Story
from ad_generation.domain.story_shot import StoryShot
from ad_generation.domain.template_setting import resolve

PREFIX = "story:"
_PREP = ("sheet", "product_sheet")


class StoryRecipe:
    @staticmethod
    def key(campaign_id: str) -> str:
        return f"{PREFIX}{campaign_id}"

    @staticmethod
    def campaign_of(key: str) -> str:
        """The campaign a story recipe key belongs to; "" for an ordinary template key."""
        return key[len(PREFIX):] if key.startswith(PREFIX) else ""

    @staticmethod
    def build(template: Recipe, story: Story, shot_ids: tuple[str, ...], others: tuple[str, ...], key: str) -> Recipe:
        """`others`: the main photos of the story's OTHER products, sent with a shot that shows
        them all ("all")."""
        if not shot_ids:
            raise ValueError(f"story {story.id}: no shots for this campaign")
        shots = [story.shot(i) for i in shot_ids]
        person = template.needs_cast()
        images_model = next((s.model for s in template.steps if s.action == "images"), "")
        video_model = next((s.model for s in template.steps if s.action == "video"), "")
        brief = next((s for s in template.steps if s.action == "brief"), None)
        steps: list[CampaignStep] = [brief] if brief else []
        steps += [s for s in template.steps if s.action in _PREP]
        scenes: list[ShotTemplate] = []
        for n, shot in enumerate(shots, 1):
            scenes.append(ShotTemplate(
                id=shot.id, purpose=shot.purpose, direction=_direction(story, shot), cast=person,
                shows_product=bool(shot.product), duration_s=shot.seconds or 5,
            ))
            title = f"{n}. {shot.purpose}"
            extra = others if shot.product == "all" else ()
            frame_id = shot.id if shot.kind == "image" else f"{shot.id}-frame"
            steps.append(CampaignStep(
                id=frame_id, title=title if shot.kind == "image" else f"{title} — first frame", action="images",
                scene=shot.id, cast=person, shows_product=bool(shot.product), count=shot.count, aspect=shot.aspect,
                model=images_model, extra_references=extra,
            ))
            if shot.kind == "video":
                steps.append(CampaignStep(
                    id=shot.id, title=title, action="video", scene=shot.id, source=frame_id, cast=person,
                    shows_product=bool(shot.product), seconds=shot.seconds, aspect=shot.aspect, model=video_model,
                ))
        # The story decides the scenes: the brief asks only who is in it (when someone is).
        settings = (("brief", (resolve("cast"),) if person else ()),)
        return replace(
            template, key=key, shots=tuple(scenes), steps=tuple(steps), settings=settings,
            aspect_ratio=next((s.aspect for s in shots if s.aspect), template.aspect_ratio),
        )


def _direction(story: Story, shot: StoryShot) -> str:
    parts = [shot.shows.rstrip(".") + "."]
    parts.append(f'In the story "{story.title}" this picture is: {shot.purpose}.')
    if story.tone:
        parts.append(f"The mood: {story.tone}.")
    if shot.text:
        parts.append(f'The words "{shot.text}" are set on it later — keep a calm, uncluttered area for them; never draw text.')
    return " ".join(parts)
