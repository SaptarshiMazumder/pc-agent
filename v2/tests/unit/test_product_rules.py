"""ProductRules — the precedence chains that decide what a product IS.

These are the rules that used to live in a Node build script, where nothing tested them and one
agent consequently shipped three different versions of itself at once (agent.toml 1.1.0, the
registry 1.0.0, the installer 0.1.5). Installs supersede BY VERSION, so the visible symptom was
authors publishing updates that never reached anyone.
"""

from __future__ import annotations

import tomllib

import pytest

from agent_runtime.distribution import parse_profile, render_profile
from agent_runtime.domain.product import (
    EngineRef,
    PlatformEndpoints,
    ProductDefaults,
    ProductError,
    ProductOverrides,
    ProductRules,
    installer_filename,
)


def app_toml(**extra) -> dict:
    """A minimal APP agent — the [app] table is what makes an agent shippable at all."""
    return {"name": "Weather Agent", "version": "2.3.0", "app": {}, **extra}


# ── name ────────────────────────────────────────────────────────────────────────────────


def test_display_name_prefers_app_title_over_agent_name():
    spec = ProductRules().derive("weather", app_toml(app={"title": "Weather Deluxe"}))
    assert spec.name == "Weather Deluxe"


def test_display_name_falls_back_to_agent_name_then_id():
    assert ProductRules().derive("weather", app_toml()).name == "Weather Agent"
    assert ProductRules().derive("weather", {"app": {}}).name == "weather"


def test_override_beats_everything():
    spec = ProductRules().derive(
        "weather", app_toml(app={"title": "Weather Deluxe"}), ProductOverrides(name="Sky")
    )
    assert spec.name == "Sky"


# ── version: the bug this module exists for ─────────────────────────────────────────────


def test_version_comes_from_agent_toml_not_a_fallback():
    assert ProductRules().derive("weather", app_toml()).version == "2.3.0"


def test_version_falls_back_only_when_undeclared():
    spec = ProductRules().derive("weather", {"app": {}})
    assert spec.version == ProductDefaults().version_fallback == "1.0.0"


def test_version_override_wins():
    spec = ProductRules().derive("weather", app_toml(), ProductOverrides(version="9.9.9"))
    assert spec.version == "9.9.9"


# ── identity ────────────────────────────────────────────────────────────────────────────


def test_product_id_is_distinct_from_the_agent_id():
    # One machine holds both: the AGENT in ~/.agentd/agents/weather and the PRODUCT in Programs.
    spec = ProductRules().derive("weather", app_toml())
    assert spec.agent_id == "weather"
    assert spec.product_id == "weather-app"


def test_app_id_is_derived_but_overridable_by_the_agent():
    assert ProductRules().derive("weather", app_toml()).app_id == "dev.agentd.app.weather"
    declared = ProductRules().derive("weather", app_toml(app={"app_id": "com.acme.weather"}))
    assert declared.app_id == "com.acme.weather"


def test_app_id_prefix_comes_from_defaults_not_a_literal():
    rules = ProductRules(ProductDefaults(app_id_prefix="com.acme.apps"))
    assert rules.derive("weather", app_toml()).app_id == "com.acme.apps.weather"


# ── icons: candidates, because the domain has no filesystem ─────────────────────────────


def test_icon_candidates_are_ordered_declaration_then_convention():
    spec = ProductRules().derive("weather", app_toml(app={"icon": "brand/logo.ico"}))
    assert spec.icon_candidates == ("brand/logo.ico", "icon.ico")


def test_icon_candidates_never_repeat_the_same_path():
    spec = ProductRules().derive("weather", app_toml(app={"icon": "icon.ico"}))
    assert spec.icon_candidates == ("icon.ico",)


def test_icon_override_is_tried_first():
    spec = ProductRules().derive("weather", app_toml(), ProductOverrides(icon="custom.ico"))
    assert spec.icon_candidates[0] == "custom.ico"


def test_windows_separators_are_normalised():
    spec = ProductRules().derive("weather", app_toml(app={"icon": r"brand\logo.ico"}))
    assert spec.icon_candidates[0] == "brand/logo.ico"


# ── refusals ────────────────────────────────────────────────────────────────────────────


