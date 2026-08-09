"""M6: distribution profiles + the Provisioned gate at the plugin-discovery chokepoint."""

import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.distribution import OPEN_PROFILE, load_profile, parse_profile
from agent_runtime.infrastructure.plugins.discovery import _passes_gates
from agent_runtime.infrastructure.plugins.manifest import PluginManifest


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
            "platform": {
                "accounts_url": "https://accounts.example/",
                "model_proxy_url": "https://proxy.example",
            },
        },
        source_path="x/distribution.toml",
    )
    assert profile.product_name == "Figure Creator Studio"
    assert profile.default_agent == "figure-creator"
    assert profile.is_provisioned("figures") and not profile.is_provisioned("video")
    assert profile.publisher_key == "PK" and not profile.is_open
    # [platform] endpoints: parsed, trailing slash normalized away
    assert profile.accounts_url == "https://accounts.example"
    assert profile.model_proxy_url == "https://proxy.example"


def test_platform_absent_means_byok_only():
    profile = parse_profile({"product": {"id": "agentd"}}, source_path="x")
    assert profile.accounts_url == "" and profile.model_proxy_url == ""


def test_legacy_model_gateway_url_is_still_parsed():
    profile = parse_profile(
        {"platform": {"model_gateway_url": "https://legacy-proxy.example/"}},
        source_path="legacy/distribution.toml",
    )
    assert profile.model_proxy_url == "https://legacy-proxy.example"
    assert profile.model_gateway_url == profile.model_proxy_url


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


# ── the marketplace's two env doors (a CONTAINER has no installer to bake a profile) ──
#
# The hosted daemon is configured entirely by task env, so both halves of "which registry, signed
# by whom" must be reachable that way. Only the url used to be, which meant the one way to give a
# container a store also turned signature verification off.

_PUBLIC_KEY = "gYM/XoS5CZo1yNAdW2Ai4HwnLNDlJhl/nvJUh5TavFY="


def test_publisher_key_env_pins_the_profile(monkeypatch):
    from agent_runtime.config import load_config

    monkeypatch.setenv("AGENTD_PUBLISHER_KEY", _PUBLIC_KEY)
    assert load_config().distribution.publisher_key == _PUBLIC_KEY


def test_registry_and_publisher_key_arrive_together(monkeypatch):
    """The pair a hosted daemon needs: a store to list, and a key to verify downloads against."""
    from agent_runtime.config import load_config

    monkeypatch.setenv("AGENTD_REGISTRY", "https://example.invalid/index.json")
    monkeypatch.setenv("AGENTD_PUBLISHER_KEY", _PUBLIC_KEY)
    config = load_config()
    assert config.registry_url == "https://example.invalid/index.json"
    assert config.distribution.publisher_key == _PUBLIC_KEY


def test_publisher_key_env_absent_leaves_the_profile_alone(monkeypatch):
    from agent_runtime.config import load_config

    monkeypatch.delenv("AGENTD_PUBLISHER_KEY", raising=False)
    # Whatever the ambient profile says, an unset variable must not blank it — that would silently
    # unpin every desktop build, whose key comes from its baked distribution.toml.
    before = load_config().distribution.publisher_key
    monkeypatch.setenv("AGENTD_PUBLISHER_KEY", "   ")  # whitespace is not a key
    assert load_config().distribution.publisher_key == before


# ── a checkout runs as a real product, not as "no product" ──────────────────
# The flavor files are tracked in the repo and baked into installers, and the desktop shell passes
# the one it was built with via AGENTD_DISTRIBUTION. A daemon started straight from a checkout got
# none of that and silently ran as the OPEN profile — so everything a profile carries was invisible
# during development. That is not a cosmetic gap: with no accounts_url, every agent UI was told
# this build has no sign-in and its login screen rendered nothing, on every developer machine.
def test_a_checkout_falls_back_to_its_own_tracked_flavor(monkeypatch):
    from agent_runtime import runtime_paths

    monkeypatch.delenv("AGENTD_DISTRIBUTION", raising=False)
    tracked = runtime_paths.checkout_distribution_file()
    assert tracked.is_file(), "the core flavor profile is tracked in the repo"
    assert runtime_paths.distribution_candidates()[-1] == tracked


def test_it_is_the_LAST_resort(monkeypatch):
    """An installed build's own baked profile must beat a repo file sitting next to it, and both
    yield to an explicit override. Order is the whole safety property here."""
    from agent_runtime import runtime_paths

    monkeypatch.setenv("AGENTD_DISTRIBUTION", "/tmp/explicit.toml")
    candidates = runtime_paths.distribution_candidates()
    assert candidates[0] == Path("/tmp/explicit.toml")
    assert candidates.index(runtime_paths.user_home() / "distribution.toml") < len(candidates) - 1
    assert candidates[-1] == runtime_paths.checkout_distribution_file()


def test_a_packaged_build_never_reaches_for_the_repo(monkeypatch):
    """REPO_ROOT is site-packages/ in a wheel — a path there is not a flavor file, it is nothing."""
    from agent_runtime import runtime_paths

    monkeypatch.delenv("AGENTD_DISTRIBUTION", raising=False)
    monkeypatch.setattr(runtime_paths, "is_packaged", lambda: True)
    assert runtime_paths.checkout_distribution_file() not in runtime_paths.distribution_candidates()


def test_the_checkout_profile_carries_the_accounts_url(monkeypatch):
    """The specific value whose absence produced a login screen that never appeared."""
    from agent_runtime.config import accounts_api_base, load_config

    monkeypatch.delenv("AGENTD_DISTRIBUTION", raising=False)
    monkeypatch.delenv("AGENTD_ACCOUNTS_URL", raising=False)
    assert accounts_api_base(load_config()).startswith("http")
