"""A text ad's words set by code, and edited — overlay mode.

typeset    a picture the image model just made (with no text) becomes a design: the picture is
           kept aside as its base, the brief's copy is laid over it in the format's areas, the
           brief's colours and font, and the layers are saved beside it.
preview    an edit drawn for the editor, not saved — exact, because it is drawn the same way.
save       an edit saved: a NEW design in the step (the old one stays), drawn from the same base.
set_words  new words for some layers of a design, everything else kept — what the agent uses.
retext_all the brief's current copy on every overlay design of a step (each lineage's newest),
           keeping where and how each design set it.

Nothing here calls a model: every edit is free and instant.
"""

from __future__ import annotations

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.poster_text_store import PosterTextStore
from ad_generation.application.interfaces.poster_typesetter import PosterTypesetter
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.poster_checklist import PosterChecklist
from ad_generation.domain.poster_copy import ROLES, PosterCopy
from ad_generation.domain.poster_layout import layers_for
from ad_generation.domain.poster_text import PosterText, TextLayer


class PosterTextService:
    def __init__(self, store: CampaignStore, texts: PosterTextStore, typesetter: PosterTypesetter) -> None:
        self._store = store
        self._texts = texts
        self._typesetter = typesetter

    def typeset(self, campaign_id: str, design: GeneratedMedia, scene: str) -> None:
        brief = self._store.brief(campaign_id)
        spec = brief.shot(scene).spec
        missing = PosterChecklist.missing(scene, spec)
        if missing:
            raise ValueError("this brief was written before words could be set in real fonts (" + ", ".join(missing) + ") — rewrite the brief")
        layers = layers_for(
            PosterCopy.from_dict(brief.copy), brief.format_key, spec["text_color"], spec["accent_color"], spec["font"]
        )
        text = PosterText(base=self._texts.keep_base(design.path), layers=layers)
        self._typesetter.render(text.base, text.layers, design.path)
        self._texts.save(design.path, text)

    def get(self, campaign_id: str, design: str) -> PosterText:
        self._own(campaign_id, design)
        text = self._texts.load(design)
        if text is None:
            raise ValueError(
                f"{design} has its text drawn by the image model, so its words cannot be edited here — "
                "change them with still_fix, or make designs with the text set in real fonts (overlay)"
            )
        return text

    def preview(self, campaign_id: str, design: str, layers: list[dict]) -> str:
        text = self.get(campaign_id, design)
        out = self._texts.edit_path(design)
        self._typesetter.render(text.base, tuple(TextLayer.from_dict(l) for l in layers), out)
        return out

    def save(self, campaign_id: str, step_id: str, design: str, layers: list[dict]) -> GeneratedMedia:
        text = PosterText(base=self.get(campaign_id, design).base, layers=tuple(TextLayer.from_dict(l) for l in layers))
        return self._draw(campaign_id, step_id, design, text)

    def set_words(self, campaign_id: str, step_id: str, design: str, words: dict[str, str]) -> GeneratedMedia:
        return self._draw(campaign_id, step_id, design, self.get(campaign_id, design).with_words(words))

    def retext_all(self, campaign_id: str, step_id: str) -> list[GeneratedMedia]:
        copy = PosterCopy.from_dict(self._store.brief(campaign_id).copy)
        newest: dict[str, tuple[float, str, PosterText]] = {}
        for m in self._store.ledger(campaign_id):
            text = self._texts.load(m.path) if m.step == step_id and m.kind == "image" else None
            if text is not None:
                made = self._store.made_at(m.path)
                if text.base not in newest or made >= newest[text.base][0]:
                    newest[text.base] = (made, m.path, text)
        if not newest:
            raise ValueError(f"step {step_id} has no designs with their text set in real fonts")
        words = {role: getattr(copy, role) for role, _ in ROLES}
        out = []
        for _, design, text in newest.values():
            present = {l.role for l in text.layers}
            out.append(self._draw(campaign_id, step_id, design, text.with_words({r: w for r, w in words.items() if r in present})))
        return out

    def _draw(self, campaign_id: str, step_id: str, design: str, text: PosterText) -> GeneratedMedia:
        out = self._store.take_stem(campaign_id, step_id) + "-1.png"
        self._typesetter.render(text.base, text.layers, out)
        self._texts.save(out, text)
        media = GeneratedMedia(
            path=out, kind="image", provider="typeset", model="text layers", cost_usd=0.0, cost_basis="exact",
            detail={"retext_of": design}, step=step_id,
        )
        self._store.record(campaign_id, media)
        return media

    @staticmethod
    def _own(campaign_id: str, design: str) -> None:
        if not design.startswith(f"campaigns/{campaign_id}/"):
            raise ValueError(f"{design} is not a design of campaign {campaign_id}")
