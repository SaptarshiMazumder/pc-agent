"""CivitaiClient — what the design needs from Civitai's public API, and nothing else.

Read-only, anonymous: searching and describing models needs no key (a download does — that is the
importer's business, with the person's own key). Every call is one GET through the injected
`fetch`, and every answer is reshaped here into the few fields a design uses, so no tool parses
Civitai's JSON itself.

THE TRAPS IT ABSORBS (live, 2026-10; comfy_kb/lora_research.md §4-5):
  * an unknown `baseModels` value is NOT refused — it silently returns nothing, so the bases come
    from the family profiles, which were checked against /api/v1/enums;
  * `query` TOGETHER WITH `baseModels` returns nothing at all (live: "anime" on ZImageTurbo → 0,
    though dozens exist). So a worded search asks without the base filter, takes a full page (100)
    and keeps the versions trained on the bases here; an unworded one filters by base on Civitai;
  * `query` cannot be paged with `page` (cursor only) — one page is all a search needs;
  * a search can fail SERVER-SIDE for one exact request — live, 2026-10: LORA+LoCon+DoRA, query
    "Retro Anime Flux V1", 100 results → HTTP 500 every time, while 20 results work (one item in
    the bigger page breaks Civitai's own server). A search that fails with a 5xx is asked again
    in a page of SMALL_PAGE;
  * one model holds versions for many bases ("Lenovo UltraReal": 13): only versions of the
    stage's bases count, and a two-expert pair is two versions of one model (high, low);
  * there is no strength field: the author's advice lives in the description's free text.
"""

from __future__ import annotations

import html
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from urllib.parse import urlencode

API = "https://civitai.com/api/v1"
#: The page a search falls back to when the full page fails on Civitai's side.
SMALL_PAGE = 20

#: The LoRA-like model types Civitai lists separately; all load through the same LoRA nodes.
LORA_TYPES = ("LORA", "LoCon", "DoRA")


@dataclass(frozen=True)
class CivitaiLora:
    model_id: int
    version_id: int
    name: str
    version: str
    base: str
    file: str
    size_mb: float
    download_url: str
    downloads: int
    liked: int
    disliked: int
    trained_words: list[str] = field(default_factory=list)
    strength_hint: str = ""
    #: The model's other versions for the same bases — the other half of a high/low pair.
    others: list["CivitaiLora"] = field(default_factory=list)

    @property
    def page(self) -> str:
        return f"https://civitai.com/models/{self.model_id}?modelVersionId={self.version_id}"


@dataclass(frozen=True)
class CivitaiImage:
    image_id: int
    url: str  # the ORIGINAL file (`original=true`): the only variant that keeps its metadata
    width: int
    height: int
    base: str  # the base model Civitai files the image under ("Flux.1 D")
    meta: dict  # Civitai's generation record, unwrapped; {} when it kept none


class CivitaiError(RuntimeError):
    """Civitai did not answer usefully; the message says how. `status` is its HTTP status (0 when
    it could not be reached or the answer was not usable)."""

    def __init__(self, message: str, status: int = 0) -> None:
        super().__init__(message)
        self.status = status


