"""BuildProductService + the payload writer + the engine catalogues.

The service is tested against FAKES, which is the point of the ports: none of these tests zip
anything, run makensis, or touch a network. The one test that does real filesystem work is the
payload writer, because writing files IS its job.
"""

from __future__ import annotations

import json
import tomllib

import pytest

from agent_runtime.application.interfaces.product import (
    PayloadManifest,
    ProductSource,
)
from agent_runtime.application.services.build_product_service import BuildProductService
from agent_runtime.distribution import parse_profile
from agent_runtime.domain.product import (
    EngineRef,
    PlatformEndpoints,
    ProductDefaults,
    ProductError,
    ProductRules,
)
from agent_runtime.infrastructure.products.engine_catalog import (
    ChainEngineCatalog,
    ConfigEngineCatalog,
    IndexEngineCatalog,
    StaticEngineCatalog,
)
from agent_runtime.infrastructure.products.payload_writer import FsPayloadWriter

GOOD_ENGINE = EngineRef("win", version="0.2.0", url="https://cdn/engine-0.2.0.exe", sha256="ab" * 32)


# ────────────────────────────── fakes ──────────────────────────────


class FakeReader:
    def __init__(self, agent_id="weather", toml=None, icons=None):
        self._agent_id = agent_id
        self._toml = toml if toml is not None else {"version": "2.3.0", "app": {}}
        self._icons = icons or {}

    def declaration(self, source):
        return self._agent_id, self._toml

    def icon_bytes(self, source, relative):
        return self._icons.get(relative)


class FakePacker:
    def __init__(self):
        self.calls = []

    def pack(self, agent_dir, out_dir, version=""):
        self.calls.append((agent_dir, out_dir, version))
        target = out_dir / "weather-2.3.0.agentpkg"
        target.write_bytes(b"PK-not-really")
        return target


class FakeWriter:
    def __init__(self):
        self.calls = []

    def write(self, spec, source, out_dir):
        self.calls.append(spec)
        return PayloadManifest(dir=out_dir, package="weather-2.3.0.agentpkg", files=("x",))


class FakeStubBuilder:
    platform = "win"
    suffix = ".exe"

    def __init__(self, reason=""):
        self._reason = reason
        self.built = []

    def available(self):
        return self._reason

    def build(self, spec, payload, engine, out_path):
        self.built.append((spec, engine, out_path))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_bytes(b"MZ")
        return out_path


def service(stub=None, catalog=None, reader=None, writer=None, defaults=None):
    return BuildProductService(
        rules=ProductRules(defaults or ProductDefaults()),
        reader=reader or FakeReader(),
        payload_writer=writer or FakeWriter(),
        stub_builder=stub,
        engine_catalog=catalog,
    )


def source(tmp_path):
    agent_dir = tmp_path / "agents" / "weather"
    agent_dir.mkdir(parents=True)
    return ProductSource(agent_dir=agent_dir)


# ────────────────────────────── ProductSource ──────────────────────────────


def test_source_needs_exactly_one_shape(tmp_path):
    with pytest.raises(ValueError, match="exactly one"):
        ProductSource()
    with pytest.raises(ValueError, match="exactly one"):
        ProductSource(agent_dir=tmp_path, package=tmp_path / "x.agentpkg")


# ────────────────────────────── refuse before writing ──────────────────────────────


def test_derivation_refuses_before_anything_is_written(tmp_path):
    """A refusal after the payload dir exists leaves half a product that looks buildable."""
    writer = FakeWriter()
    svc = service(writer=writer, reader=FakeReader(toml={"name": "Headless", "version": "1.0"}))
    with pytest.raises(ProductError):
        svc.build(source(tmp_path), tmp_path / "payload")
    assert writer.calls == []


# ────────────────────────────── payload without a stub is a SUCCESS ──────────────────────


def test_no_stub_builder_warns_and_still_returns_a_payload(tmp_path):
    build = service(stub=None).build(source(tmp_path), tmp_path / "payload")
    assert build.stub is None and not build.complete
    assert build.payload.package == "weather-2.3.0.agentpkg"
    assert any("no stub builder" in w for w in build.warnings)


def test_unavailable_toolchain_warns_with_the_reason(tmp_path):
    stub = FakeStubBuilder(reason="makensis is not on PATH")
    build = service(stub=stub, catalog=StaticEngineCatalog(GOOD_ENGINE)).build(
        source(tmp_path), tmp_path / "payload"
    )
    assert stub.built == []
    assert any("makensis is not on PATH" in w for w in build.warnings)


# ────────────────────────────── a stub is never built blind ───────────────────────────────


