"""An images step -> its images: a prompt and references, any number of times.

The prompt and references are the step's defaults (its brief scene, the cast and product
references) unless the run gives its own — the user types a prompt and picks references in the
window, and those are exactly what is sent. The product's must-keep details are added whenever
the step shows the product, and the photo style — a real photograph in natural light, with
unretouched skin when a person is in it — is added to EVERY image, whoever wrote its prompt.
"""

from __future__ import annotations

from dataclasses import replace

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.application.step_defaults import StepDefaults
from ad_generation.domain.campaign_checklist import CampaignChecklist
from ad_generation.domain.campaign_step import CampaignStep
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest
from ad_generation.domain.step_run import StepRun


class KeyframeService:
    def __init__(
        self,
        generators: GeneratorCatalog,
        store: CampaignStore,
        defaults: StepDefaults,
        style_person: str,
        style_product: str,
    ) -> None:
        self._generators = generators
        self._store = store
        self._defaults = defaults
        self._style_person = style_person.strip()
        self._style_product = style_product.strip()

    def generate(
        self,
        campaign_id: str,
        checklist: CampaignChecklist,
        step: CampaignStep,
        run: StepRun,
        count: int,
        provider: str,
        model: str,
        max_usd: float = 0.0,
    ) -> list[GeneratedMedia]:
        prompt = run.prompt or self._defaults.prompt(campaign_id, step)
        if not prompt:
            raise ValueError(f"step {step.id} has no prompt of its own and no brief scene — give one")
        references = list(
            run.references if run.references is not None else self._defaults.references(campaign_id, checklist, step)
        )
        if run.like and run.like not in references:
            references.append(run.like)
        identity = self._defaults.identity(checklist, step)
        if identity:
            prompt += "\n\n" + identity
        legend = self._defaults.legend(campaign_id, checklist, references, run.like)
        if legend:
            prompt += "\n\nReference images, in order:\n" + "\n".join(legend)
        profile = self._store.profile(campaign_id)
        if step.shows_product and profile.must_keep:
            prompt += (
                f"\n\nReproduce the product exactly as in its photos: {profile.description}. "
                "These details must be exact: " + "; ".join(profile.must_keep) + "."
            )
        if step.shows_product and profile.scale_and_label():
            prompt += "\n\n" + profile.scale_and_label()
        if run.change:
            prompt += "\n\nChange from the last images: " + run.change
        copy = self._defaults.copy_lines(campaign_id, step)
        if copy and step.text == "overlay":
            # The words are set on it afterwards, in real fonts: the picture carries none.
            prompt += "\n\nThe result is one finished poster picture filling the whole frame, with no text anywhere."
        elif copy:
            # A TEXT AD is a design, not a photograph: no photo style; the words stay exact.
            prompt += "\n\nThe result is one finished poster design filling the whole frame, with exactly the text above."
        else:
            style = self._style_person if step.cast else self._style_product
            if style not in prompt:  # a prompt composed before the style moved here already ends with it
                prompt += "\n\n" + style
            # Each request makes ONE picture; said plainly, and nothing about how many or about
            # layouts (naming "triptych" or "3 images" is what draws one).
            prompt += "\n\nThe result is a single photograph filling the whole frame."
        request = ImageRequest(
            model=model,
            prompt=prompt,
            references=tuple(references),
            aspect_ratio=self._store.brief(campaign_id).aspect_ratio,
            variants=count,
            out_stem=self._store.take_stem(campaign_id, step.id),
            max_usd=max_usd,
        )
        made = [replace(m, step=step.id) for m in self._generators.image(provider).generate(request)]
        for m in made:
            self._store.record(campaign_id, m)
        return made
