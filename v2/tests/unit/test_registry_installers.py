"""Installers in the registry — the artifact for someone who has NOTHING installed yet.

A .agentpkg is installed by a running daemon, so on its own the marketplace can only sell to
people who already own the product. An `installers` row is what a browser downloads on a bare
machine. These tests pin the three things that make that safe rather than merely working:

  * the file→bundle link is a NAMING CONVENTION, and anything that does not match is ignored
    rather than guessed at (a mis-attached installer offers one agent's download on another's card)
  * each installer's digest is signed SEPARATELY, because the entry signature covers only the
    .agentpkg — unsigned rows would let anyone who can write to the registry swap the executable
    without breaking a signature
  * a client receives an ABSOLUTE url, resolved by the daemon that knows where the index came from
  * the `installers` key is parsed TOLERANTLY: it was added after clients shipped, so junk in it
    must never blank an older install's store page
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.application.services.marketplace_service import _entry_dict
from agent_runtime.domain.bundle import RegistryEntry, parse_registry_index
from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace.index_builder import (
    PLATFORM_BY_EXT,
    build_index,
    installer_name,
)


def _bundle(directory: Path, bundle_id="game-master", version="0.2.0") -> Path:
    """A minimal real .agentpkg, packed the way the CLI packs one."""
    from agent_runtime.cli.commands.bundle import _pack_agent_dir

    agent = directory / "src" / bundle_id
    agent.mkdir(parents=True)
    (agent / "agent.toml").write_text(
        f'name = "Game Master"\nversion = "{version}"\n', encoding="utf-8"
    )
    out = directory / "registry"
    out.mkdir(exist_ok=True)
    return _pack_agent_dir(agent, out)


def _index(directory: Path, **kw) -> dict:
    path = build_index(directory, name="test", **kw)
    return json.loads(path.read_text(encoding="utf-8"))


def _entry(index: dict, bundle_id="game-master") -> dict:
    return next(b for b in index["bundles"] if b["id"] == bundle_id)


# --- discovery by convention ------------------------------------------------


def test_an_installer_named_by_convention_is_indexed(tmp_path):
    pkg = _bundle(tmp_path)
    exe = pkg.parent / installer_name("game-master", "0.2.0", ".exe")
    exe.write_bytes(b"MZ fake installer")

    row = _entry(_index(pkg.parent))["installers"][0]
    assert row["platform"] == "win"
    assert row["url"] == exe.name  # RELATIVE, same rule as the bundle url
    assert row["size"] == len(b"MZ fake installer")
    assert row["sha256"]


def test_no_installer_means_no_key_at_all(tmp_path):
    """An agent that ships none must not grow an empty array — the client tests for the key's
    presence to decide whether to render a Download button."""
    pkg = _bundle(tmp_path)
    assert "installers" not in _entry(_index(pkg.parent))


@pytest.mark.parametrize(
    "filename",
    [
        "game-master-9.9.9-setup.exe",  # a DIFFERENT version
        "other-agent-0.2.0-setup.exe",  # a different bundle
        "game-master-0.2.0.exe",  # missing the -setup marker
        "Game Master Setup 0.2.0.exe",  # electron-builder's raw output, un-renamed
        "game-master-0.2.0-setup.txt",  # not an installer extension
    ],
)
def test_files_that_do_not_match_the_convention_are_ignored(tmp_path, filename):
    """Ignored, never guessed at. Attaching the wrong file would offer a download that installs
    something other than the card says."""
    pkg = _bundle(tmp_path)
    (pkg.parent / filename).write_bytes(b"nope")
    assert "installers" not in _entry(_index(pkg.parent)), filename


def test_several_platforms_are_indexed_independently(tmp_path):
    pkg = _bundle(tmp_path)
    for suffix in (".exe", ".dmg", ".appimage"):
        (pkg.parent / installer_name("game-master", "0.2.0", suffix)).write_bytes(b"x")
    got = {r["platform"] for r in _entry(_index(pkg.parent))["installers"]}
    assert got == {"win", "mac", "linux"}


def test_platform_map_never_sends_a_mac_a_windows_build(tmp_path):
    """The extension map is the only thing deciding who is offered what."""
    assert PLATFORM_BY_EXT[".exe"] == "win"
    assert PLATFORM_BY_EXT[".dmg"] == "mac"
    assert PLATFORM_BY_EXT[".appimage"] == "linux"


# --- signing ----------------------------------------------------------------


def test_each_installer_digest_is_signed_on_its_own(tmp_path):
    """THE security property. The entry `sig` covers the .agentpkg digest only, so without this a
    registry writer could repoint the download at any executable and nothing would notice."""
    private, public = signing.generate_keypair()
    pkg = _bundle(tmp_path)
    exe = pkg.parent / installer_name("game-master", "0.2.0", ".exe")
    exe.write_bytes(b"MZ fake installer")

    row = _entry(_index(pkg.parent, private_key_b64=private, public_key_b64=public))["installers"][0]
    assert signing.verify(public, row["sha256"].encode("ascii"), row["sig"])


def test_a_tampered_installer_digest_fails_its_signature(tmp_path):
    private, public = signing.generate_keypair()
    pkg = _bundle(tmp_path)
    (pkg.parent / installer_name("game-master", "0.2.0", ".exe")).write_bytes(b"MZ real")
    row = _entry(_index(pkg.parent, private_key_b64=private, public_key_b64=public))["installers"][0]
    assert not signing.verify(public, b"0" * 64, row["sig"])


def test_an_unsigned_index_carries_no_installer_signature(tmp_path):
    """An unsigned registry must not present signatures it cannot vouch for."""
    pkg = _bundle(tmp_path)
    (pkg.parent / installer_name("game-master", "0.2.0", ".exe")).write_bytes(b"x")
    assert "sig" not in _entry(_index(pkg.parent))["installers"][0]


# --- parsing back (the client half) -----------------------------------------


def test_round_trip_through_the_index_parser(tmp_path):
    pkg = _bundle(tmp_path)
    (pkg.parent / installer_name("game-master", "0.2.0", ".exe")).write_bytes(b"MZ x")
    index = parse_registry_index(_index(pkg.parent))
    entry = next(e for e in index.bundles if e.id == "game-master")
    assert len(entry.installers) == 1
    assert entry.installers[0].platform == "win"
    assert entry.installers[0].url.endswith("-setup.exe")


@pytest.mark.parametrize(
    ("installers", "why"),
    [
        ("not a list", "a string where an array belongs"),
        ([{"platform": "win"}], "a row with no url"),
        ([{"url": "x.exe"}], "a row with no platform"),
        (["just a string"], "a non-object row"),
        ([], "an empty array"),
    ],
)
def test_junk_installers_are_dropped_not_raised(installers, why):
    """This key postdates shipped clients. A registry that upsets the parser would show an EMPTY
    marketplace with no explanation on every older install — the failure mode is silent and total,
    so tolerance here is not politeness."""
    index = parse_registry_index(
        {
            "schema": 1,
            "bundles": [{"id": "game-master", "name": "GM", "version": "1", "url": "a.agentpkg", "installers": installers}],
        }
    )
    assert index.bundles[0].installers == (), why
    assert index.bundles[0].id == "game-master", why  # the entry itself still survives


def test_a_row_with_an_unparseable_size_still_offers_the_download(tmp_path):
    """Size is cosmetic (it labels the button). Losing the download over it would be a bad trade."""
    index = parse_registry_index(
        {
            "schema": 1,
            "bundles": [
                {
                    "id": "gm",
                    "name": "GM",
                    "version": "1",
                    "url": "a.agentpkg",
                    "installers": [{"platform": "win", "url": "gm-setup.exe", "size": "huge"}],
                }
            ],
        }
    )
    assert index.bundles[0].installers[0].size == 0
    assert index.bundles[0].installers[0].url == "gm-setup.exe"


# --- what the client is handed ----------------------------------------------


def test_the_client_receives_an_absolute_url(tmp_path):
    """Resolved daemon-side: the registry base url is the daemon's knowledge, and a client that
    had to join them itself would need that base shipped to it purely to build a link."""
    entry = RegistryEntry(
        id="gm",
        name="GM",
        version="1",
        installers=parse_registry_index(
            {
                "schema": 1,
                "bundles": [
                    {
                        "id": "gm",
                        "name": "GM",
                        "version": "1",
                        "url": "a.agentpkg",
                        "installers": [{"platform": "win", "url": "gm-0.2.0-setup.exe"}],
                    }
                ],
            }
        ).bundles[0].installers,
    )

    from agent_runtime.infrastructure.marketplace.registry_client import RegistryClient

    client = RegistryClient.__new__(RegistryClient)
    client._index_url = "https://example.test/registry/index.json"

    out = _entry_dict(entry, client.asset_url)
    assert out["installers"][0]["url"] == "https://example.test/registry/gm-0.2.0-setup.exe"


def test_no_resolver_leaves_the_relative_url_alone(tmp_path):
    """A registry served off a local directory has no base to join against."""
    entry = RegistryEntry(id="gm", name="GM", version="1")
    assert "installers" not in _entry_dict(entry)


# --- staging: the publish step that renames the delivered exe ---------------


def test_publish_stages_the_installer_matching_the_version(tmp_path):
    """dist-app.mjs delivers `<Product> Setup <ver>.exe`; several versions accumulate in that
    folder. The one whose name states the version being published must win, or the registry
    advertises 0.3.0 and hands over an older build."""
    from agent_runtime.cli.commands.bundle import _stage_installers

    delivered = tmp_path / "agents" / "game-master" / "clients" / "desktop"
    delivered.mkdir(parents=True)
    (delivered / "Game Master Setup 0.1.0.exe").write_bytes(b"old")
    (delivered / "Game Master Setup 0.2.0.exe").write_bytes(b"new")

    staging = tmp_path / "registry"
    staging.mkdir()
    staged = _stage_installers(tmp_path / "agents" / "game-master", staging, "game-master", "0.2.0")

    assert [p.name for p in staged] == ["game-master-0.2.0-setup.exe"]
    assert staged[0].read_bytes() == b"new"


def test_publish_warns_when_no_delivered_installer_names_the_version(tmp_path, capsys):
    """It still publishes the newest — refusing would block a publish over a cosmetic mismatch —
    but silence here would ship an installer that lies about its own version."""
    from agent_runtime.cli.commands.bundle import _stage_installers

    delivered = tmp_path / "agents" / "gm" / "clients" / "desktop"
    delivered.mkdir(parents=True)
    (delivered / "Game Master Setup 0.1.0.exe").write_bytes(b"old")

    staging = tmp_path / "registry"
    staging.mkdir()
    staged = _stage_installers(tmp_path / "agents" / "gm", staging, "gm", "0.9.9")

    assert [p.name for p in staged] == ["gm-0.9.9-setup.exe"]
    assert "WARNING" in capsys.readouterr().out


def test_no_delivered_installer_stages_nothing(tmp_path):
    """Publishing an agent nobody built an exe for is normal, not an error."""
    from agent_runtime.cli.commands.bundle import _stage_installers

    agent = tmp_path / "agents" / "gm"
    agent.mkdir(parents=True)
    staging = tmp_path / "registry"
    staging.mkdir()
    assert _stage_installers(agent, staging, "gm", "1.0.0") == []
