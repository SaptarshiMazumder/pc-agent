"""One listing from many shelves: the public marketplace plus this caller's org registries.

An enterprise publish uploads to `<registry>/orgs/<org_id>/index.json`. That is only half of
"everyone in my company has it" — the member's client has to READ that shelf, and the marketplace
service takes exactly one client. These pin the fan-out that makes it one.

The guards that matter, in order:
  * an org row is installable, and routes its download back to the shelf that listed it
  * an org shelf that is missing or broken must NEVER blank the public listing
  * a broken PUBLIC registry is still a real failure and must surface
  * rows are carried EXACTLY — `publisher_id` is the roster lookup key, and rewriting it would
    fail verification on every member's machine
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_runtime.domain.bundle import RegistryEntry, RegistryIndex
from agent_runtime.infrastructure.marketplace.federated_client import (
    FederatedRegistryClient,
    org_index_url,
)

ORG = "org_82bdccbd70a0ffa7"


def entry(bundle_id: str, version: str = "1.0.0", publisher_id: str = "c-abc") -> RegistryEntry:
    return RegistryEntry(id=bundle_id, name=bundle_id, version=version, publisher_id=publisher_id)


class FakeClient:
    """One shelf. `boom` makes fetching raise, which is how an org that has never published (404)
    and one that fails verification both look from here."""

    def __init__(self, *entries, boom: str = "", engines=(), web_host=""):
        self.index = RegistryIndex(bundles=tuple(entries), engines=engines, web_host=web_host)
        self.boom = boom
        self.downloaded: list[str] = []

    async def fetch_index(self) -> RegistryIndex:
        if self.boom:
            raise RuntimeError(self.boom)
        return self.index

    async def download(self, e: RegistryEntry, dest_dir: Path) -> Path:
        self.downloaded.append(e.id)
        return dest_dir / f"{e.id}.agentpkg"


# ────────────────────────────── the url ──────────────────────────────


@pytest.mark.parametrize(
    "base,expected",
    [
        ("https://api.example", f"https://api.example/registry/org/{ORG}"),
        ("https://api.example/", f"https://api.example/registry/org/{ORG}"),
    ],
)
def test_an_org_shelf_is_read_through_the_publish_service(base, expected):
    """NOT a bucket path. `orgs/*` is carved out of the registry bucket's public-read grant, so
    the index comes from an endpoint that authenticates the member and presigns the artifacts --
    an org id is not a secret, and an unguessable url is not an access control."""
    assert org_index_url(base, ORG) == expected


def test_no_publish_service_or_no_org_derives_nothing():
    """A build with no publish service gets the public marketplace alone, rather than a shelf it
    could never read."""
    assert org_index_url("", ORG) == ""
    assert org_index_url("https://api.example", "") == ""


# ────────────────────────────── the merge ──────────────────────────────


@pytest.mark.asyncio
async def test_a_members_listing_holds_both_shelves():
    public, org = FakeClient(entry("weather")), FakeClient(entry("payroll"))
    index = await FederatedRegistryClient(public, [(ORG, org)]).fetch_index()
    assert [b.id for b in index.bundles] == ["payroll", "weather"]


@pytest.mark.asyncio
async def test_an_org_row_shadows_the_public_one_of_the_same_id():
    """Matching the layer precedence agents already use (curated < org < personal): a company that
    publishes its own `weather` means the one its staff should get."""
    public, org = FakeClient(entry("weather", "1.0.0")), FakeClient(entry("weather", "9.9.9"))
    index = await FederatedRegistryClient(public, [(ORG, org)]).fetch_index()
    assert [(b.id, b.version) for b in index.bundles] == [("weather", "9.9.9")]


@pytest.mark.asyncio
async def test_rows_are_carried_exactly_so_verification_still_works():
    """`publisher_id` is how a client finds the key that signed a bundle. Re-stamping it with the
    org id would point verification at a roster entry that does not exist, and every install of a
    company's own agent would fail closed."""
    org = FakeClient(entry("payroll", publisher_id="c-someone"))
    index = await FederatedRegistryClient(FakeClient(), [(ORG, org)]).fetch_index()
    assert index.bundles[0].publisher_id == "c-someone"