def test_no_engine_means_no_stub_and_an_actionable_message(tmp_path):
    stub = FakeStubBuilder()
    build = service(stub=stub, catalog=StaticEngineCatalog()).build(
        source(tmp_path), tmp_path / "payload"
    )
    assert stub.built == []
    assert any("engine_installer_url" in w for w in build.warnings)


def test_an_engine_with_no_digest_is_refused(tmp_path):
    """A stub downloads and RUNS a 250 MB executable. No digest, no installer — ever."""
    half = EngineRef("win", url="https://cdn/engine.exe")  # no sha256
    stub = FakeStubBuilder()
    build = service(stub=stub, catalog=StaticEngineCatalog(half)).build(
        source(tmp_path), tmp_path / "payload"
    )
    assert stub.built == []
    assert any("sha256=missing" in w for w in build.warnings)


def test_a_good_engine_produces_an_installer_named_by_convention(tmp_path):
    stub = FakeStubBuilder()
    build = service(stub=stub, catalog=StaticEngineCatalog(GOOD_ENGINE)).build(
        source(tmp_path), tmp_path / "payload", tmp_path / "out"
    )
    assert build.complete
    assert build.stub.name == "weather-2.3.0-setup.exe"
    assert build.engine == GOOD_ENGINE
    assert build.warnings == []


def test_the_stub_dir_defaults_beside_the_payload(tmp_path):
    build = service(stub=FakeStubBuilder(), catalog=StaticEngineCatalog(GOOD_ENGINE)).build(
        source(tmp_path), tmp_path / "products" / "payload"
    )
    assert build.stub.parent == tmp_path / "products"


# ────────────────────────────── FsPayloadWriter ──────────────────────────────


def test_payload_writer_produces_a_readable_document_and_the_bundle(tmp_path):
    reader = FakeReader(icons={"brand/logo.ico": b"ICON-BYTES"})
    spec = ProductRules(
        ProductDefaults(platform=PlatformEndpoints("https://accounts.x", "https://models.x"))
    ).derive("weather", {"version": "2.3.0", "app": {"icon": "brand/logo.ico"}})
    writer = FsPayloadWriter(FakePacker(), reader)

    payload = writer.write(spec, source(tmp_path), tmp_path / "payload")

    assert payload.package == "weather-2.3.0.agentpkg"
    assert set(payload.files) == {
        "bundles/weather-2.3.0.agentpkg",
        "distribution.toml",
        "icon.ico",
    }
    # The icon is NORMALISED: declared at brand/logo.ico, shipped as icon.ico, so the engine and
    # the stub both know where it is without parsing the toml to find out.
    assert (payload.dir / "icon.ico").read_bytes() == b"ICON-BYTES"
    profile = parse_profile(tomllib.loads((payload.dir / "distribution.toml").read_text()))
    assert profile.app_agent == "weather"
    assert profile.icon == "icon.ico"
    assert profile.accounts_url == "https://accounts.x"


def test_payload_writer_omits_the_icon_when_the_agent_has_none(tmp_path):
    spec = ProductRules().derive("weather", {"version": "2.3.0", "app": {}})
    payload = FsPayloadWriter(FakePacker(), FakeReader()).write(
        spec, source(tmp_path), tmp_path / "payload"
    )
    assert payload.icon == ""
    assert not (payload.dir / "icon.ico").exists()
    assert parse_profile(tomllib.loads((payload.dir / "distribution.toml").read_text())).icon == ""


def test_payload_writer_clears_stale_output(tmp_path):
    """Two .agentpkg files in one payload and the engine installs whichever it finds first."""
    out = tmp_path / "payload"
    (out / "bundles").mkdir(parents=True)
    (out / "bundles" / "weather-1.0.0.agentpkg").write_bytes(b"OLD")
    (out / "leftover.txt").write_text("stale")

    spec = ProductRules().derive("weather", {"version": "2.3.0", "app": {}})
    payload = FsPayloadWriter(FakePacker(), FakeReader()).write(spec, source(tmp_path), out)

    assert not (out / "leftover.txt").exists()
    assert not (out / "bundles" / "weather-1.0.0.agentpkg").exists()
    assert "bundles/weather-2.3.0.agentpkg" in payload.files


def test_payload_writer_copies_a_prebuilt_package_instead_of_packing(tmp_path):
    package = tmp_path / "weather-9.9.9.agentpkg"
    package.write_bytes(b"PREBUILT")
    packer = FakePacker()
    spec = ProductRules().derive("weather", {"version": "9.9.9", "app": {}})

    payload = FsPayloadWriter(packer, FakeReader()).write(
        spec, ProductSource(package=package), tmp_path / "payload"
    )

    assert packer.calls == []  # nothing was packed — intake copies
    assert (payload.dir / "bundles" / "weather-9.9.9.agentpkg").read_bytes() == b"PREBUILT"


