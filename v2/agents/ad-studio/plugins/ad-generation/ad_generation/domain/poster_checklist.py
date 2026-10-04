"""What every text ad's design MUST state — checked before its prompt is composed, as the shot
checklist is for a photo ad."""

from __future__ import annotations

from ad_generation.domain.poster_text import DISPLAY_FONTS, HEX

DESIGN = (
    "layout", "background", "product_placement", "palette", "typography", "mood", "text_color", "accent_color", "font",
)


class PosterChecklist:
    @staticmethod
    def missing(shot_id: str, spec: dict) -> list[str]:
        missing = [f"{shot_id}.{k}" for k in DESIGN if not str(spec.get(k) or "").strip()]
        for k in ("text_color", "accent_color"):
            if spec.get(k) and not HEX.match(str(spec[k])):
                missing.append(f"{shot_id}.{k} (a #rrggbb colour)")
        if spec.get("font") and spec["font"] not in DISPLAY_FONTS:
            missing.append(f"{shot_id}.font (one of {', '.join(DISPLAY_FONTS)})")
        return missing
