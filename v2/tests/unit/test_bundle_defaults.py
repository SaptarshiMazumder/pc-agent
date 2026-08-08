"""BundleDefaults — where a packed agent's identity comes from.

The rule this pins down: `agentd bundle pack` never read agent.toml, so it fell back to the
directory name and a hardcoded "1.0.0". Because installs supersede BY VERSION, that made every
release after the first silently fail to replace the one before it. These tests fix the
precedence that fixes that:

    explicit argument  >  bundle.toml  >  agent.toml  >  fallback
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_authoring.domain.bundle_defaults import DEFAULT_VERSION, BundleDefaults


def _agent(**kw):
    base = {"name": "Weather Bot", "version": "2.4.0", "description": "forecasts"}
    base.update(kw)
    return base


# --- the version bug this phase exists to kill ------------------------------
def test_version_comes_from_agent_toml():
    m = BundleDefaults().manifest("weather", _agent(), {})
    assert m.version == "2.4.0"  # NOT the old hardcoded 1.0.0


def test_explicit_version_wins_over_everything():
    m = BundleDefaults().manifest(
        "weather", _agent(), {"version": "3.0.0"}, version="9.9.9"
    )
    assert m.version == "9.9.9"


def test_bundle_toml_outranks_agent_toml():
    """bundle.toml is the publisher-facing file — it stays authoritative when present."""
    m = BundleDefaults().manifest("weather", _agent(), {"version": "3.0.0"})
    assert m.version == "3.0.0"


def test_falls_back_only_when_nothing_declares_a_version():
    m = BundleDefaults().manifest("weather", _agent(version=""), {})
    assert m.version == DEFAULT_VERSION


# --- identity ---------------------------------------------------------------
def test_id_defaults_to_the_agent_id_and_name_to_agent_toml():
    m = BundleDefaults().manifest("weather", _agent(), {})
    assert (m.id, m.name, m.description) == ("weather", "Weather Bot", "forecasts")


def test_bundle_id_may_differ_from_the_agent_id():
    m = BundleDefaults().manifest("weather", _agent(), {"id": "acme-weather"})
    assert m.id == "acme-weather"


def test_name_falls_back_to_the_id_when_nothing_names_it():
    m = BundleDefaults().manifest("weather", {}, {})
    assert m.name == "weather"


def test_publisher_fields_come_only_from_bundle_toml():
    """publisher/entitlement/icon have no agent.toml equivalent — [app] icon is an installer
    .ico path, while a bundle icon is a store-card glyph name. Do not conflate them."""
    m = BundleDefaults().manifest(
        "weather",
        _agent(**{"app": {"icon": "weather.ico"}}),
        {"publisher": "acme", "entitlement": "pro", "icon": "cloud"},
    )
    assert (m.publisher, m.entitlement, m.icon) == ("acme", "pro", "cloud")

    bare = BundleDefaults().manifest("weather", _agent(**{"app": {"icon": "weather.ico"}}), {})
    assert bare.icon == ""  # the .ico must NOT leak in as a glyph name


# --- dependencies -----------------------------------------------------------
def test_shared_plugin_deps_are_read_from_bundle_toml():
    m = BundleDefaults().manifest(
        "weather",
        _agent(),
        {"plugins": [{"id": "figures", "source": "vendored"}, {"id": "web", "source": "builtin"}]},
    )
    assert [(d.id, d.source) for d in m.plugins] == [("figures", "vendored"), ("web", "builtin")]


def test_dep_rows_without_an_id_are_dropped():
    m = BundleDefaults().manifest("weather", _agent(), {"plugins": [{"source": "pip"}, "junk"]})
    assert m.plugins == ()


def test_private_plugins_are_not_declared_as_dependencies():
    """agents/<id>/plugins/ ships INSIDE the zip's agent/ tree. Declaring it would make the
    installer hunt for a vendored copy at plugins/<pid>/ that was never written."""
    m = BundleDefaults().manifest(
        "weather", _agent(), {}, private_plugin_ids=("weather-kit",)
    )
    assert m.plugins == ()
