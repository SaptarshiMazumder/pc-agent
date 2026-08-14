"""The STORE VIEW of a registry index — one finished row per bundle, ready to render.

WHY THIS IS ITS OWN MODULE. A store card needs three things an index entry does not carry
directly: the creator's NAME (the entry names them by opaque id; only the signed roster has the
name), an installer URL a browser can follow (the entry's is relative to the index), and the
Open-in-browser link (joined from the index-level web host and the bundle id). Working those out
is a join over the whole index, not a property of one entry — so it lived in the daemon's
marketplace service, which meant the ONLY way to see a store was to run a daemon.

It is pure and it lives in the domain, so exactly ONE implementation of "what a store row is"
feeds all three consumers:

  * the DAEMON, on every `marketplace.catalog` call, over an index it has verified;
  * the PUBLISH SERVICE, which writes the result to ``catalog.json`` beside ``index.json``;
  * the PUBLIC MARKETPLACE PAGE, which fetches that file and has no daemon and no keys.

WHAT THIS IS NOT. It is not a trust boundary. Nothing here verifies anything — verification is
the registry client's job and happens where a bundle is DOWNLOADED and RUN. That is why the
daemon builds its rows from the index it just verified rather than reading ``catalog.json``: the
generated file is a rendering convenience for a page that only ever draws links, and a client
that installs software must never take its facts from it.

RELATIVE OR ABSOLUTE URLS. Index urls are relative so the same folder works from disk and from a
CDN, and joining them belongs to whoever knows where the index came from — the registry client
for the daemon, the index store for the publish service. Both hand that knowledge in here; a
directory build passes neither and its rows stay relative, which is correct for a folder that
still has to work after being uploaded somewhere else. ``base`` is recorded in the document so a
reader that receives relative rows knows what to join them against.
"""

from __future__ import annotations

from collections.abc import Callable
from urllib.parse import urljoin

from agent_runtime.domain.bundle import RegistryEntry, RegistryIndex

# The name of the generated file, beside index.json. Named here because both the writer
# (publish) and every reader (the web page, the uploader) must spell it the same way.
CATALOG_FILENAME = "catalog.json"

# Bumped only when a row's SHAPE changes incompatibly. A reader that does not recognise the
# number should fall back to index.json rather than render a store it may be misreading.
CATALOG_SCHEMA = 1


def build_catalog(
    index: RegistryIndex,
    *,
    base: str = "",
    resolve: Callable[[str], str] | None = None,
) -> dict:
    """The whole store, as plain JSON-able data.

    :param base: the url relative artifact paths resolve against ("" => leave them relative).
    :param resolve: an explicit joiner, when the caller knows better than ``base`` can express
        (the daemon's registry client, which knows the exact index url it fetched).
    """
    join = resolve or (lambda url: urljoin(base, url) if base and url else url)
    names = index.publishers.display_names() if index.publishers else {}
    return {
        "schema": CATALOG_SCHEMA,
        "registry": index.name,
        "publisher": index.publisher,
        # Recorded rather than applied when there is nothing to apply: a reader holding relative
        # rows and an empty base joins against the catalog's own location, which is the right
        # answer for a registry served straight out of its own directory.
        "base": base,
        "webHost": index.web_host,
        "bundles": [_row(entry, join, index, names) for entry in index.bundles],
    }


def _row(
    entry: RegistryEntry,
    join: Callable[[str], str],
    index: RegistryIndex,
    publisher_names: dict[str, str],
) -> dict:
    """One catalog row."""
    out = {
        "id": entry.id,
        "name": entry.name,
        "version": entry.version,
        "description": entry.description,
        "agentdCompat": entry.agentd_compat,
        "price": entry.price,
        "entitlement": entry.entitlement,
        "size": entry.size,
        "icon": entry.icon,
        "delivery": {"web": entry.delivery.web, "exe": entry.delivery.exe},
        # WHO STANDS BEHIND IT. `publisherId` is the opaque creator id the entry's signature was
        # verified against — the identity that is actually proven — and `publisher` is the name to
        # put in front of a reader. Three sources, in falling order of how much they are worth:
        #
        #   1. the SIGNED roster, looked up by creator id — the only verified name there is;
        #   2. the entry's own declared name, which an operator-built index writes from bundle.toml
        #      and which therefore comes from the same party that built the index;
        #   3. the index's single publisher, the schema-1 answer to a question schema 1 asks once.
        #
        # An entry that names a creator NEVER falls past step 1. A missing roster name there means
        # the roster does not list them, and the honest render for that is the bare id — dropping
        # to a name they typed themselves would dress an unlisted creator as a known one.
        "publisherId": entry.publisher_id,
        "publisher": publisher_names.get(entry.publisher_id, "")
        or ("" if entry.publisher_id else (entry.publisher or index.publisher)),
    }
    if entry.delivery.web and index.web_host:
        out["webUrl"] = f"{index.web_host.rstrip('/')}/apps/{entry.id}/"
    if entry.installers:
        out["installers"] = [
            {
                "platform": asset.platform,
                "url": join(asset.url),
                "size": asset.size,
                "sha256": asset.sha256,
            }
            for asset in entry.installers
        ]
    return out