@pytest.mark.asyncio
async def test_deployment_facts_fill_in_from_an_org_shelf_only_when_the_public_one_has_none():
    public = FakeClient(entry("weather"), web_host="https://public.example")
    org = FakeClient(entry("payroll"), web_host="https://org.example")
    index = await FederatedRegistryClient(public, [(ORG, org)]).fetch_index()
    assert index.web_host == "https://public.example"

    index2 = await FederatedRegistryClient(FakeClient(), [(ORG, org)]).fetch_index()
    assert index2.web_host == "https://org.example"


# ────────────────────────────── failure isolation ──────────────────────────────


@pytest.mark.asyncio
async def test_an_org_that_never_published_leaves_the_marketplace_intact():
    """The COMMON case, not a fault: an org with no index yet 404s, and the store must still
    render every public agent."""
    public = FakeClient(entry("weather"))
    index = await FederatedRegistryClient(public, [(ORG, FakeClient(boom="404"))]).fetch_index()
    assert [b.id for b in index.bundles] == ["weather"]


@pytest.mark.asyncio
async def test_one_broken_shelf_does_not_hide_another_orgs_agents():
    good = FakeClient(entry("payroll"))
    federated = FederatedRegistryClient(
        FakeClient(entry("weather")),
        [("org_broken", FakeClient(boom="bad roster")), ("org_good", good)],
    )
    index = await federated.fetch_index()
    assert [b.id for b in index.bundles] == ["payroll", "weather"]


@pytest.mark.asyncio
async def test_a_broken_public_registry_is_still_a_real_failure():
    """The inverse of the rule above. An unreadable MARKETPLACE is not something to paper over:
    silently serving org-only results would read as "the marketplace is empty today"."""
    federated = FederatedRegistryClient(FakeClient(boom="down"), [(ORG, FakeClient(entry("p")))])
    with pytest.raises(RuntimeError, match="down"):
        await federated.fetch_index()


# ────────────────────────────── download routing ──────────────────────────────


@pytest.mark.asyncio
async def test_a_download_goes_back_to_the_shelf_that_listed_it(tmp_path):
    public, org = FakeClient(entry("weather")), FakeClient(entry("payroll"))
    federated = FederatedRegistryClient(public, [(ORG, org)])
    index = await federated.fetch_index()

    by_id = {b.id: b for b in index.bundles}
    await federated.download(by_id["payroll"], tmp_path)
    await federated.download(by_id["weather"], tmp_path)
    assert org.downloaded == ["payroll"]
    assert public.downloaded == ["weather"]


@pytest.mark.asyncio
async def test_a_shadowed_id_downloads_from_the_org_not_the_marketplace(tmp_path):
    """The shadow has to hold all the way through the install, or a member would see their
    company's version and download the public one."""
    public, org = FakeClient(entry("weather", "1.0.0")), FakeClient(entry("weather", "9.9.9"))
    federated = FederatedRegistryClient(public, [(ORG, org)])
    index = await federated.fetch_index()
    await federated.download(index.bundles[0], tmp_path)
    assert org.downloaded == ["weather"]
    assert public.downloaded == []


@pytest.mark.asyncio
async def test_an_unknown_id_falls_back_to_the_marketplace(tmp_path):
    public = FakeClient(entry("weather"))
    federated = FederatedRegistryClient(public, [(ORG, FakeClient())])
    await federated.fetch_index()
    await federated.download(entry("assembled-by-hand"), tmp_path)
    assert public.downloaded == ["assembled-by-hand"]


@pytest.mark.asyncio
async def test_with_no_public_registry_an_unknown_id_says_so(tmp_path):
    federated = FederatedRegistryClient(None, [(ORG, FakeClient())])
    await federated.fetch_index()
    with pytest.raises(LookupError, match="nowhere-agent"):
        await federated.download(entry("nowhere-agent"), tmp_path)
