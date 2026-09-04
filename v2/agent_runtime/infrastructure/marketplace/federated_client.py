"""FederatedRegistryClient — the public marketplace PLUS every registry this caller's
organizations publish to, behind the one RegistryClient port.

WHY THIS EXISTS. An enterprise publish is the same pipeline as a marketplace publish against a
different shelf (`IndexStore.scoped`): the `orgs/<org_id>/` prefix. Uploading there is only half
of "everyone in my company has it" — a member's client has to READ that shelf too, and
the marketplace service takes exactly one client. So the fan-out lives here rather than in the
service, which keeps installing, verifying and unpacking identical whichever shelf a bundle came
from, and keeps "which registries am I entitled to" a composition-root question.

EACH REGISTRY VERIFIES ITSELF. Every member client is a full RegistryClient with its own pinned
root key and its own roster memory, so an org index is verified exactly as strictly as the public
one: root-signed roster, creator-signed bundles, replay-detected by issue date. This class never
looks at a signature — it merges already-trusted listings and remembers which client produced
each row so `download` goes back to the same one.

ORG ROWS WIN AN ID COLLISION, matching the layer precedence agents already use everywhere else
(curated < org < personal): a company that publishes its own `weather` means the one its staff
should get. The public row is not deleted, merely shadowed — and only for that org's members.

A BROKEN ORG SHELF MUST NOT TAKE DOWN THE MARKETPLACE. A registry that 404s (an org that has
never published), times out, or fails verification is logged and skipped; the rest of the listing
still renders. The inverse — one unreachable shelf blanking a store that was working — is the
failure this shape exists to avoid, and it is why the public client is fetched first and its
errors alone are allowed to propagate.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from pathlib import Path

from agent_runtime.domain.bundle import RegistryEntry, RegistryIndex

log = logging.getLogger("agentd")


def org_index_url(publish_url: str, org_id: str) -> str:
    """One organization's registry, read through the PUBLISH SERVICE rather than off the CDN.

    NOT a bucket path. The public marketplace is a static index anyone may GET; an org's registry
    is carved out of that grant (infra/modules/registry.tf) precisely because an org id is not a
    secret -- it rides in tokens, in the UI, in logs and in support threads, and "nobody will find
    the url" is not an access control an enterprise can be sold. So the service authenticates the
    member, checks the membership, and returns the index with presigned artifact links.

    "" when either half is missing, which is how a build with no publish service ends up with the
    public marketplace alone instead of a shelf it could never read.
    """
    base = (publish_url or "").strip().rstrip("/")
    org = (org_id or "").strip()
    return f"{base}/registry/org/{org}" if base and org else ""


class FederatedRegistryClient:
    """:param public: the marketplace client (None for a deployment with no public registry).
    :param org_clients: ``[(org_id, client), ...]`` — one per organization this caller belongs to.
    """

    def __init__(self, public, org_clients=()):
        self._public = public
        self._orgs = list(org_clients)
        #: bundle id -> the client that listed it, so `download` asks the shelf the row came from.
        #: Rebuilt on every fetch_index: a stale route would download the wrong registry's bytes.
        self._route: dict[str, object] = {}

    # ------------------------------------------------------------------ index
    async def fetch_index(self) -> RegistryIndex:
        index = RegistryIndex()
        self._route = {}

        if self._public is not None:
            # NOT guarded: a marketplace that cannot be read is a real failure and the caller
            # must see it. Only the org shelves below are best-effort.
            index = await self._public.fetch_index()
            for entry in index.bundles:
                self._route[entry.id] = self._public

        rows: dict[str, RegistryEntry] = {e.id: e for e in index.bundles}
        for org_id, client in self._orgs:
            try:
                org_index = await client.fetch_index()
            except Exception as e:  # noqa: BLE001 — one shelf must not blank the others
                # DEBUG, not WARNING: an org that has simply never published has no index, and
                # that is the common case rather than a fault worth a log line per store open.
                log.debug("org registry %s unavailable: %s", org_id, e)
                continue
            for entry in org_index.bundles:
                # CARRIED EXACTLY. Not re-stamped with the org id, tempting as that is for
                # provenance: `publisher_id` is the ROSTER LOOKUP KEY — the client finds the
                # creator's public key by it, and an empty one means "the index's own key". Either
                # way, writing an org id into it points verification at a key that does not exist
                # and every install of a company's own agent fails closed. Which shelf a row came
                # from is remembered beside the row, in `_route`, where it costs nothing.
                rows[entry.id] = entry
                self._route[entry.id] = client
            # The engine list and web host are deployment facts, identical on every shelf; take
            # them from an org index only when the public one had none (a private deployment).
            if not index.engines and org_index.engines:
                index = replace(index, engines=org_index.engines)
            if not index.web_host and org_index.web_host:
                index = replace(index, web_host=org_index.web_host)

        return replace(index, bundles=tuple(sorted(rows.values(), key=lambda e: e.id)))

    # ------------------------------------------------------------------ download
    async def download(self, entry: RegistryEntry, dest_dir: Path) -> Path:
        """Route to the shelf that listed this row.

        A row whose id was never seen falls back to the public client — the honest default when
        the caller assembled an entry itself rather than taking one from `fetch_index`. With no
        public registry at all that is an error, and it says which id could not be placed rather
        than failing on a None attribute.
        """
        client = self._route.get(entry.id) or self._public
        if client is None:
            raise LookupError(
                f"no registry to download '{entry.id}' from — it is not in the public listing "
                "and no organization registry offered it."
            )
        return await client.download(entry, dest_dir)
