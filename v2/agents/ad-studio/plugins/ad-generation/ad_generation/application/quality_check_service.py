"""A still or a clip -> a verdict: is the product exact, is the person the same, would it sell.
Advice shown to the user beside the result — it never blocks or picks anything.

A clip is judged by its last frame — where drift has had the whole clip to build up. A clip with
no last frame on file cannot be checked here, and says so, rather than passing unseen.
"""

from __future__ import annotations

import json

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.reasoner import Reasoner
from ad_generation.domain.media_verdict import MediaVerdict


class QualityCheckService:
    def __init__(self, reasoner: Reasoner, store: CampaignStore, cast: CastLibrary, instructions: str) -> None:
        self._reasoner = reasoner
        self._store = store
        self._cast = cast
        self._instructions = instructions

    def check_identity(self, campaign_id: str, media: str, cast_name: str) -> MediaVerdict:
        """A sheet judged for ONE thing: is this the cast member. No product, no scene."""
        sheet = self._cast.get(cast_name).sheet
        facts = {
            "judged": "a shoot reference sheet: the cast member in the ad's outfit, six views",
            "shot": {"purpose": "reference sheet", "shows_product": False, "cast": [cast_name]},
            "product": None,
            "image_order": ["image 1: the sheet being judged", f"image 2: character sheet of {cast_name}"],
        }
        answer = self._reasoner.read_images(
            self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False), [media, sheet]
        )
        verdict = MediaVerdict.from_dict(answer, path=media)
        self._store.record_verdict(campaign_id, verdict)
        return verdict

    def check(
        self,
        campaign_id: str,
        media: str,
        last_frame: str,
        purpose: str,
        cast_names: tuple[str, ...],
        shows_product: bool,
        copy: tuple[str, ...] = (),
    ) -> MediaVerdict:
        """ADVICE for the user, never a gate: the score and problems are shown beside the result,
        and the user decides what to keep. `purpose` is what the result was made for (the step's
        title); `cast_names` who should appear in it; `copy` a text ad's words, read back."""
        profile = self._store.profile(campaign_id)
        is_clip = media.lower().endswith((".mp4", ".mov", ".webm"))
        if is_clip and not last_frame:
            raise ValueError(
                f"{media} is a clip and no last frame was saved with it, so it cannot be checked "
                "here — show it to the user instead."
            )
        judged = last_frame if is_clip else media
        sheets = [self._cast.get(n).sheet for n in cast_names]
        images = [judged, *profile.photos, *sheets]
        facts = {
            "judged": "the LAST FRAME of a video clip" if is_clip else ("a designed text ad" if copy else "a still"),
            "shot": {"purpose": purpose, "cast": list(cast_names), "shows_product": shows_product},
            "product": {
                "name": profile.name,
                "description": profile.description,
                "must_keep": profile.must_keep,
                "size": profile.size,
                "label_text": profile.label_text,
            },
            "copy": list(copy),
            "image_order": (
                ["image 1: the one being judged"]
                + [f"image {i}: product photo" for i in range(2, 2 + len(profile.photos))]
                + [f"image {2 + len(profile.photos) + i}: character sheet of {n}" for i, n in enumerate(cast_names)]
            ),
        }
        answer = self._reasoner.read_images(
            self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False), images
        )
        verdict = MediaVerdict.from_dict(answer, path=media)
        self._store.record_verdict(campaign_id, verdict)
        return verdict
