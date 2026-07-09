"""M6: distribution profiles + the Provisioned gate at the plugin-discovery chokepoint."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.distribution import OPEN_PROFILE, load_profile, parse_profile
from agentd.infrastructure.plugins.discovery import _passes_gates
from agentd.infrastructure.plugins.manifest import PluginManifest


def _manifest(plugin_id: str, **kwargs) -> PluginManifest:
    return PluginManifest(id=plugin_id, name=plugin_id, kind="native", entry="x:y", **kwargs)


def _config(profile=None, plugins=None):
    return SimpleNamespace(distribution=profile, plugins=plugins or {})


def test_open_profile_provisions_everything():
    assert OPEN_PROFILE.is_provisioned("anything")
    assert OPEN_PROFILE.store_enabled and OPEN_PROFILE.is_open


def test_parse_profile_full():
    profile = parse_profile(
        {
            "product": {
                "id": "fc-studio",
                "name": "Figure Creator Studio",
                "default_agent": "figure-creator",
                "preinstalled_bundles": ["figure-creator"],
            },
            "provisioning": {"plugins": ["core_fs", "figures"]},
            "store": {
                "enabled": True,
                "registry_url": "https://r.example/index.json",
                "publisher_key": "PK",
            },
        },
        source_path="x/distribution.toml",
    )
    assert profile.product_name == "Figure Creator Studio"
    assert profile.default_agent == "figure-creator"
    assert profile.is_provisioned("figures") and not profile.is_provisioned("video")
    assert profile.publisher_key == "PK" and not profile.is_open


def test_bad_profile_degrades_to_open(tmp_path):
    bad = tmp_path / "distribution.toml"
    bad.write_text("not [valid toml", encoding="utf-8")
    assert load_profile(bad).is_open


def test_missing_profile_is_open(tmp_path):
    assert load_profile(tmp_path / "nope.toml").is_open


def test_provisioned_gate_blocks_unlisted_plugin():
    profile = parse_profile({"provisioning": {"plugins": ["figures"]}}, "x")
    config = _config(profile=profile)
    assert _passes_gates(config, _manifest("figures"), None)
    assert not _passes_gates(config, _manifest("video"), None)


def test_no_profile_or_no_list_gates_nothing():
    assert _passes_gates(_config(profile=None), _manifest("video"), None)
    assert _passes_gates(_config(profile=OPEN_PROFILE), _manifest("video"), None)


def test_enabled_gate_still_applies_after_provisioning():
    profile = parse_profile({"provisioning": {"plugins": ["figures"]}}, "x")
    config = _config(profile=profile, plugins={"figures": False})
    assert not _passes_gates(config, _manifest("figures"), None), (
        "provisioned but config-disabled must stay off"
    )