def test_an_agent_with_no_app_section_cannot_be_a_product():
    with pytest.raises(ProductError, match="no \\[app\\] section"):
        ProductRules().derive("headless", {"name": "Headless", "version": "1.0.0"})


def test_a_package_with_no_declaration_needs_an_explicit_name():
    # Intake from a published .agentpkg on a machine that has no copy of the agent directory.
    with pytest.raises(ProductError, match="explicit product name"):
        ProductRules().derive("weather", {})


def test_a_package_with_a_name_override_is_allowed_through():
    # The [app] check is WAIVED here because it cannot be performed — not because it stopped
    # applying. Nothing else about the derivation changes.
    spec = ProductRules().derive("weather", {}, ProductOverrides(name="Weather", version="1.2.3"))
    assert (spec.name, spec.version) == ("Weather", "1.2.3")


def test_no_agent_id_is_refused():
    with pytest.raises(ProductError, match="needs an agent id"):
        ProductRules().derive("  ", app_toml())


# ── platform endpoints ──────────────────────────────────────────────────────────────────


def test_platform_is_inherited_from_the_install_defaults():
    hosted = ProductDefaults(
        platform=PlatformEndpoints("https://accounts.example", "https://models.example")
    )
    spec = ProductRules(hosted).derive("weather", app_toml())
    assert spec.hosted
    assert spec.platform.accounts_url == "https://accounts.example"


def test_a_checkout_with_no_platform_produces_a_byok_product():
    assert not ProductRules().derive("weather", app_toml()).hosted


def test_platform_override_replaces_the_inherited_pair():
    hosted = ProductDefaults(platform=PlatformEndpoints("https://a", "https://m"))
    spec = ProductRules(hosted).derive(
        "weather", app_toml(), ProductOverrides(platform=PlatformEndpoints("https://x", "https://y"))
    )
    assert (spec.platform.accounts_url, spec.platform.model_proxy_url) == ("https://x", "https://y")


def test_half_configured_endpoints_are_not_hosted():
    assert not PlatformEndpoints("https://accounts.example", "").hosted
    assert not PlatformEndpoints("", "https://models.example").hosted


# ── the payload document round-trips through the reader that parses it ───────────────────


def test_profile_round_trips_so_a_product_ships_a_readable_document():
    """The whole reason render_profile lives beside parse_profile.

    A payload's distribution.toml is read by TWO parsers — this one, and the desktop shell's. A
    writer that could emit something this reader does not accept would produce an installer that
    opens the generic client instead of the author's app, with nothing in the file to show why.
    """
    spec = ProductRules(
        ProductDefaults(platform=PlatformEndpoints("https://accounts.example", "https://models.example"))
    ).derive("weather", app_toml(app={"title": "Weather Deluxe"}))
    profile = spec.to_profile(icon="icon.ico")

    reparsed = parse_profile(tomllib.loads(render_profile(profile, header="generated")))
    assert reparsed == profile


def test_the_rendered_document_carries_app_agent():
    # app_agent is THE knob, and it is one the daemon itself never reads — so a writer built only
    # from the fields the daemon uses would have dropped it and produced a silently wrong product.
    spec = ProductRules().derive("weather", app_toml())
    text = render_profile(spec.to_profile())
    assert 'app_agent = "weather"' in text
    assert "[store]" in text and "enabled = false" in text  # a single-agent app has no store


# ── installer naming: one convention, shared with the registry reader ────────────────────


def test_installer_filename_matches_the_registry_convention():
    from agent_runtime.infrastructure.marketplace import index_builder

    spec = ProductRules().derive("weather", app_toml())
    assert spec.installer_filename(".exe") == "weather-2.3.0-setup.exe"
    # The publisher-side reader must find exactly what the builder wrote. Same function, so it
    # cannot drift — this asserts the re-export is actually wired.
    assert index_builder.installer_name("weather", "2.3.0", ".EXE") == spec.installer_filename(".exe")
    assert installer_filename("weather", "2.3.0", ".exe") == spec.installer_filename()


# ── EngineRef ───────────────────────────────────────────────────────────────────────────


def test_an_engine_without_a_digest_is_not_usable():
    assert not EngineRef("win", url="https://x/e.exe").usable
    assert not EngineRef("win", sha256="ab" * 32).usable
    assert EngineRef("win", url="https://x/e.exe", sha256="ab" * 32).usable