class CivitaiClient:
    def __init__(self, fetch: Callable[..., object]) -> None:
        """:param fetch: outbound.fetch's shape — (url, timeout_s=) -> Response(ok, status, text, json())."""
        self._fetch = fetch

    def search_loras(self, bases: list[str], query: str = "", limit: int = 8) -> list[CivitaiLora]:
        """The most-downloaded safe-for-work LoRAs trained on one of `bases`, matching `query`."""
        params = [("types", t) for t in LORA_TYPES] + [("sort", "Most Downloaded"), ("nsfw", "false")]
        if query.strip():
            params += [("query", query.strip()), ("limit", "100")]
        else:
            params += [("baseModels", b) for b in bases] + [("limit", str(max(1, min(limit, 100))))]
        data = self._models(params)
        out = [hit for item in data.get("items") or [] if (hit := self._lora(item, set(bases))) is not None]
        return sorted(out, key=lambda lo: -lo.downloads)[:limit]

    def image(self, image_id: int) -> CivitaiImage:
        """An image page's record. `withMeta` is required — without it `meta` is always null — and a
        single-image answer nests the record as `meta.meta`."""
        data = self._get(f"{API}/images?{urlencode({'imageId': image_id, 'withMeta': 'true'})}")
        items = data.get("items") or []
        if not items:
            raise CivitaiError(f"Civitai has no image {image_id} (deleted, private, or a mature image)")
        item = items[0]
        meta = item.get("meta") or {}
        if isinstance(meta, dict) and set(meta) == {"id", "meta"}:
            meta = meta.get("meta") or {}
        url = str(item.get("url") or "")
        if url and "original=true" not in url:
            url = re.sub(r"/(width=\d+[^/]*)/", "/original=true/", url)
        return CivitaiImage(image_id=int(item.get("id") or image_id), url=url, width=int(item.get("width") or 0),
                            height=int(item.get("height") or 0), base=str(item.get("baseModel") or ""),
                            meta=meta if isinstance(meta, dict) else {})

    def version(self, version_id: int) -> CivitaiLora | None:
        """A model version by id (an image's civitaiResources, an AIR URN)."""
        return self._from_version(self._get(f"{API}/model-versions/{int(version_id)}", missing_ok=True))

    def version_by_hash(self, file_hash: str) -> CivitaiLora | None:
        """A model version by its file's hash — AutoV1/V2/V3, SHA256, BLAKE3 or CRC32 (A1111's
        "Lora hashes" are AutoV3)."""
        return self._from_version(self._get(f"{API}/model-versions/by-hash/{file_hash.strip()}", missing_ok=True))

    def lora_by_file(self, file_name: str) -> CivitaiLora | None:
        """The LoRA version whose file is `file_name` — a ComfyUI upload names only its file. Found by
        searching the file's words and keeping the version that ships exactly that file."""
        base = file_name.replace("\\", "/").rsplit("/", 1)[-1]
        stem = base.rsplit(".", 1)[0]
        words = re.sub(r"([a-z])([A-Z])", r"\1 \2", stem)
        words = re.sub(r"[^A-Za-z0-9]+", " ", words).strip()
        queries = [words] + ([re.sub(r"\s*v?\d+(\s+\d+)*$", "", words, flags=re.I)]
                             if re.search(r"v?\d+$", words, re.I) else [])
        for query in dict.fromkeys(q for q in queries if q):
            params = [("types", t) for t in LORA_TYPES] + [("query", query), ("limit", "100")]
            for item in self._models(params).get("items") or []:
                for version in item.get("modelVersions") or []:
                    if any(str(f.get("name") or "").lower() == base.lower() for f in version.get("files") or []):
                        hit = self._version(item, version)
                        if hit is not None:
                            return hit
        return None

    # ------------------------------------------------------------------ reshaping

    @classmethod
    def _from_version(cls, version: dict | None) -> CivitaiLora | None:
        """A `/model-versions/…` answer: the model rides along as `model` and `modelId`."""
        if not version:
            return None
        item = {"id": version.get("modelId") or 0, "name": (version.get("model") or {}).get("name") or "",
                "stats": version.get("stats") or {}, "description": version.get("description") or ""}
        return cls._version(item, version)

    @classmethod
    def _lora(cls, item: dict, bases: set[str]) -> CivitaiLora | None:
        """The model's first loadable version for `bases`, carrying the others."""
        versions = [v for v in (cls._version(item, v) for v in item.get("modelVersions") or []
                                if v.get("baseModel") in bases) if v is not None]
        if not versions:
            return None
        first, *rest = versions
        return CivitaiLora(**{**first.__dict__, "others": rest})

    @classmethod
    def _version(cls, item: dict, version: dict) -> CivitaiLora | None:
        files = [f for f in version.get("files") or [] if str(f.get("name") or "").endswith(".safetensors")]
        primary = next((f for f in files if f.get("primary")), files[0] if files else None)
        if primary is None:
            return None  # a .zip pair or a pickle: nothing ComfyUI loads as it is
        stats = item.get("stats") or {}
        return CivitaiLora(
            model_id=int(item["id"]), version_id=int(version["id"]), name=str(item.get("name") or ""),
            version=str(version.get("name") or ""), base=str(version.get("baseModel") or ""),
            file=str(primary["name"]), size_mb=round(float(primary.get("sizeKB") or 0) / 1024, 1),
            download_url=str(version.get("downloadUrl") or f"https://civitai.com/api/download/models/{version['id']}"),
            downloads=int(stats.get("downloadCount") or 0), liked=int(stats.get("thumbsUpCount") or 0),
            disliked=int(stats.get("thumbsDownCount") or 0),
            trained_words=[str(w).strip(" ,") for w in version.get("trainedWords") or [] if str(w).strip(" ,")],
            strength_hint=cls._strength_hint(str(item.get("description") or "")),
        )

    @staticmethod
    def _strength_hint(description: str) -> str:
        """The author's own words on strength/weight, if the description has any."""
        text = html.unescape(re.sub(r"<[^>]+>", " ", description))
        m = re.search(r"(?:[^.\n]|\.\d){0,40}\b(strength|weight)s?\b(?:[^.\n]|\.\d){0,60}", text, re.I)
        return re.sub(r"\s+", " ", m.group(0)).strip() if m else ""

    def _models(self, params: list[tuple[str, str]]) -> dict:
        """A `/models` search; one that fails on Civitai's side is asked again in a smaller page."""
        try:
            return self._get(f"{API}/models?{urlencode(params)}")
        except CivitaiError as e:
            limit = int(dict(params).get("limit") or SMALL_PAGE)
            if e.status < 500 or limit <= SMALL_PAGE:
                raise
            smaller = [(k, v) for k, v in params if k != "limit"] + [("limit", str(SMALL_PAGE))]
            return self._get(f"{API}/models?{urlencode(smaller)}")

    def _get(self, url: str, missing_ok: bool = False) -> dict:
        """The JSON object at `url`; {} for a 404 when `missing_ok` (a hash Civitai does not know)."""
        res = self._fetch(url, timeout_s=30.0)
        if res.error:
            raise CivitaiError(f"Civitai could not be reached ({res.error})")
        if missing_ok and res.status == 404:
            return {}
        if not res.ok:
            raise CivitaiError(f"Civitai answered HTTP {res.status}: {(res.text or '')[:200]}", res.status)
        data = res.json()
        if not isinstance(data, dict):
            raise CivitaiError("Civitai answered with something that is not a JSON object")
        if data.get("error"):
            raise CivitaiError(f"Civitai: {data['error']}")
        return data


__all__ = ["API", "CivitaiClient", "CivitaiError", "CivitaiImage", "CivitaiLora", "LORA_TYPES"]
