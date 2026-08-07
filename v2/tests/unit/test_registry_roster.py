"""Per-creator trust: the signed roster (registry index schema 2).

THE PROBLEM. A schema-1 registry has exactly one publisher key, pinned into every client at
install time. A second creator cannot publish to it: their signature is rejected by every client
already out there, and the only fix is re-pinning — a new build shipped to everyone, per creator.

THE ANSWER these tests pin down. Clients still pin exactly ONE key, the platform root. The root
key signs a ROSTER of creators; each creator signs their own bundles with their own key. Adding a
creator is a roster edit and re-pins nothing.

The tests worth reading are the refusals. A publish that goes wrong here does not look wrong: the
store lists the bundle and every single download fails verification. So each way of getting it
wrong — unknown creator, revoked creator, right creator with the wrong key, a replayed roster —
has a test that says which one it was.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain.bundle import (
    BundleError,
    PublisherEntry,
    parse_publisher_roster,
    parse_registry_index,
    roster_signing_payload,
)
from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace import roster_builder
from agent_runtime.infrastructure.marketplace.registry_client import RegistryClient
from agent_runtime.infrastructure.marketplace.trust import RosterMemory, verify_roster

ISSUED = "2026-08-08T10:00:00Z"


@pytest.fixture
def root():
    return signing.generate_keypair()  # (private, public)


@pytest.fixture
def acme():
    return signing.generate_keypair()


def _roster(root, creators: dict, revoked=(), issued: str = ISSUED) -> dict:
    """creators: {id: public_key}."""
    return roster_builder.build_roster(
        [{"id": cid, "name": cid.title(), "key": key, "added": issued} for cid, key in creators.items()],
        list(revoked),
        issued,
        root[0],
        root[1],
    )


# ─────────────────────────── the signed payload ───────────────────────────


def test_the_signing_payload_is_order_independent():
    """Both sides build these bytes independently. Any disagreement is a signature that never
    verifies, on a path where 'invalid' is indistinguishable from an attack."""
    a = PublisherEntry(id="acme", key="k1", name="Acme", added="x")
    b = PublisherEntry(id="beta", key="k2", name="Beta", added="y")
    assert roster_signing_payload((a, b), ("z",), ISSUED) == roster_signing_payload((b, a), ("z",), ISSUED)


def test_the_payload_changes_when_a_key_changes():
    a = PublisherEntry(id="acme", key="k1")
    swapped = PublisherEntry(id="acme", key="ATTACKER")
    assert roster_signing_payload((a,), (), ISSUED) != roster_signing_payload((swapped,), (), ISSUED)


def test_a_roster_verifies_against_the_root_key(root, acme):
    block = _roster(root, {"acme": acme[1]})
    assert verify_roster(parse_publisher_roster(block), root[1])


def test_a_roster_does_not_verify_against_a_different_root_key(root, acme):
    block = _roster(root, {"acme": acme[1]})
    other = signing.generate_keypair()
    assert not verify_roster(parse_publisher_roster(block), other[1])


def test_swapping_a_creators_key_breaks_the_roster_signature(root, acme):
    """The attack the roster exists to stop: substitute your own key for a trusted creator's."""
    block = _roster(root, {"acme": acme[1]})
    attacker = signing.generate_keypair()
    block["roster"][0]["key"] = attacker[1]
    assert not verify_roster(parse_publisher_roster(block), root[1])


def test_an_unsigned_roster_is_refused_when_a_key_is_pinned(root, acme):
    block = _roster(root, {"acme": acme[1]})
    block.pop("sig")
    assert not verify_roster(parse_publisher_roster(block), root[1])


def test_revocation_removes_a_creator_from_the_trusted_map(root, acme):
    block = _roster(root, {"acme": acme[1]}, revoked=["acme"])
    assert parse_publisher_roster(block).trusted_keys() == {}


def test_a_revoked_creator_stays_visible_in_the_file(root, acme):
    """Deleting the row would block installs just as well and would destroy the record of who they
    were and which key was theirs — the two facts an incident review needs."""
    block = roster_builder.without_publisher(
        _roster(root, {"acme": acme[1]}), publisher_id="acme", issued="2026-08-09T00:00:00Z",
        root_private_b64=root[0], root_public_b64=root[1],
    )
    roster = parse_publisher_roster(block)
    assert [e.id for e in roster.entries] == ["acme"]
    assert roster.revoked == ("acme",)
    assert verify_roster(roster, root[1])  # still signed after the edit


def test_re_adding_a_revoked_creator_un_revokes_them(root, acme):
    block = _roster(root, {"acme": acme[1]}, revoked=["acme"])
    block = roster_builder.with_publisher(
        block, publisher_id="acme", name="Acme", key=acme[1], issued="2026-08-09T00:00:00Z",
        root_private_b64=root[0], root_public_b64=root[1],
    )
    assert parse_publisher_roster(block).trusted_keys() == {"acme": acme[1]}


# ─────────────────────────── parsing, and not breaking schema 1 ───────────────────────────


