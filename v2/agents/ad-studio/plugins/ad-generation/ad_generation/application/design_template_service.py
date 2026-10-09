"""The design template library: browse it, keep a slide's design as a template, drop your own."""

from __future__ import annotations

from ad_generation.application.interfaces.design_template_store import DesignTemplateStore
from ad_generation.application.interfaces.post_store import PostStore
from ad_generation.domain.design_template import DesignTemplate


class DesignTemplateService:
    def __init__(self, templates: DesignTemplateStore, posts: PostStore) -> None:
        self._templates = templates
        self._posts = posts

    def all(self) -> list[DesignTemplate]:
        return self._templates.all()

    def save_from_slide(self, slug: str, number: int, name: str, description: str, tags: list[str]) -> DesignTemplate:
        post = self._posts.get(slug)
        if not 1 <= number <= len(post.slides):
            raise ValueError(f"post {slug} has slides 1-{len(post.slides)}, not {number}")
        slide = post.slides[number - 1]
        if not slide.design:
            raise ValueError(f"slide {number} has no design yet — design it first (post_design)")
        if not name.strip():
            raise ValueError("name the template, e.g. 'Festive editorial card'")
        kind = "video" if slide.kind == "video" else "still"
        return self._templates.save(name, description, kind, tags, slide.design, slide.preview)

    def delete(self, slug: str) -> None:
        self._templates.delete(slug)
