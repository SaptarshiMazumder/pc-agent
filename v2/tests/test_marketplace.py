"""M4 end-to-end (in-process): pack an agent -> build a registry index -> catalog ->
install (files land, ledger written, hot-reload hook fires) -> uninstall (workspace
survives, shared plugins survive). Plus the safety rails: zip-slip, sha mismatch,
signature enforcement, compat refusal."""

import asyncio
import json
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.bundle import BundleError, BundleManifest, PluginDep
from agentd.infrastructure.marketplace import bundle_io
from agentd.infrastructure.marketplace.factory import build_marketplace_service
from agentd.infrastructure.marketplace.index_builder import build_index
from agentd.infrastructure import signing


def _make_agent_dir(root: Path, agent_id: str = "demo-agent") -> Path:
    agent = root / "src" / agent_id
    (agent / "skills" / "hello").mkdir(parents=True)
    (agent / "workspace").mkdir()
    (agent / "agent.toml").write_text('name = "Demo Agent"\n', encoding="utf-8")
    (agent / "IDENTITY.md").write_text("# Demo\nYou are Demo.\n", encoding="utf-8")
    (agent / "skills" / "hello" / "SKILL.md").write_text("# hello\n", encoding="utf-8")
    (agent / "workspace" / "junk.txt").write_text("never packed", encoding="utf-8")
    return agent


def _make_plugin_dir(root: Path, plugin_id: str = "demo-plugin") -> Path:
    plugin = root / "srcplugins" / plugin_id
    plugin.mkdir(parents=True)
    (plugin / "plugin.toml").write_text(
        f'id = "{plugin_id}"\nname = "Demo Plugin"\nkind = "native"\nentry = "x:y"\n',
        encoding="utf-8")
    (plugin / "tool.py").write_text("# tool code\n", encoding="utf-8")
    return plugin


def _pack(tmp: Path, agent_id: str = "demo-agent", version: str = "1.0.0",
          with_plugin: bool = True, **manifest_kwargs) -> Path:
    agent_dir = _make_agent_dir(tmp / version, agent_id)
    plugins = {}
    deps = []
    if with_plugin:
        plugin_dir = _make_plugin_dir(tmp / version)
        plugins["demo-plugin"] = plugin_dir
        deps.append(PluginDep(id="demo-plugin", source="vendored"))
    manifest = BundleManifest(id=agent_id, name="Demo Agent", version=version,
                              plugins=tuple(deps), **manifest_kwargs)
    return bundle_io.pack_bundle(agent_dir, tmp / "dist", manifest, plugins)


def _config(tmp: Path, registry_url: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        state_dir=tmp / "state", agents_dir=tmp / "agents", plugins_dir=tmp / "plugins",
        builtin_plugins_dir="", registry_url=registry_url, distribution=None)


def _service(tmp: Path, registry_url: str = "", **kwargs):
    return build_marketplace_service(_config(tmp, registry_url), **kwargs)


# ---------------------------------------------------------------- pack + zip safety


def test_pack_excludes_workspace_and_writes_manifest(tmp_path):
    package = _pack(tmp_path)
    with zipfile.ZipFile(package) as zf:
        names = zf.namelist()
    assert "bundle.toml" in names
    assert "agent/IDENTITY.md" in names
    assert "plugins/demo-plugin/plugin.toml" in names
    assert not any("workspace" in n for n in names), "user workspace must never ship"
    manifest = bundle_io.read_manifest(package)
    assert manifest.id == "demo-agent" and manifest.plugins[0].id == "demo-plugin"


def test_unpack_rejects_zip_slip(tmp_path):
    evil = tmp_path / "evil.agentpkg"
    with zipfile.ZipFile(evil, "w") as zf:
        zf.writestr("bundle.toml", '[bundle]\nid = "evil"\nname = "e"\nversion = "1.0.0"\n')
        zf.writestr("agent/../../escape.txt", "boo")
    manifest = bundle_io.read_manifest(evil)
    with pytest.raises(BundleError, match="unsafe path"):
        bundle_io.unpack_bundle(evil, manifest, tmp_path / "agents", tmp_path / "plugins")
    assert not (tmp_path / "escape.txt").exists()


# ---------------------------------------------------------------- the full round trip


