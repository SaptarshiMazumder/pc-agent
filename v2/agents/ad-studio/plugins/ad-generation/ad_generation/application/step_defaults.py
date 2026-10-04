"""What a step runs with when the user does not say otherwise — its prompt, its references, its
clip length — and what each reference IS, for the prompt's legend.

The same answers feed the generators AND the window, which shows them pre-filled in the step's
Generate panel: what the user sees there is exactly what a run without changes would send.
"""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.face_crops import FaceCrops
from ad_generation.application.interfaces.sheet_panels import SheetPanels
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.cast_looks import looks_from_description
from ad_generation.domain.poster_copy import PosterCopy
from ad_generation.domain.product_sheet_layout import VIEWS


class StepDefaults:
    def __init__(
        self, store: CampaignStore, cast: CastLibrary, faces: FaceCrops, panels: SheetPanels, max_product_photos: int
    ) -> None:
        self._store = store
        self._cast = cast
        self._faces = faces
        self._panels = panels
        self._max_product_photos = max_product_photos

    def prompt(self, campaign_id: str, step: CampaignStep) -> str:
        """Its own prompt, else its brief scene's (the still's prompt for images, the motion
        prompt for video); "" when it has neither."""
        if step.prompt:
            return step.prompt
        if not step.scene:
            return ""
        shot = self._store.brief(campaign_id).shot(step.scene)
        if step.action == "video":
            return shot.motion_prompt
        if step.text == "overlay":
            if not shot.overlay_prompt:
                raise ValueError(f"step {step.id} sets its words in real fonts, and this brief has no text-ad design — rewrite the brief")
            return shot.overlay_prompt
        return shot.keyframe_prompt

    def copy_lines(self, campaign_id: str, step: CampaignStep) -> tuple[str, ...]:
        """A step that makes a text ad (it uses the scene of a text ad's brief): its words, exactly.
        () for anything else — a product still added to a text-ad campaign is a photo."""
        if not step.scene:
            return ()
        return PosterCopy.from_dict(self._store.brief(campaign_id).copy).lines()

    def references(self, campaign_id: str, checklist: CampaignChecklist, step: CampaignStep) -> tuple[str, ...]:
        """Its own references, else: the shoot sheet and the cast member's FACE (the close-up cut
        from their sheet — a whole sheet's grid of panels is what image models copy into collages)
        when a person appears, the product photos when the product does (images; a clip's first
        frame already shows the product)."""
        if step.references:
            return step.references
        refs: list[str] = []
        if step.cast:
            sheet = checklist.first("sheet")
            if sheet and sheet.pick:
                refs.append(sheet.pick)
            if checklist.cast_name:
                refs.append(self.face(checklist.cast_name))
        if step.shows_product:
            panels = self.panels(checklist)
            if step.action == "images":
                # With a product sheet, the two best photos and its six views; else up to the max.
                photos = self._store.profile(campaign_id).photos
                refs += list(photos[: 2 if panels else self._max_product_photos])
            refs += panels  # a clip gets the views too: they hold the product as it turns
        return tuple(refs)

    def panels(self, checklist: CampaignChecklist) -> list[str]:
        """The picked product sheet's panels, one view each — none without a picked sheet."""
        sheet = checklist.first("product_sheet")
        return self._panels.panels(sheet.pick) if sheet and sheet.pick else []

    def face(self, cast_name: str) -> str:
        """The cast member's face close-up, cut from their character sheet."""
        return self._faces.face(self._cast.get(cast_name).sheet)

    def identity(self, checklist: CampaignChecklist, step: CampaignStep) -> str:
        """Who appears, in words — their hair, skin and features from the cast description, so a
        hair colour or face shape is said as well as shown."""
        if not (step.cast and checklist.cast_name):
            return ""
        member = self._cast.get(checklist.cast_name)
        looks = looks_from_description(member.description)
        return f"{member.name}'s features (her hairstyle follows the scene): {looks}" if looks else ""

    def seconds(self, campaign_id: str, step: CampaignStep) -> int:
        if step.seconds:
            return step.seconds
        if step.scene:
            return self._store.brief(campaign_id).shot(step.scene).duration_s
        return 5

    def legend(self, campaign_id: str, checklist: CampaignChecklist, references: list[str], like: str = "") -> list[str]:
        """'image n: what it is' for each reference, so the prompt can say how to use it."""
        profile = self._store.profile(campaign_id)
        views = dict(zip(self.panels(checklist), VIEWS))
        sheet = checklist.first("sheet")
        cast_refs = (
            {self._cast.get(checklist.cast_name).sheet, self.face(checklist.cast_name)} if checklist.cast_name else set()
        )
        lines = []
        for n, ref in enumerate(references, 1):
            if like and ref == like:
                what = "the image the user liked — make a fresh variation of it: the same scene, framing and light"
            elif sheet and ref == sheet.pick:
                what = f"{checklist.cast_name} as she appears in this ad — match this outfit, hair styling and light exactly"
            elif ref in cast_refs:
                what = (
                    f"identity reference for {checklist.cast_name} — use ONLY this person's face, hair colour "
                    "and skin tone; ignore the clothes, jewellery, background and framing shown in it"
                )
            elif ref in views:
                what = f"the product, {profile.name}, seen from the {views[ref]} — from its reference sheet"
                if views[ref].startswith("close-up"):
                    what = f"the product, {profile.name}: a {views[ref]} — from its reference sheet"
            elif ref in profile.photos:
                what = f"the product, {profile.name}"
            else:
                what = "a reference the user chose"
            lines.append(f"image {n}: {what}")
        return lines
