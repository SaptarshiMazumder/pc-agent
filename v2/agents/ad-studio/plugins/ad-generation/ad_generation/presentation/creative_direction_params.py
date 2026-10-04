"""The creative-direction inputs, shared by every tool that writes a brief — one schema, one
parser, used by campaign_start."""

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
        "headline": {"type": "string", "description": "Text ad: the headline, EXACTLY as the user wrote it."},
        "subline": {"type": "string", "description": "Text ad: the line under the headline, exactly as written."},
        "offer": {"type": "string", "description": "Text ad: the offer (\"20% off\", \"Buy 2 get 1\"), exactly as written — ONLY one the user gave."},
        "cta": {"type": "string", "description": "Text ad: the call to action (\"Shop now\"), exactly as written."},
        "fine_print": {"type": "string", "description": "Text ad: small print (dates, terms), exactly as written — only the user's."},
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
            headline=text("headline"),
            subline=text("subline"),
            offer=text("offer"),
            cta=text("cta"),
            fine_print=text("fine_print"),
        )
