"""The creative-direction inputs, shared by every tool that writes a brief — one schema, one
parser, so `campaign_run` and `campaign_brief` cannot disagree on a field."""

from __future__ import annotations

from ad_generation.domain.creative_direction import CreativeDirection

_REQUIREMENT = "The user's requirement, in their words; leave out to let the brief choose."


class CreativeDirectionParams:
    SCHEMA = {
        "wardrobe": {"type": "string", "description": "What the model wears. " + _REQUIREMENT},
        "location": {"type": "string", "description": "Where the ad happens. " + _REQUIREMENT},
        "time_of_day": {"type": "string", "description": "e.g. golden hour, night. " + _REQUIREMENT},
        "weather": {"type": "string", "description": "e.g. light rain, clear and warm. " + _REQUIREMENT},
        "pose": {"type": "string", "description": "How the model poses or moves. " + _REQUIREMENT},
        "mood": {"type": "string", "description": "e.g. playful, moody, luxurious. " + _REQUIREMENT},
        "background": {"type": "string", "description": "What is behind the model. " + _REQUIREMENT},
        "direction": {"type": "string", "description": "Anything else the user said about the ad, in their words."},
    }

    @staticmethod
    def parse(params: dict) -> CreativeDirection:
        text = lambda k: str(params.get(k) or "").strip()  # noqa: E731
        return CreativeDirection(
            wardrobe=text("wardrobe"),
            location=text("location"),
            time_of_day=text("time_of_day"),
            weather=text("weather"),
            pose=text("pose"),
            mood=text("mood"),
            background=text("background"),
            notes=text("direction"),
        )
