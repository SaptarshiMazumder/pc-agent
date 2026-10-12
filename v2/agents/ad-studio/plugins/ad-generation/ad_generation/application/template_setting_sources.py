"""What a setting's `source` provides: the options the user picks from, and the words a pick
becomes in the brief.

    locations        the location library — the category's own places first; a picked name
                     becomes that place described ("Jaipur haveli courtyard" -> sandstone arches…)
    category_poses   the product's category's poses (a saree's drapes, a necklace's framings)

A setting with no source keeps its template's own options. Free words are always allowed: a value
that names nothing in the library is used as the user wrote it.
"""

from __future__ import annotations

from ad_generation.application.interfaces.location_library import LocationLibrary
from ad_generation.domain.category_profile import CategoryProfile
from ad_generation.domain.template_setting import TemplateSetting


class TemplateSettingSources:
    def __init__(self, locations: LocationLibrary) -> None:
        self._locations = locations

    def options(self, setting: TemplateSetting, category: CategoryProfile | None) -> tuple[str, ...]:
        if setting.source == "locations":
            places = self._locations.all()
            first = [p for key in (category.locations if category else ()) for p in places if p.key == key]
            return tuple(p.name for p in first + [p for p in places if p not in first])
        if setting.source == "category_poses":
            return category.poses if category and category.poses else setting.options
        return setting.options

    def words(self, setting: TemplateSetting, value: str) -> str:
        if setting.source == "locations":
            return next((p.prompt for p in self._locations.all() if p.name == value), value)
        return value
