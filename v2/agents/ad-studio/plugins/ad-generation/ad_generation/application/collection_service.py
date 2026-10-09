"""Gathering generations into collections, from any campaign — what a post is made from."""

from __future__ import annotations

import time

from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.collection_store import CollectionStore
from ad_generation.domain.collection import Collection, CollectionItem, ProductSource

_VIDEO = (".mp4", ".mov", ".webm")
_MEDIA = (".png", ".jpg", ".jpeg", ".webp", *_VIDEO)


class CollectionService:
    def __init__(self, collections: CollectionStore, campaigns: CampaignStore) -> None:
        self._collections = collections
        self._campaigns = campaigns

    def all(self) -> list[Collection]:
        return self._collections.all()

    def get(self, slug: str) -> Collection:
        return self._collections.get(slug)

    def add(self, collection: str, items: list[dict], new: bool = False) -> Collection:
        """`items`: [{path, campaign, note?}] — files of those campaigns. `collection` is an existing
        collection's name or slug, or with `new` the name of one to make."""
        found = self._target(collection, new)
        added = [self._item(i) for i in items]
        found.add(added)
        for i in added:
            found.know(i.product)
        self._collections.save(found)
        return found

    def import_files(self, collection: str, files: list[dict], new: bool = False) -> Collection:
        """The user's own images and clips, made elsewhere: `files` [{path (an upload), product,
        note?}] are copied into the collection, each under the product it shows."""
        found = self._target(collection, new)
        for f in files:
            src, product = str(f.get("path") or ""), str(f.get("product") or "").strip()
            if not product:
                raise ValueError(f"{src}: say which product it shows")
            if not src.lower().endswith(_MEDIA):
                raise ValueError(f"{src}: use an image (png, jpg, webp) or a clip (mp4, mov, webm)")
            path = self._collections.import_file(found.slug, src)
            found.add([CollectionItem(path=path, kind="video" if path.lower().endswith(_VIDEO) else "image",
                                      campaign="", product=product, note=str(f.get("note") or ""))])
            found.know(product)
        self._collections.save(found)
        return found

    def set_product(self, slug: str, product: dict) -> Collection:
        c = self._collections.get(slug)
        c.set_product(ProductSource.from_dict(product))
        self._collections.save(c)
        return c

    def _target(self, collection: str, new: bool) -> Collection:
        found = self._collections.find(collection)
        if new:
            if found:
                raise ValueError(f"a collection called '{collection}' already exists — add to it instead")
            if not collection.strip():
                raise ValueError("give the new collection a name")
            return Collection(slug=self._collections.new_slug(collection), name=collection.strip(), created=time.time())
        if found is None:
            raise KeyError(f"no collection '{collection}' (collections: {', '.join(c.name for c in self.all()) or 'none yet'})")
        return found

    def remove(self, slug: str, paths: list[str]) -> Collection:
        c = self._collections.get(slug)
        c.remove(paths)
        self._collections.save(c)
        return c

    def reorder(self, slug: str, paths: list[str]) -> Collection:
        c = self._collections.get(slug)
        c.reorder(paths)
        self._collections.save(c)
        return c

    def rename(self, slug: str, name: str) -> Collection:
        if not name.strip():
            raise ValueError("give the collection a name")
        c = self._collections.get(slug)
        c.name = name.strip()
        self._collections.save(c)
        return c

    def delete(self, slug: str) -> None:
        self._collections.delete(slug)

    def _item(self, raw: dict) -> CollectionItem:
        path, campaign = str(raw.get("path") or ""), str(raw.get("campaign") or "")
        if not path.startswith(f"campaigns/{campaign}/"):
            raise ValueError(f"{path} is not a file of campaign '{campaign}'")
        if path not in {m.path for m in self._campaigns.ledger(campaign)}:
            raise ValueError(f"{path} is not one of campaign {campaign}'s generations")
        return CollectionItem(
            path=path,
            kind="video" if path.lower().endswith(_VIDEO) else "image",
            campaign=campaign,
            product=self._campaigns.profile(campaign).name,
            note=str(raw.get("note") or ""),
        )
