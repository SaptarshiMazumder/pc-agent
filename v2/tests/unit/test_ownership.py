"""Ownership as DATA (domain/ownership.py + ownership_store) — Step 2 of the identity plan.

Step 1 derived "is this agent the caller's" from folder layout; these pin the replacement: a
record the runtime writes at create/install, one membership rule for every deployment shape,
legacy dirs meaning exactly what they meant before, and the record never traveling inside a
published bundle (ownership of a copy is decided where the copy lands).
"""

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.domain import ownership
from agent_runtime.infrastructure.agents import ownership_store

# ─────────────────────────── the rules (pure) ───────────────────────────


def test_a_desktop_keeps_local_alongside_a_signed_in_account():
    """THE game-master rule: signing in on your own machine adds an identity, it never
    subtracts one — the agents that were yours signed-out stay yours signed-in."""
    assert ownership.callers("acct_a", hosted=False) == {"acct_a", "local"}
    assert ownership.owned_by_caller("local", "acct_a", hosted=False)


def test_a_hosted_stranger_acts_only_as_their_account():
    assert ownership.callers("acct_a", hosted=True) == {"acct_a"}
    assert not ownership.owned_by_caller("local", "acct_a", hosted=True)
    assert not ownership.owned_by_caller("platform", "acct_a", hosted=True)


def test_the_hosted_operator_is_the_platform():
    """Machine token, no account, on a hosted daemon: the deployment acting on itself."""
    assert ownership.callers(None, hosted=True) == {"platform"}


def test_a_platform_install_is_curation_by_definition():
    """One derivation, so a seed job cannot forget to say `curated`."""
    assert ownership.origin_for_install("platform") == "curated"
    assert ownership.origin_for_install("acct_a") == "installed"
    assert ownership.origin_for_install("local") == "installed"


def test_presumed_owner_restates_the_old_layer_rule():
    """A record-less dir behaves exactly as Step 1 left it — shared belongs to the deployment
    (platform when hosted, the local owner otherwise), an overlay to its account."""
    assert ownership.presumed_owner(in_overlay=False, account_id=None, hosted=False) == "local"
    assert ownership.presumed_owner(in_overlay=False, account_id="acct_a", hosted=True) == "platform"
    assert ownership.presumed_owner(in_overlay=True, account_id="acct_a", hosted=True) == "acct_a"


def test_a_half_readable_record_degrades_to_no_record():
    """Never to a crash, and never to an owner nobody is."""
    assert ownership.parse_record(None) is None
    assert ownership.parse_record("garbage") is None
    assert ownership.parse_record({}) is None
    assert ownership.parse_record({"owner": "  "}) is None
    junk = ownership.parse_record({"owner": "acct_a", "origin": "stolen", "source": "nope"})
    assert junk == ownership.OwnershipRecord(owner="acct_a", origin="authored")


# ─────────────────────────── the store (IO) ───────────────────────────


def test_roundtrip(tmp_path):
    ownership_store.stamp_install(tmp_path, "acct_a", False, source_id="demo", source_version="1.2")
    record = ownership_store.read(tmp_path)
    assert record == ownership.OwnershipRecord("acct_a", "installed", "demo", "1.2")
    # and the on-disk shape is the documented one, not an accident of dataclass naming
    raw = json.loads((tmp_path / ".agentd-meta.json").read_text(encoding="utf-8"))
    assert raw == {"owner": "acct_a", "origin": "installed", "source": {"id": "demo", "version": "1.2"}}


def test_an_authored_stamp_carries_no_source(tmp_path):
    ownership_store.stamp_create(tmp_path, None, hosted=False)
    raw = json.loads((tmp_path / ".agentd-meta.json").read_text(encoding="utf-8"))
    assert raw == {"owner": "local", "origin": "authored"}


def test_a_hosted_no_account_install_stamps_curated(tmp_path):
    """The docker seed job and a web-app sync run with no account: that IS curation."""
    ownership_store.stamp_install(tmp_path, None, True, source_id="w", source_version="1")
    record = ownership_store.read(tmp_path)
    assert (record.owner, record.origin) == ("platform", "curated")


def test_a_missing_or_corrupt_file_reads_as_none(tmp_path):
    assert ownership_store.read(tmp_path) is None
    assert ownership_store.read(None) is None
    (tmp_path / ".agentd-meta.json").write_text("{not json", encoding="utf-8")
    assert ownership_store.read(tmp_path) is None


# ─────────────────────────── the record never ships ───────────────────────────


def test_the_packer_excludes_the_record(tmp_path):
    """A bundle carries the AUTHOR's files. Shipping the author's record would make every
    install claim the author's identity — the installer stamps a fresh record on arrival."""
    from agent_runtime.infrastructure.marketplace import bundle_io

    (tmp_path / "agent.toml").write_text('name = "Demo"\n', encoding="utf-8")
    ownership_store.stamp_create(tmp_path, "acct_author", hosted=False)

    packed = [p.name for p in bundle_io._iter_files(tmp_path)]
    assert "agent.toml" in packed
    assert ".agentd-meta.json" not in packed


def test_the_validators_file_listing_excludes_it_too(tmp_path):
    """agent-builder's validator describes what the package WILL contain."""
    from agent_authoring.infrastructure.agent_dir_reader import AgentDirReader

    (tmp_path / "agent.toml").write_text('name = "Demo"\n', encoding="utf-8")
    ownership_store.stamp_create(tmp_path, "acct_author", hosted=False)

    files = AgentDirReader(registry=None).files(tmp_path)
    assert files == ["agent.toml"]
