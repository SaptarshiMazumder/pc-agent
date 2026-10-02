"""A still or a clip -> a verdict: is the product exact, is the person the same, would it sell.

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

    def check(self, campaign_id: str, shot_id: str, media: str, last_frame: str) -> MediaVerdict:
        profile = self._store.profile(campaign_id)
        shot = self._store.brief(campaign_id).shot(shot_id)
        is_clip = media.lower().endswith((".mp4", ".mov", ".webm"))
        if is_clip and not last_frame:
            raise ValueError(
                f"{media} is a clip and no last frame was saved with it, so it cannot be checked "
                "here — show it to the user instead."
            )
        judged = last_frame if is_clip else media
        sheets = [self._cast.get(n).sheet for n in shot.cast]
        images = [judged, *profile.photos, *sheets]
        facts = {
            "judged": "the LAST FRAME of a video clip" if is_clip else "a still",
            "shot": shot.to_dict(),
            "product": {"name": profile.name, "description": profile.description, "must_keep": profile.must_keep},
            "image_order": (
                ["image 1: the one being judged"]
                + [f"image {i}: product photo" for i in range(2, 2 + len(profile.photos))]
                + [f"image {2 + len(profile.photos) + i}: character sheet of {n}" for i, n in enumerate(shot.cast)]
            ),
        }
        answer = self._reasoner.read_images(
            self._instructions + "\n\nFACTS:\n" + json.dumps(facts, ensure_ascii=False), images
        )
        verdict = MediaVerdict.from_dict(answer, path=media)
        self._store.record_verdict(campaign_id, verdict)
        return verdict
