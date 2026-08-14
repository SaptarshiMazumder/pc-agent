"""catalog.json — the STORE VIEW of a registry, generated where the registry is written.

WHY IT EXISTS. Browsing the marketplace used to require a daemon: the join that turns index
entries into store cards (creator names off the signed roster, absolute installer urls, the
Open-in-browser link) lived in the daemon's marketplace service, so the only way to see the store
was to already run the product. The join is now one domain function, run once at publish time and
written beside index.json, so a plain static page can render the same store with no daemon, no
keys and no server.

The properties pinned here:

  * ONE implementation. The daemon's `marketplace.catalog` and the generated file come from the
    same builder, so a card cannot mean two different things in two clients.
  * The daemon builds its rows from the index it VERIFIED, never from the generated file — a
    client that installs software must not take its facts from a rendering.
  * Urls: relative in a directory registry (it has to survive being copied), absolute when the
    writer knows the public base, and the base is recorded either way.
  * Generating it can NEVER fail a publish. index.json is the registry; a stale catalog is a
    slightly old page.
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain.bundle import (
    DeliveryModes,
    InstallerAsset,
    PublisherEntry,
    PublisherRoster,
    RegistryEntry,
    RegistryIndex,
)
from agent_runtime.domain.catalog import CATALOG_FILENAME, build_catalog
from agent_runtime.infrastructure.publish.index_store import S3IndexStore


def _entry(**kw) -> RegistryEntry:
    base = {"id": "game-master", "name": "Game Master", "version": "0.3.0"}
    base.update(kw)
    return RegistryEntry(**base)


# ────────────────────────────── the rows ──────────────────────────────


def test_a_row_carries_everything_a_card_shows():
    doc = build_catalog(RegistryIndex(name="agentd", bundles=(_entry(description="d", icon="dice"),)))
    row = doc["bundles"][0]
    assert row["id"] == "game-master"
    assert row["name"] == "Game Master"
    assert row["version"] == "0.3.0"
    assert row["description"] == "d"
    assert row["icon"] == "dice"
    assert row["delivery"] == {"web": False, "exe": True}
    assert doc["registry"] == "agentd"


def test_the_publisher_name_comes_from_the_signed_roster():
    """An entry names its creator by opaque id; the roster is the only place that id has a name."""
    roster = PublisherRoster(
        entries=(PublisherEntry(id="cr-1", name="Bio Labs", key="k"),), issued="2026-01-01"
    )
    doc = build_catalog(RegistryIndex(bundles=(_entry(publisher_id="cr-1"),), publishers=roster))
    assert doc["bundles"][0]["publisher"] == "Bio Labs"
    assert doc["bundles"][0]["publisherId"] == "cr-1"


def test_an_unlisted_creator_renders_as_a_bare_id_not_a_name_they_typed():
    """Falling back to the manifest's own `publisher` would dress an unlisted creator as a known
    one — a name nobody signed, rendered next to an id that was verified."""
    doc = build_catalog(RegistryIndex(bundles=(_entry(publisher_id="cr-9", publisher="Totally Legit"),)))
    assert doc["bundles"][0]["publisher"] == ""


def test_the_open_in_browser_link_is_finished_or_absent():
    web = _entry(delivery=DeliveryModes(web=True))
    doc = build_catalog(RegistryIndex(bundles=(web,), web_host="https://run.example/"))
    assert doc["bundles"][0]["webUrl"] == "https://run.example/apps/game-master/"
    assert doc["webHost"] == "https://run.example/"
    # no hosted deployment known, and an author who never opted in: no link either way
    assert "webUrl" not in build_catalog(RegistryIndex(bundles=(web,)))["bundles"][0]
    assert (
        "webUrl"
        not in build_catalog(RegistryIndex(bundles=(_entry(),), web_host="https://run.example"))[
            "bundles"
        ][0]
    )


# ────────────────────────────── urls ──────────────────────────────


def test_a_base_makes_installer_urls_absolute_and_is_recorded():
    """The public page fetches catalog.json from a different origin than the artifacts, so a
    relative row there would resolve against the SITE and 404."""
    entry = _entry(installers=(InstallerAsset(platform="win", url="gm-0.3.0-setup.exe", size=9),))
    doc = build_catalog(RegistryIndex(bundles=(entry,)), base="https://cdn.example/registry/")
    assert doc["base"] == "https://cdn.example/registry/"
    assert doc["bundles"][0]["installers"][0]["url"] == "https://cdn.example/registry/gm-0.3.0-setup.exe"


def test_no_base_leaves_urls_relative():
    """A directory registry is browsed from its own location and must survive being copied."""
    entry = _entry(installers=(InstallerAsset(platform="win", url="gm-0.3.0-setup.exe"),))
    doc = build_catalog(RegistryIndex(bundles=(entry,)))
    assert doc["bundles"][0]["installers"][0]["url"] == "gm-0.3.0-setup.exe"
    assert doc["base"] == ""


# ────────────────────────────── generation, at both writers ──────────────────────────────


def test_building_a_directory_index_also_writes_the_catalog(tmp_path):
    from agent_runtime.infrastructure.marketplace.index_builder import build_index

    build_index(tmp_path, name="local")
    catalog = json.loads((tmp_path / CATALOG_FILENAME).read_text(encoding="utf-8"))
    assert catalog["registry"] == "local"
    assert catalog["bundles"] == []


class FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.meta = type("meta", (), {"region_name": "ap-northeast-1"})()

    def get_object(self, Bucket, Key):  # noqa: N803
        raise KeyError("NoSuchKey")

    def put_object(self, Bucket, Key, Body, ContentType="", CacheControl=""):  # noqa: N803
        self.objects[Key] = Body


def test_writing_the_index_refreshes_the_catalog_beside_it():
    """Every writer of the index — publishing AND roster admission — reaches it through this one
    method, so no code path can leave the store page stale by forgetting about the catalog."""
    s3 = FakeS3()
    S3IndexStore(s3, "bucket").write_index(
        {
            "schema": 1,
            "name": "agentd",
            "bundles": [{"id": "gm", "name": "GM", "version": "1.0.0", "url": "gm-1.0.0.agentpkg"}],
        }
    )
    catalog = json.loads(s3.objects[CATALOG_FILENAME])
    assert [b["id"] for b in catalog["bundles"]] == ["gm"]
    assert catalog["base"] == "https://bucket.s3.ap-northeast-1.amazonaws.com/"


def test_a_prefixed_registry_keeps_its_prefix_in_the_base():
    """urljoin against a base with no trailing slash REPLACES the last segment, which would drop
    the prefix and 404 every download in the store."""
    s3 = FakeS3()
    S3IndexStore(s3, "bucket", prefix="registry").write_index({"bundles": []})
    catalog = json.loads(s3.objects["registry/" + CATALOG_FILENAME])
    assert catalog["base"] == "https://bucket.s3.ap-northeast-1.amazonaws.com/registry/"


def test_a_broken_catalog_never_fails_the_publish():
    """index.json is the registry. Reporting failure for a rendering nothing installs from would
    be a worse outcome than the stale page it prevents."""

    class HalfBrokenS3(FakeS3):
        def put_object(self, Bucket, Key, Body, ContentType="", CacheControl=""):  # noqa: N803
            if Key == CATALOG_FILENAME:
                raise RuntimeError("s3 is having a day")
            super().put_object(Bucket, Key, Body, ContentType, CacheControl)

    s3 = HalfBrokenS3()
    S3IndexStore(s3, "bucket").write_index({"bundles": []})  # must not raise
    assert "index.json" in s3.objects


# ────────────────────────────── the daemon still verifies ──────────────────────────────


@pytest.mark.asyncio
async def test_the_daemon_builds_rows_from_the_index_it_verified():
    """Not from catalog.json. The generated file is a convenience for a page that only draws
    links; a client that downloads and RUNS software takes its facts from the signed document."""
    from agent_runtime.application.services.marketplace_service import MarketplaceService

    fetched = []

    class Registry:
        async def fetch_index(self):
            fetched.append("index")
            return RegistryIndex(name="r", bundles=(_entry(),))

    class Store:
        def list(self):
            return []

    service = MarketplaceService(
        registry_client=Registry(),
        installer=object(),
        installed_store=Store(),
        agentd_version="0.1.0",
        download_dir=Path("."),
    )
    doc = await service.catalog()
    assert fetched == ["index"]
    assert doc["bundles"][0]["id"] == "game-master"
    # the daemon-only facts a page has no answer for
    assert doc["bundles"][0]["installed"] is False
    assert doc["bundles"][0]["compatible"] is True
