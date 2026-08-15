"""Packing an agent must never ship a stale client SDK.

WHY THIS TEST EXISTS. `ui/vendor/agentd-client.js` is COPIED into an agent when it is scaffolded,
so one SDK lives as N independent copies: the repo's agents, agents authored in an account
directory, agents already installed on a machine, and the embedded runtime's own. A fix therefore
has to be chased into every copy, and the ones that are missed fail quietly and much later —
"that method is not a function", or a sign-in that reads a response field the server stopped
sending. That is not hypothetical: a published agent shipped a client that read the pre-token
`login.token` field, so its sign-in failed against a current server while the server returned 200.

Substituting at pack time makes the whole class impossible: whatever is on disk, the PACKAGE
carries the SDK belonging to the engine that packed it.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from agent_runtime.infrastructure.marketplace import bundle_io
from agent_runtime.infrastructure.marketplace.bundle_io import VENDORED_SDK, pack_bundle
from agent_runtime.domain.bundle import BundleManifest

CURRENT = b"// current SDK build\nexport const marker = 'current'\n"
STALE = b"// ancient SDK build\nexport const marker = 'stale'\n"


@pytest.fixture
def agent(tmp_path: Path) -> Path:
    d = tmp_path / "demo-agent"
    (d / "ui" / "vendor").mkdir(parents=True)
    (d / "agent.toml").write_text('name = "Demo"\n', encoding="utf-8")
    (d / VENDORED_SDK).write_bytes(STALE)
    (d / "ui" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    return d


def _manifest() -> BundleManifest:
    return BundleManifest(id="demo-agent", name="Demo", version="1.0.0")


def _entry(pkg: Path, name: str) -> bytes:
    with zipfile.ZipFile(pkg) as zf:
        return zf.read(name)


def test_a_stale_vendored_sdk_is_replaced(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: CURRENT)
    pkg = pack_bundle(agent, tmp_path / "out", _manifest())
    assert _entry(pkg, "agent/ui/vendor/agentd-client.js") == CURRENT


def test_the_authors_source_is_left_alone(agent, tmp_path, monkeypatch):
    """Packing must not rewrite the directory it was pointed at — that would be a surprise, and
    the thing that has to be current is the artifact, not the author's tree."""
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: CURRENT)
    pack_bundle(agent, tmp_path / "out", _manifest())
    assert (agent / VENDORED_SDK).read_bytes() == STALE


def test_zip_entry_uses_forward_slashes(agent, tmp_path, monkeypatch):
    """`writestr` does NOT normalise separators the way `write` does, so a Windows-built package
    could carry `agent\\ui\\vendor\\...` — one path component to a POSIX reader, and an agent
    whose UI silently has no SDK at all."""
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: CURRENT)
    pkg = pack_bundle(agent, tmp_path / "out", _manifest())
    with zipfile.ZipFile(pkg) as zf:
        names = zf.namelist()
    assert "agent/ui/vendor/agentd-client.js" in names
    assert not any("\\" in n for n in names)


def test_other_files_are_untouched(agent, tmp_path, monkeypatch):
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: CURRENT)
    pkg = pack_bundle(agent, tmp_path / "out", _manifest())
    assert _entry(pkg, "agent/ui/index.html") == b"<!doctype html>"
    assert b"Demo" in _entry(pkg, "agent/agent.toml")


def test_an_engine_with_no_sdk_asset_packs_the_file_as_is(agent, tmp_path, monkeypatch):
    """Degrade, never fail. An install without the asset (an odd build, a partial checkout) must
    still be able to pack — shipping the author's copy is exactly the old behaviour."""
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: None)
    pkg = pack_bundle(agent, tmp_path / "out", _manifest())
    assert _entry(pkg, "agent/ui/vendor/agentd-client.js") == STALE


def test_an_agent_without_a_ui_is_unaffected(tmp_path, monkeypatch):
    """A UI-less agent must not be given a vendor directory it never had."""
    monkeypatch.setattr(bundle_io, "_canonical_sdk", lambda: CURRENT)
    d = tmp_path / "headless"
    d.mkdir()
    (d / "agent.toml").write_text('name = "Headless"\n', encoding="utf-8")
    pkg = pack_bundle(d, tmp_path / "out", BundleManifest(id="headless", name="H", version="1.0.0"))
    with zipfile.ZipFile(pkg) as zf:
        assert not [n for n in zf.namelist() if "vendor" in n]


def test_the_real_asset_resolves_in_this_checkout():
    """The resolver must actually find the SDK here, or every test above is checking a mock while
    the real path is broken."""
    from agent_runtime.runtime_paths import sdk_client_asset

    asset = sdk_client_asset()
    assert asset is not None and asset.is_file(), "no SDK asset — run `npm run build` in clients/sdk-js"
    assert asset.stat().st_size > 1000
