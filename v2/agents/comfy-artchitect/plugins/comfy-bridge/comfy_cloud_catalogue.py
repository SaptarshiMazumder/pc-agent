"""ComfyCloudCatalogue — Comfy Cloud's node list as the PERSON sees it: with the models they imported.

`/api/object_info` is Comfy Cloud's shared node list: its loaders' choices are the 1,300+
preinstalled models, never a person's own. A model they import (`POST /api/assets/download`) is an
ASSET of their account, tagged `models` + its folder (`loras`, `checkpoints`, …); Comfy's own
editor fills a model dropdown from `/api/assets?include_tags=models,<folder>` and writes the asset's
file name — `user_metadata.filename`, else `metadata.filename`, else its name — into the workflow
(ComfyUI_frontend assetService / assetMetadataUtils). Checked against the shared list alone, every
imported model reads as missing: an import "never lands", a design using one never validates.

So the person's model assets are merged into the loaders that read their folder, and everything
that asks "can Comfy Cloud load this file?" asks this one catalogue. A failure to read the assets
is said, not skipped: without them the answer would be wrong, not partial.
"""

from __future__ import annotations

import copy
from collections.abc import Callable

from model_readiness import ModelReadiness

#: Assets read per page (the API's maximum).
PAGE = 500


class ComfyCloudCatalogue:
    def __init__(self, get: Callable) -> None:
        """:param get: (path, timeout_s=) -> Response, already carrying the person's key."""
        self._get = get

    def with_own_models(self, object_info: dict) -> dict:
        """`object_info` with each of the person's imported models added to the loaders of its folder."""
        return self.merge(object_info, self.own_models())

    def own_models(self) -> dict[str, list[str]]:
        """{folder: [file name a workflow uses]} for the models the person imported."""
        out: dict[str, list[str]] = {}
        for asset in self.own_assets():
            name = self.file_name(asset)
            for folder in asset.get("tags") or []:
                if folder in ModelReadiness.DIRECTORY_FIELDS and name:
                    out.setdefault(folder, []).append(name)
        return out

    def own_loras(self) -> list[tuple[str, str]]:
        """[(file name, Civitai base or '')] for the LoRAs the person imported — the base when the
        import recorded it (setup does; one made in Comfy's own window does not)."""
        return [(self.file_name(a), str((a.get("user_metadata") or {}).get("base_model") or ""))
                for a in self.own_assets() if "loras" in (a.get("tags") or []) and self.file_name(a)]

    def own_assets(self) -> list[dict]:
        """Every model asset of the person's own (not Comfy Cloud's shared ones)."""
        out: list[dict] = []
        offset = 0
        while True:
            res = self._get(f"/api/assets?include_tags=models&include_public=false&limit={PAGE}&offset={offset}",
                            timeout_s=30.0)
            if not res.ok:
                raise ValueError(f"Comfy Cloud did not list the models imported into this account (HTTP "
                                 f"{res.status} {res.error or (res.text or '')[:200]}) — without them an imported "
                                 "model would read as missing")
            body = res.json() or {}
            out += [a for a in body.get("assets") or [] if isinstance(a, dict)]
            if not body.get("has_more"):
                return out
            offset += PAGE

    @staticmethod
    def file_name(asset: dict) -> str:
        """The name a workflow names the asset by — the editor's own rule."""
        for meta in (asset.get("user_metadata") or {}, asset.get("metadata") or {}):
            if isinstance(meta.get("filename"), str) and meta["filename"]:
                return meta["filename"]
        return str(asset.get("name") or "")

    @staticmethod
    def merge(object_info: dict, own: dict[str, list[str]]) -> dict:
        """Pure: each folder's files added to every loader field that reads that folder."""
        if not own:
            return object_info
        fields = {f: folder for folder, fs in ModelReadiness.DIRECTORY_FIELDS.items() for f in fs}
        out = dict(object_info)
        for node_class, spec in object_info.items():
            if not isinstance(spec, dict):
                continue
            changed = None
            for section in ("required", "optional"):
                for field, entry in ((spec.get("input") or {}).get(section) or {}).items():
                    folder = fields.get(field)
                    choices = entry[0] if isinstance(entry, list) and entry else None
                    if folder not in own or not isinstance(choices, list):
                        continue
                    extra = [n for n in own[folder] if n not in choices]
                    if not extra:
                        continue
                    changed = changed or copy.deepcopy(spec)
                    changed["input"][section][field][0] = choices + extra
            if changed is not None:
                out[node_class] = changed
        return out


__all__ = ["ComfyCloudCatalogue"]
