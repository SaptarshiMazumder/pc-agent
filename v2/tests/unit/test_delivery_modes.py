"""Delivery modes — HOW a published agent reaches people, chosen by its author.

Three doors, of which a bundle always has the first:

    install into agentd     what a bundle IS — never declared, never optional
    exe                     a standalone per-agent installer, built at publish (default ON,
                            because it was the only behaviour before this field existed)
    web                     the hosted platform serves the app at /apps/<id>/ and the store
                            shows Open-in-browser (default OFF: running on the platform's
                            infrastructure must be an opt-in, never an inference)

The properties pinned here:

  * the field parses tolerantly everywhere (it postdates shipped clients)
  * a bundle with no [delivery] keeps meaning exactly what it always meant
  * web delivery REQUIRES [app] — refused at pack time, where the author is looking
  * the catalog hands the client a FINISHED Open link (`webUrl`) or nothing; the hosted
    deployment's address is registry knowledge (index-level `web.host`), never the client's
  * `sync_web_app` is the ONLY door a visitor's URL opens, and it re-checks the author's
    opt-in — a URL guess must not conscript the host into running arbitrary bundles
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.services.marketplace_service import MarketplaceService
from agent_runtime.domain.bundle import (
    BundleError,
    DeliveryModes,
    InstalledBundle,
    RegistryEntry,
    RegistryIndex,
    parse_bundle_manifest,
    parse_registry_index,
)
from agent_runtime.domain.catalog import build_catalog
from agent_runtime.infrastructure.marketplace import bundle_io
from agent_runtime.infrastructure.marketplace.packer import pack_agent_dir

# ────────────────────────────── domain: parsing ──────────────────────────────


def test_manifest_delivery_parses_from_the_bundle_table():
    manifest = parse_bundle_manifest(
        {
            "bundle": {
                "id": "bedtime-kids",
                "name": "Bedtime Kids",
                "version": "1.0.0",
                "delivery": {"web": True, "exe": False},
            }
        }
    )
    assert manifest.delivery == DeliveryModes(web=True, exe=False)


def test_a_manifest_without_delivery_keeps_the_pre_field_meaning():
    manifest = parse_bundle_manifest(
        {"bundle": {"id": "weather", "name": "W", "version": "1.0.0"}}
    )
    assert manifest.delivery == DeliveryModes(web=False, exe=True)


@pytest.mark.parametrize("junk", ["not a dict", 7, ["web"], None])
def test_junk_delivery_degrades_to_the_defaults_not_an_error(junk):
    """Same contract as `installers`: this key postdates shipped clients, so a registry that
    upsets the parser would blank the store on every older install."""
    index = parse_registry_index(
        {
            "schema": 1,
            "bundles": [
                {"id": "gm", "name": "GM", "version": "1", "url": "a.agentpkg", "delivery": junk}
            ],
        }
    )
    assert index.bundles[0].delivery == DeliveryModes()


def test_registry_row_delivery_round_trips():
    index = parse_registry_index(
        {
            "schema": 1,
            "bundles": [
                {
                    "id": "bedtime-kids",
                    "name": "BK",
                    "version": "1",
                    "url": "a.agentpkg",
                    "delivery": {"web": True, "exe": False},
                }
            ],
        }
    )
    assert index.bundles[0].delivery == DeliveryModes(web=True, exe=False)


def test_web_host_is_index_level_knowledge_and_parses_tolerantly():
    assert parse_registry_index({"schema": 1, "web": {"host": "https://run.example"}}).web_host == "https://run.example"
    assert parse_registry_index({"schema": 1}).web_host == ""
    assert parse_registry_index({"schema": 1, "web": "not a dict"}).web_host == ""


# ────────────────────────────── packing ──────────────────────────────


def _agent(tmp_path: Path, agent_toml: str) -> Path:
    agent = tmp_path / "src" / "bedtime-kids"
    agent.mkdir(parents=True)
    (agent / "agent.toml").write_text(agent_toml, encoding="utf-8")
    return agent


APP_AGENT = 'name = "Bedtime Kids"\nversion = "1.0.0"\n\n[app]\ntitle = "Bedtime Kids"\n'


def test_delivery_in_agent_toml_survives_into_the_packed_manifest(tmp_path):
    agent = _agent(tmp_path, APP_AGENT + "\n[delivery]\nweb = true\nexe = false\n")
    pkg = pack_agent_dir(agent, tmp_path / "out")
    assert bundle_io.read_manifest(pkg).delivery == DeliveryModes(web=True, exe=False)


def test_bundle_toml_overrides_agent_toml_as_a_whole_table(tmp_path):
    """The publisher-facing file states the modes; inheriting an unmentioned key from
    agent.toml would make that statement mean different things for different agents."""
    agent = _agent(tmp_path, APP_AGENT + "\n[delivery]\nweb = true\nexe = false\n")
    (agent / "bundle.toml").write_text(
        '[bundle]\nid = "bedtime-kids"\nversion = "1.0.0"\n\n[bundle.delivery]\nweb = true\n',
        encoding="utf-8",
    )
    manifest = bundle_io.read_manifest(pack_agent_dir(agent, tmp_path / "out"))
    # exe was NOT mentioned in bundle.toml -> the table default (True), not agent.toml's False
    assert manifest.delivery == DeliveryModes(web=True, exe=True)


def test_web_delivery_without_an_app_table_is_refused_at_pack_time(tmp_path):
    agent = _agent(
        tmp_path, 'name = "BK"\nversion = "1.0.0"\n\n[delivery]\nweb = true\n'
    )
    with pytest.raises(ValueError, match=r"\[app\]"):
        pack_agent_dir(agent, tmp_path / "out")


def test_packing_without_delivery_stays_byte_compatible_in_meaning(tmp_path):
    agent = _agent(tmp_path, APP_AGENT)
    assert bundle_io.read_manifest(pack_agent_dir(agent, tmp_path / "out")).delivery == DeliveryModes()


def test_icon_survives_packing(tmp_path):
    """Regression: _manifest_toml never serialized `icon`, so a store glyph chosen by the
    author was silently dropped between pack and publish."""
    agent = _agent(tmp_path, APP_AGENT)
    (agent / "bundle.toml").write_text(
        '[bundle]\nid = "bedtime-kids"\nversion = "1.0.0"\nicon = "sparkles"\n', encoding="utf-8"
    )
    assert bundle_io.read_manifest(pack_agent_dir(agent, tmp_path / "out")).icon == "sparkles"


# ────────────────────────────── the catalog row ──────────────────────────────


def _entry(**kw) -> RegistryEntry:
    base = {"id": "bedtime-kids", "name": "BK", "version": "1.0.0"}
    base.update(kw)
    return RegistryEntry(**base)


def _entry_dict(entry, web_host=""):
    """The one catalog row for `entry` — the store view a client renders (domain/catalog.py)."""
    index = RegistryIndex(bundles=(entry,), web_host=web_host)
    return build_catalog(index)["bundles"][0]


def test_the_client_receives_a_finished_open_link_or_nothing():
    web = _entry(delivery=DeliveryModes(web=True))
    assert _entry_dict(web, web_host="https://run.example/")["webUrl"] == (
        "https://run.example/apps/bedtime-kids/"
    )
    # no hosted deployment known -> no link, even for a web-delivered bundle
    assert "webUrl" not in _entry_dict(web)
    # author never opted in -> no link, even with a host
    assert "webUrl" not in _entry_dict(_entry(), web_host="https://run.example")


def test_every_catalog_row_states_its_delivery():
    row = _entry_dict(_entry(delivery=DeliveryModes(web=True, exe=False)))
    assert row["delivery"] == {"web": True, "exe": False}


# ────────────────────────────── sync_web_app (the visitor's door) ──────────────────────────────


class FakeRegistry:
    def __init__(self, *entries, web_host=""):
        self._index = RegistryIndex(bundles=tuple(entries), web_host=web_host)

    async def fetch_index(self):
        return self._index


class FakeStore:
    def __init__(self, *bundles):
        self.rows = {b.id: b for b in bundles}

    def get(self, bundle_id):
        return self.rows.get(bundle_id)

    def list(self):
        return list(self.rows.values())

    def record(self, bundle):
        self.rows[bundle.id] = bundle

    def remove(self, bundle_id):
        self.rows.pop(bundle_id, None)


def _service(registry, store, tmp_path) -> MarketplaceService:
    service = MarketplaceService(
        registry_client=registry,
        installer=None,  # type: ignore[arg-type] — every test path stops before install IO
        installed_store=store,
        agentd_version="1.0.0",
        download_dir=tmp_path / "downloads",
    )
    installs: list[str] = []

    async def fake_install(bundle_id="", file=""):
        installs.append(bundle_id)
        return {"installed": True, "id": bundle_id}

    service.install = fake_install  # type: ignore[method-assign]
    service.installs = installs  # type: ignore[attr-defined]
    return service


@pytest.mark.asyncio
async def test_a_missing_web_bundle_is_installed(tmp_path):
    entry = _entry(delivery=DeliveryModes(web=True))
    service = _service(FakeRegistry(entry), FakeStore(), tmp_path)
    await service.sync_web_app("bedtime-kids")
    assert service.installs == ["bedtime-kids"]


@pytest.mark.asyncio
async def test_a_current_install_is_a_no_op(tmp_path):
    entry = _entry(delivery=DeliveryModes(web=True))
    have = InstalledBundle(id="bedtime-kids", version="1.0.0")
    service = _service(FakeRegistry(entry), FakeStore(have), tmp_path)
    result = await service.sync_web_app("bedtime-kids")
    assert result["current"] is True
    assert service.installs == []


@pytest.mark.asyncio
async def test_a_stale_install_is_updated(tmp_path):
    entry = _entry(version="2.0.0", delivery=DeliveryModes(web=True))
    have = InstalledBundle(id="bedtime-kids", version="1.0.0")
    service = _service(FakeRegistry(entry), FakeStore(have), tmp_path)
    await service.sync_web_app("bedtime-kids")
    assert service.installs == ["bedtime-kids"]


@pytest.mark.asyncio
async def test_a_bundle_that_never_opted_into_web_is_refused(tmp_path):
    """THE guard. /apps/<id> is reachable by anyone with a URL bar; without this check a
    visitor could make the host install and run any published bundle."""
    service = _service(FakeRegistry(_entry()), FakeStore(), tmp_path)
    with pytest.raises(BundleError, match="does not offer web delivery"):
        await service.sync_web_app("bedtime-kids")
    assert service.installs == []


@pytest.mark.asyncio
async def test_an_unknown_id_is_refused_with_a_plain_answer(tmp_path):
    service = _service(FakeRegistry(), FakeStore(), tmp_path)
    with pytest.raises(BundleError, match="not in the registry"):
        await service.sync_web_app("nope")


@pytest.mark.asyncio
async def test_a_web_sync_is_stamped_hosted_not_endorsed(tmp_path):
    """HOSTED ≠ ENDORSED. The plain install stamps a no-account platform install `curated` —
    right for the boot seed, wrong here: this copy exists because an AUTHOR published web=true,
    and `curated` would put every web publish in every user's sidebar. The sync re-stamps it
    `web-app`: url-reachable, Store-listed, auto-listed for nobody."""
    from agent_runtime.infrastructure.agents import ownership_store

    entry = _entry(delivery=DeliveryModes(web=True))
    service = _service(FakeRegistry(entry), FakeStore(), tmp_path)
    (tmp_path / "bedtime-kids").mkdir()
    service._installer = type("I", (), {"agents_dir": tmp_path})()

    await service.sync_web_app("bedtime-kids")

    record = ownership_store.read(tmp_path / "bedtime-kids")
    assert (record.owner, record.origin) == ("platform", "web-app")
    assert record.source_id == "bedtime-kids"


# ────────────────────────────── the gateway's front door ──────────────────────────────


class FakeMarket:
    """Just enough marketplace for the gateway: sync_web_app records or raises."""

    def __init__(self, error: str = ""):
        self.synced: list[str] = []
        self._error = error

    def has(self, bundle_id):
        return False

    async def sync_web_app(self, bundle_id):
        if self._error:
            raise BundleError(self._error)
        self.synced.append(bundle_id)
        return {"installed": True, "id": bundle_id}


def _gateway(tmp_path, hosted: bool, market: FakeMarket):
    from types import SimpleNamespace

    from agent_runtime.presentation.gateway import Gateway

    def missing(agent_id):
        raise KeyError(agent_id)

    gw = Gateway(
        config=SimpleNamespace(state_dir=tmp_path, agent_id="main", hosted=hosted),
        service=None,
        registry=SimpleNamespace(list_ids=lambda: [], get=missing),
    )
    gw.marketplace = market  # pre-built: _marketplace() must never compose a real one here
    return gw


@pytest.mark.asyncio
async def test_desktop_keeps_its_plain_404(tmp_path):
    """The whole feature is hosted-only. A desktop /apps miss behaves exactly as before."""
    from urllib.parse import urlsplit

    gw = _gateway(tmp_path, hosted=False, market=FakeMarket())
    response = await gw._serve_app(urlsplit("/apps/ghost/"))
    assert response.status_code == 404
    assert gw.web_app_syncs == {}


@pytest.mark.asyncio
async def test_first_open_schedules_the_install_and_serves_a_holding_page(tmp_path):
    from urllib.parse import urlsplit

    market = FakeMarket()
    gw = _gateway(tmp_path, hosted=True, market=market)
    response = await gw._serve_app(urlsplit("/apps/bedtime-kids/"))

    assert response.status_code == 200
    assert b"refresh" in response.body  # the page polls itself until the install lands
    await gw.web_app_syncs["bedtime-kids"]["task"]
    assert market.synced == ["bedtime-kids"]
    assert gw.web_app_syncs["bedtime-kids"]["error"] == ""


@pytest.mark.asyncio
async def test_a_failed_sync_answers_with_its_error_and_does_not_retry_per_request(tmp_path):
    """The error message IS the page (a visitor deserves the reason), and it stands for a
    while — each retry is a registry fetch, and this path belongs to anyone with a URL bar."""
    from urllib.parse import urlsplit

    market = FakeMarket(error="'bedtime-kids' does not offer web delivery")
    gw = _gateway(tmp_path, hosted=True, market=market)
    await gw._serve_app(urlsplit("/apps/bedtime-kids/"))
    await gw.web_app_syncs["bedtime-kids"]["task"]

    response = await gw._serve_app(urlsplit("/apps/bedtime-kids/"))
    assert response.status_code == 404
    assert b"does not offer web delivery" in response.body
    # the failure did not queue another sync
    assert gw.web_app_syncs["bedtime-kids"]["task"] is None
