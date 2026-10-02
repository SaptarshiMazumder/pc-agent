"""What every shot's prompts MUST state — checked before a prompt is composed, so no still or
clip is ever generated with an item of the checklist left out.

The LOOK is decided once per ad (one outfit, one place, one light) and shared by every shot;
the SHOT fields change shot to shot. `wardrobe` is required only when someone is cast.
"""

from __future__ import annotations

LOOK = ("location", "time_of_day", "weather", "mood")
LOOK_WITH_CAST = ("wardrobe",) + LOOK
SHOT = ("pose", "background", "framing", "lighting", "action", "camera_move")


class ShotChecklist:
    @staticmethod
    def missing_look(look: dict, has_cast: bool) -> list[str]:
        required = LOOK_WITH_CAST if has_cast else LOOK
        return [f"look.{k}" for k in required if not str(look.get(k) or "").strip()]

    @staticmethod
    def missing_shot(shot_id: str, spec: dict) -> list[str]:
        return [f"{shot_id}.{k}" for k in SHOT if not str(spec.get(k) or "").strip()]