def test_a_schema_1_index_still_parses(root):
    """Installed clients built before schema 2 existed are out there indefinitely. An index they
    cannot parse shows an empty store with no explanation."""
    index = parse_registry_index(
        {"schema": 1, "publisher_key": root[1], "bundles": [{"id": "a", "version": "1.0.0"}]}
    )
    assert index.schema == 1
    assert index.publishers is None
    assert index.bundles[0].publisher_id == ""


def test_a_schema_2_index_parses_the_roster_and_the_publisher_ids(root, acme):
    index = parse_registry_index(
        {
            "schema": 2,
            "publisher_key": root[1],
            "publishers": _roster(root, {"acme": acme[1]}),
            "bundles": [{"id": "a", "version": "1.0.0", "publisher_id": "acme"}],
        }
    )
    assert index.schema == 2
    assert index.publishers.trusted_keys() == {"acme": acme[1]}
    assert index.bundles[0].publisher_id == "acme"


def test_a_future_schema_is_refused_with_a_useful_message():
    with pytest.raises(BundleError) as e:
        parse_registry_index({"schema": 9, "bundles": []})
    assert "update agentd" in str(e.value)


# ─────────────────────────── the client, end to end ───────────────────────────


def _registry(tmp_path: Path, root, entries: list[dict], roster_block: dict | None) -> Path:
    """Write a real on-disk registry: artifacts plus an index.json pointing at them."""
    directory = tmp_path / "registry"
    directory.mkdir(exist_ok=True)
    bundles = []
    for spec in entries:
        artifact = directory / f"{spec['id']}-1.0.0.agentpkg"
        artifact.write_bytes(spec["id"].encode() * 64)
        digest = hashlib.sha256(artifact.read_bytes()).hexdigest()
        row = {
            "id": spec["id"],
            "name": spec["id"],
            "version": "1.0.0",
            "url": artifact.name,
            "sha256": digest,
        }
        if spec.get("publisher_id"):
            row["publisher_id"] = spec["publisher_id"]
        if spec.get("signer"):
            row["sig"] = signing.sign(spec["signer"], digest.encode("ascii"))
        bundles.append(row)
    index = {
        "schema": 2 if roster_block is not None else 1,
        "name": "test registry",
        "publisher_key": root[1],
        "bundles": bundles,
    }
    if roster_block is not None:
        index["publishers"] = roster_block
    (directory / "index.json").write_text(json.dumps(index, indent=2), encoding="utf-8")
    return directory


def _client(directory: Path, root, tmp_path: Path) -> RegistryClient:
    return RegistryClient(
        str(directory), pinned_publisher_key=root[1], trust_state_path=tmp_path / "trust.json"
    )


def _install(client: RegistryClient, bundle_id: str, tmp_path: Path):
    async def go():
        index = await client.fetch_index()
        entry = next(b for b in index.bundles if b.id == bundle_id)
        return await client.download(entry, tmp_path / "dl")

    return asyncio.run(go())


def test_a_creator_signed_bundle_installs(tmp_path, root, acme):
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": acme[0]}],
        _roster(root, {"acme": acme[1]}),
    )
    assert _install(_client(directory, root, tmp_path), "figures", tmp_path).is_file()


def test_two_creators_coexist_each_verified_against_their_own_key(tmp_path, root, acme):
    """The thing schema 1 could not do at all."""
    beta = signing.generate_keypair()
    directory = _registry(
        tmp_path, root,
        [
            {"id": "figures", "publisher_id": "acme", "signer": acme[0]},
            {"id": "decks", "publisher_id": "beta", "signer": beta[0]},
        ],
        _roster(root, {"acme": acme[1], "beta": beta[1]}),
    )
    client = _client(directory, root, tmp_path)
    assert _install(client, "figures", tmp_path).is_file()
    assert _install(client, "decks", tmp_path).is_file()


def test_a_creator_not_on_the_roster_is_refused(tmp_path, root, acme):
    stranger = signing.generate_keypair()
    directory = _registry(
        tmp_path, root, [{"id": "evil", "publisher_id": "stranger", "signer": stranger[0]}],
        _roster(root, {"acme": acme[1]}),
    )
    with pytest.raises(BundleError) as e:
        _install(_client(directory, root, tmp_path), "evil", tmp_path)
    assert "not a trusted creator" in str(e.value)


def test_a_revoked_creators_bundle_is_refused(tmp_path, root, acme):
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": acme[0]}],
        _roster(root, {"acme": acme[1]}, revoked=["acme"]),
    )
    with pytest.raises(BundleError) as e:
        _install(_client(directory, root, tmp_path), "figures", tmp_path)
    assert "not a trusted creator" in str(e.value)


def test_a_bundle_signed_with_the_wrong_key_is_refused(tmp_path, root, acme):
    """Claiming to be a trusted creator is free; signing as one is not."""
    impostor = signing.generate_keypair()
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": impostor[0]}],
        _roster(root, {"acme": acme[1]}),
    )
    with pytest.raises(BundleError) as e:
        _install(_client(directory, root, tmp_path), "figures", tmp_path)
    assert "INVALID" in str(e.value)


