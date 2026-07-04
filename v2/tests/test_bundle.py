"""Domain rules for bundles (M4): manifest parsing, compat, index parsing."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.domain.bundle import (
    BundleError,
    compat_ok,
    is_update,
    parse_bundle_manifest,
    parse_registry_index,
)


def _manifest(**bundle):
    base = {"id": "figure-creator", "name": "Figure Creator", "version": "1.0.0"}
    base.update(bundle)
    return {"bundle": base}


def test_parse_minimal_manifest():
    m = parse_bundle_manifest(_manifest())
    assert m.id == "figure-creator" and m.version == "1.0.0" and m.plugins == ()
    assert m.entitlement == ""  # free by default


def test_parse_plugin_deps_all_sources():
    m = parse_bundle_manifest(_manifest(plugins=[
        {"id": "figures", "source": "vendored"},
        {"id": "webx", "source": "pip", "package": "agentd-plugin-webx", "version": ">=1"},
        {"id": "shell", "source": "builtin"},
    ]))
    assert [d.source for d in m.plugins] == ["vendored", "pip", "builtin"]
    assert m.plugins[1].package == "agentd-plugin-webx"


@pytest.mark.parametrize("bad", [
    {},                                                    # no [bundle]
    _manifest(id="../evil"),                               # bad id
    _manifest(version="not-a-version"),                    # bad version
    _manifest(plugins=[{"id": "x", "source": "carrier-pigeon"}]),   # bad source
    _manifest(plugins=[{"id": "x", "source": "pip"}]),     # pip without package
])
def test_parse_rejects_malformed(bad):
    with pytest.raises(BundleError):
        parse_bundle_manifest(bad if "bundle" in bad else bad)


def test_compat_rules():
    assert compat_ok("0.1.0", "")                  # empty spec: anything goes
    assert compat_ok("0.2.5", ">=0.1,<1")
    assert not compat_ok("1.0.0", ">=0.1,<1")
    assert not compat_ok("0.1.0", "garbage spec")  # malformed: fail closed


def test_is_update():
    assert is_update("1.0.0", "1.1.0")
    assert not is_update("1.1.0", "1.0.0")
    assert not is_update("1.0.0", "not-a-version")


def test_parse_registry_index_skips_junk_rows():
    index = parse_registry_index({"schema": 1, "publisher_key": "PK", "bundles": [
        {"id": "good", "version": "1.0.0", "url": "good-1.0.0.agentpkg", "sha256": "aa"},
        {"id": "../evil", "version": "1"},        # invalid id -> skipped
        "not-a-dict",                              # junk -> skipped
    ]})
    assert [e.id for e in index.bundles] == ["good"]
    assert index.publisher_key == "PK"


def test_parse_registry_index_rejects_unknown_schema():
    with pytest.raises(BundleError):
        parse_registry_index({"schema": 2, "bundles": []})