def test_install_from_local_registry_and_uninstall(tmp_path):
    package = _pack(tmp_path)
    build_index(package.parent, name="local-test")
    events = []
    reloads = []
    service = _service(tmp_path, registry_url=str(package.parent),
                       on_event=events.append, after_change=lambda changed: reloads.append(changed) or {"ok": 1})

    catalog = asyncio.run(service.catalog())
    assert catalog["bundles"][0]["id"] == "demo-agent"
    assert catalog["bundles"][0]["installed"] is False

    result = asyncio.run(service.install(bundle_id="demo-agent"))
    assert result["installed"] and result["plugins"] == ["demo-plugin"]
    assert (tmp_path / "agents" / "demo-agent" / "IDENTITY.md").is_file()
    assert (tmp_path / "plugins" / "demo-plugin" / "plugin.toml").is_file()
    assert reloads, "after_change (hot-reload) must fire on install"
    assert any(e["step"] == "installed" for e in events)

    ledger = json.loads((tmp_path / "state" / "installed_bundles.json").read_text())
    assert ledger["bundles"][0]["id"] == "demo-agent"
    assert (asyncio.run(service.catalog()))["bundles"][0]["installed"] is True

    # user puts work into the workspace; a plain uninstall must keep it
    workspace = tmp_path / "agents" / "demo-agent" / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "my-figure.svg").write_text("<svg/>", encoding="utf-8")
    asyncio.run(service.uninstall("demo-agent"))
    assert not (tmp_path / "agents" / "demo-agent" / "IDENTITY.md").exists()
    assert (workspace / "my-figure.svg").is_file(), "plain uninstall keeps user files"
    assert not (tmp_path / "plugins" / "demo-plugin").exists()
    assert service.installed()["bundles"] == []


def test_update_replaces_definition_keeps_workspace(tmp_path):
    v1 = _pack(tmp_path, version="1.0.0")
    service = _service(tmp_path)
    asyncio.run(service.install(file=str(v1)))
    workspace = tmp_path / "agents" / "demo-agent" / "workspace"
    workspace.mkdir(exist_ok=True)
    (workspace / "keep.txt").write_text("keep me", encoding="utf-8")

    v2 = _pack(tmp_path, version="2.0.0")
    result = asyncio.run(service.install(file=str(v2)))
    assert result["version"] == "2.0.0"
    assert (workspace / "keep.txt").is_file(), "update must not clobber the workspace"
    assert service.installed()["bundles"][0]["version"] == "2.0.0"


def test_shared_vendored_plugin_survives_sibling_uninstall(tmp_path):
    a = _pack(tmp_path, agent_id="agent-a")
    b = _pack(tmp_path, agent_id="agent-b", version="1.0.1")
    service = _service(tmp_path)
    asyncio.run(service.install(file=str(a)))
    asyncio.run(service.install(file=str(b)))
    asyncio.run(service.uninstall("agent-a"))
    assert (tmp_path / "plugins" / "demo-plugin" / "plugin.toml").is_file(), \
        "agent-b still needs demo-plugin"
    asyncio.run(service.uninstall("agent-b"))
    assert not (tmp_path / "plugins" / "demo-plugin").exists()


# ---------------------------------------------------------------- safety rails


def test_compat_refusal(tmp_path):
    package = _pack(tmp_path, agentd_compat=">=99")
    service = _service(tmp_path)
    with pytest.raises(BundleError, match="update agentd"):
        asyncio.run(service.install(file=str(package)))


def test_sha256_mismatch_refused(tmp_path):
    package = _pack(tmp_path)
    index_path = build_index(package.parent)
    index = json.loads(index_path.read_text())
    index["bundles"][0]["sha256"] = "0" * 64
    index_path.write_text(json.dumps(index), encoding="utf-8")
    service = _service(tmp_path, registry_url=str(package.parent))
    with pytest.raises(BundleError, match="sha256 mismatch"):
        asyncio.run(service.install(bundle_id="demo-agent"))


def test_pinned_key_requires_valid_signature(tmp_path):
    private_b64, public_b64 = signing.generate_keypair()
    package = _pack(tmp_path)
    build_index(package.parent, private_key_b64=private_b64, public_key_b64=public_b64)

    config = _config(tmp_path, registry_url=str(package.parent))
    config.distribution = SimpleNamespace(publisher_key=public_b64)
    service = build_marketplace_service(config)
    result = asyncio.run(service.install(bundle_id="demo-agent"))   # signed: OK
    assert result["installed"]

    # tamper: re-index UNSIGNED while the install still pins the key -> refuse
    build_index(package.parent)
    with pytest.raises(BundleError, match="unsigned artifact"):
        asyncio.run(service.install(bundle_id="demo-agent"))

    # tamper: sign with the WRONG key -> refuse
    wrong_private, _ = signing.generate_keypair()
    build_index(package.parent, private_key_b64=wrong_private, public_key_b64=public_b64)
    with pytest.raises(BundleError, match="signature INVALID"):
        asyncio.run(service.install(bundle_id="demo-agent"))


def test_uninstall_unknown_bundle_message(tmp_path):
    service = _service(tmp_path)
    with pytest.raises(BundleError, match="not an installed bundle"):
        asyncio.run(service.uninstall("nope"))