def test_a_tampered_roster_refuses_the_WHOLE_registry(tmp_path, root, acme):
    """Not just the affected bundle. A roster that does not verify means we know nothing about who
    anyone is, and a store rendering cards from a listing it has already distrusted will happily
    offer the install."""
    attacker = signing.generate_keypair()
    block = _roster(root, {"acme": acme[1]})
    block["roster"][0]["key"] = attacker[1]
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": attacker[0]}], block
    )
    with pytest.raises(BundleError) as e:
        asyncio.run(_client(directory, root, tmp_path).fetch_index())
    assert "pinned platform key" in str(e.value)


def test_schema_2_with_no_roster_at_all_is_refused(tmp_path, root):
    directory = _registry(tmp_path, root, [{"id": "a"}], {})
    with pytest.raises(BundleError):
        asyncio.run(_client(directory, root, tmp_path).fetch_index())


def test_a_legacy_unstamped_entry_still_verifies_against_the_pinned_key(tmp_path, root, acme):
    """The migration path: make the old single publisher key the root key, and entries carried over
    from schema 1 (no publisher_id) keep verifying against it. Nothing already published needs
    re-signing on the day the second creator arrives."""
    directory = _registry(
        tmp_path, root,
        [
            {"id": "legacy", "signer": root[0]},  # no publisher_id — pre-roster
            {"id": "figures", "publisher_id": "acme", "signer": acme[0]},
        ],
        _roster(root, {"acme": acme[1]}),
    )
    client = _client(directory, root, tmp_path)
    assert _install(client, "legacy", tmp_path).is_file()
    assert _install(client, "figures", tmp_path).is_file()


def test_an_unsigned_bundle_is_refused_when_a_key_is_pinned(tmp_path, root, acme):
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme"}], _roster(root, {"acme": acme[1]})
    )
    with pytest.raises(BundleError) as e:
        _install(_client(directory, root, tmp_path), "figures", tmp_path)
    assert "unsigned artifact" in str(e.value)


# ─────────────────────────── replay ───────────────────────────


def test_a_replayed_older_roster_is_refused(tmp_path, root, acme):
    """Signatures make forgery impossible and do nothing about replay. Serving last month's index
    verbatim would quietly un-revoke a creator revoked yesterday."""
    current = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": acme[0]}],
        _roster(root, {"acme": acme[1]}, revoked=["acme"], issued="2026-08-08T10:00:00Z"),
    )
    client = _client(current, root, tmp_path)
    with pytest.raises(BundleError):  # revoked, as expected
        _install(client, "figures", tmp_path)

    # …now the registry is rewritten with the OLDER roster, from before the revocation.
    index = json.loads((current / "index.json").read_text(encoding="utf-8"))
    index["publishers"] = _roster(root, {"acme": acme[1]}, issued="2026-07-01T10:00:00Z")
    (current / "index.json").write_text(json.dumps(index), encoding="utf-8")

    with pytest.raises(BundleError) as e:
        asyncio.run(_client(current, root, tmp_path).fetch_index())
    assert "OLDER than one already accepted" in str(e.value)


def test_a_newer_roster_is_accepted_and_remembered(tmp_path, root, acme):
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": acme[0]}],
        _roster(root, {"acme": acme[1]}, issued="2026-08-08T10:00:00Z"),
    )
    asyncio.run(_client(directory, root, tmp_path).fetch_index())
    index = json.loads((directory / "index.json").read_text(encoding="utf-8"))
    index["publishers"] = _roster(root, {"acme": acme[1]}, issued="2026-09-01T10:00:00Z")
    (directory / "index.json").write_text(json.dumps(index), encoding="utf-8")
    asyncio.run(_client(directory, root, tmp_path).fetch_index())  # must not raise

    memory = RosterMemory(tmp_path / "trust.json")
    assert memory.newest_seen(str((directory / "index.json").resolve().as_uri())) == "2026-09-01T10:00:00Z"


def test_the_memory_survives_a_new_process(tmp_path):
    path = tmp_path / "trust.json"
    RosterMemory(path).remember("https://r/index.json", ISSUED)
    assert RosterMemory(path).newest_seen("https://r/index.json") == ISSUED


def test_an_unwritable_memory_does_not_break_installs(tmp_path):
    """Losing a replay bound is bad. Refusing to install anything because a cache file is
    unwritable is worse."""
    memory = RosterMemory(tmp_path / "nope" / "deep" / "trust.json")
    memory.remember("https://r/index.json", ISSUED)  # must not raise
    assert memory.is_downgrade("https://r/index.json", "2020-01-01T00:00:00Z") is True


def test_an_unpinned_install_does_not_record_a_roster_date(tmp_path, root, acme):
    """A dev/unpinned client verifies nothing, so remembering a roster date it never checked would
    only make a LATER pinned run refuse a legitimate registry."""
    directory = _registry(
        tmp_path, root, [{"id": "figures", "publisher_id": "acme", "signer": acme[0]}],
        _roster(root, {"acme": acme[1]}),
    )
    client = RegistryClient(str(directory), pinned_publisher_key="", trust_state_path=tmp_path / "t.json")
    asyncio.run(client.fetch_index())
    assert not (tmp_path / "t.json").exists()
