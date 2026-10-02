"""A description (and optional look references) -> a recurring AI model with a character sheet."""

from __future__ import annotations

from ad_generation.application.interfaces.cast_library import CastLibrary
from ad_generation.application.interfaces.generator_catalog import GeneratorCatalog
from ad_generation.domain.cast_member import CastMember
from ad_generation.domain.generated_media import GeneratedMedia
from ad_generation.domain.image_request import ImageRequest


class CastService:
    def __init__(self, generators: GeneratorCatalog, cast: CastLibrary, sheet_prompt: str) -> None:
        self._generators = generators
        self._cast = cast
        self._sheet_prompt = sheet_prompt  # template with {description}

    def create(
        self, name: str, description: str, references: list[str], provider: str, model: str
    ) -> tuple[CastMember, GeneratedMedia]:
        request = ImageRequest(
            model=model,
            prompt=self._sheet_prompt.format(description=description),
            references=tuple(references),
            aspect_ratio="16:9",
            variants=1,
            out_stem=self._cast.sheet_stem(name),
        )
        sheet = self._generators.image(provider).generate(request)[0]
        member = CastMember(name=name, description=description, sheet=sheet.path)
        self._cast.save(member)
        return member, sheet