def test_a_copied_package_always_lands_with_a_agentpkg_suffix(tmp_path):
    """The engine finds a payload's agent by globbing `bundles/*.agentpkg`. A source file called
    anything else is INVISIBLE to it, and the failure is silent and total: the product installs,
    the window opens, and there is no agent in it. That shipped once — the publish service named
    the upload after the wrong multipart part and copied through `weather-1.0.0-setup.exe`."""
    package = tmp_path / "weather-1.0.0-setup.exe"
    package.write_bytes(b"PREBUILT")
    spec = ProductRules().derive("weather", {"version": "1.0.0", "app": {}})

    payload = FsPayloadWriter(FakePacker(), FakeReader()).write(
        spec, ProductSource(package=package), tmp_path / "payload"
    )

    assert payload.package.endswith(".agentpkg")
    assert [f for f in payload.files if f.startswith("bundles/")] == [
        "bundles/weather-1.0.0-setup.exe.agentpkg"
    ]


# ────────────────────────────── engine catalogues ──────────────────────────────


class FakeConfig:
    def __init__(self, **kw):
        self.engine_installer_url = kw.get("url", "")
        self.engine_installer_sha256 = kw.get("sha256", "")
        self.engine_installer_platform = kw.get("platform", "win")
        self.engine_version = kw.get("version", "")


def test_config_catalog_answers_only_for_its_declared_platform():
    catalog = ConfigEngineCatalog(FakeConfig(url="https://c/e.exe", sha256="AB" * 32, version="1.0"))
    ref = catalog.resolve("win")
    assert ref and ref.usable and ref.sha256 == "ab" * 32  # normalised to lowercase hex
    assert catalog.resolve("mac") is None  # a win url must never be offered to a mac build


def test_config_catalog_is_silent_when_unset():
    assert ConfigEngineCatalog(FakeConfig()).resolve("win") is None


def test_index_catalog_reads_the_engine_block_and_absolutises_relative_urls():
    index = {
        "schema": 2,
        "bundles": [],
        "publishers": {},
        "engine": [
            {"platform": "win", "version": "0.2.0", "url": "agentd-0.2.0-setup.exe", "sha256": "cd" * 32}
        ],
    }
    catalog = IndexEngineCatalog(
        "https://cdn.example/registry/index.json", opener=lambda _: json.dumps(index)
    )
    ref = catalog.resolve("win")
    assert ref.url == "https://cdn.example/registry/agentd-0.2.0-setup.exe"
    assert ref.version == "0.2.0" and ref.usable


def test_index_catalog_is_quiet_when_the_registry_is_unreachable():
    """A slow CDN must not fail a build — the payload is still worth producing."""

    def boom(_):
        raise OSError("connection reset")

    assert IndexEngineCatalog("https://cdn/index.json", opener=boom).resolve("win") is None


def test_index_catalog_survives_junk():
    assert IndexEngineCatalog("https://x/i.json", opener=lambda _: "not json").resolve("win") is None
    empty = json.dumps({"schema": 1, "bundles": [], "engine": "nonsense"})
    assert IndexEngineCatalog("https://x/i.json", opener=lambda _: empty).resolve("win") is None


def test_index_catalog_needs_no_registry():
    assert IndexEngineCatalog("").resolve("win") is None


def test_chain_prefers_config_over_the_registry():
    from_config = FakeConfig(url="https://override/e.exe", sha256="11" * 32)
    index = json.dumps(
        {"schema": 1, "bundles": [], "engine": [{"platform": "win", "url": "https://cdn/e.exe", "sha256": "22" * 32}]}
    )
    chain = ChainEngineCatalog(
        ConfigEngineCatalog(from_config),
        IndexEngineCatalog("https://cdn/index.json", opener=lambda _: index),
    )
    assert chain.resolve("win").url == "https://override/e.exe"


def test_chain_falls_through_to_the_registry():
    index = json.dumps(
        {"schema": 1, "bundles": [], "engine": [{"platform": "win", "url": "https://cdn/e.exe", "sha256": "22" * 32}]}
    )
    chain = ChainEngineCatalog(
        ConfigEngineCatalog(FakeConfig()),
        IndexEngineCatalog("https://cdn/index.json", opener=lambda _: index),
    )
    assert chain.resolve("win").url == "https://cdn/e.exe"


def test_chain_reports_a_half_configured_engine_rather_than_none():
    """"url set, digest missing" and "no engine at all" need different messages."""
    chain = ChainEngineCatalog(ConfigEngineCatalog(FakeConfig(url="https://c/e.exe")))
    ref = chain.resolve("win")
    assert ref is not None and not ref.usable
