"""The account's library: every generation of every campaign, and the user's own uploads — browsed
in one place, and any mix of them picked into a collection (what a post is made from). Free and
instant."""

from __future__ import annotations

from ad_generation.application.collection_service import CollectionService
from ad_generation.application.interfaces.campaign_store import CampaignStore
from ad_generation.application.interfaces.library_store import LibraryStore
from ad_generation.domain.collection import Collection
from ad_generation.domain.library_item import LibraryItem


class LibraryService:
    def __init__(self, campaigns: CampaignStore, library: LibraryStore, collections: CollectionService) -> None:
        self._campaigns = campaigns
        self._library = library
        self._collections = collections

    def all(self) -> list[LibraryItem]:
        """Generations and uploads together, newest first."""
        made = [
            LibraryItem(path=m.path, kind=m.kind, source="campaign", campaign=cid,
                        product=self._campaigns.profile(cid).name, added=self._campaigns.made_at(m.path))
            for cid in self._campaigns.campaign_ids()
            for m in self._campaigns.ledger(cid)
        ]
        return sorted([*made, *self._library.uploads()], key=lambda i: i.added, reverse=True)

    def campaigns(self) -> list[dict]:
        """Each campaign's id and product name — what the library can be filtered by."""
        return [{"id": cid, "name": self._campaigns.profile(cid).name} for cid in self._campaigns.campaign_ids()]

    def upload(self, files: list[dict]) -> list[LibraryItem]:
        """`files` [{path (a workspace upload), label?}] -> kept in the library."""
        if not files:
            raise ValueError("no files to add")
        return [self._library.add_upload(str(f.get("path") or ""), str(f.get("label") or "")) for f in files]

    def remove(self, path: str) -> None:
        self._library.remove_upload(path)

    def to_collection(self, collection: str, new: bool, paths: list[str]) -> Collection:
        """Any mix of library items into a collection — an existing one (name or slug), or with
        `new` one named `collection`. Generations are linked from their campaign; uploads are copied
        in under their label."""
        if not paths:
            raise ValueError("pick something to add")
        known = {i.path: i for i in self.all()}
        missing = [p for p in paths if p not in known]
        if missing:
            raise ValueError(f"not in the library: {', '.join(missing)}")
        made = [{"path": p, "campaign": known[p].campaign} for p in paths if known[p].source == "campaign"]
        mine = [{"path": p, "product": known[p].product} for p in paths if known[p].source == "upload"]
        target, first = collection, new
        result = None
        if made:
            result = self._collections.add(target, made, first)
            target, first = result.slug, False
        if mine:
            result = self._collections.import_files(target, mine, first)
        return result
