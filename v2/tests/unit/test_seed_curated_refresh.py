"""Seeding shipped default agents: OURS refresh on upgrade, THEIRS are never overwritten.

The failure this pins: the seeded agent-builder kept its first-ever files forever ("never
overwrite anything"), so an engine upgrade ran a new daemon under the previous install's
sign-in gate — which spoke a protocol the daemon no longer had, and every login ended in
"signed in, but this device did not activate". Curated agents are the platform's (their
ownership record says so), and the platform updating its own files is what an upgrade IS.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.cli import first_run
from agent_runtime.domain import ownership
from agent_runtime.infrastructure.agents import ownership_store


@pytest.fixture
def world(tmp_path, monkeypatch):
    starter = tmp_path / "wheel" / "agents"
    home = tmp_path / "home"
    (home / "agents").mkdir(parents=True)

    # the shipped tree: one curated agent + main's shared skills library
    builder = starter / "agent-builder"
    (builder / "ui" / "vendor").mkdir(parents=True)
    (builder / "agent.toml").write_text('name = "Agent Builder"\n', encoding="utf-8")
    (builder / "ui" / "vendor" / "sdk.js").write_text("v2-NEW-SDK", encoding="utf-8")
    skills = starter / "main" / "skills"
    skills.mkdir(parents=True)
    (skills / "web.md").write_text("shipped skill", encoding="utf-8")

    monkeypatch.setattr(first_run.runtime_paths, "ensure_user_layout", lambda: home)
    monkeypatch.setattr(first_run.runtime_paths, "packaged_soul_file", lambda: starter / "nope")
    monkeypatch.setattr(first_run.runtime_paths, "packaged_starter_agents_dir", lambda: starter)
    monkeypatch.setattr(first_run.runtime_paths, "is_packaged", lambda: True)
    return home, starter


def test_a_fresh_seed_installs_and_stamps(world):
    home, _ = world
    first_run.seed_user_layout()
    dst = home / "agents" / "agent-builder"
    assert (dst / "ui" / "vendor" / "sdk.js").read_text(encoding="utf-8") == "v2-NEW-SDK"
    record = ownership_store.read(dst)
    assert (record.owner, record.origin) == ("platform", "curated")


def test_an_upgrade_refreshes_our_agent_but_not_its_workspace(world):
    """THE ancient-SDK bug. The old copy is curated (ours), so the new wheel's files replace
    it — while workspace/, the user's data inside our agent, survives byte-for-byte."""
    home, _ = world
    dst = home / "agents" / "agent-builder"
    (dst / "ui" / "vendor").mkdir(parents=True)
    (dst / "ui" / "vendor" / "sdk.js").write_text("v1-ANCIENT-SDK", encoding="utf-8")
    (dst / "workspace").mkdir()
    (dst / "workspace" / "draft.md").write_text("the user's work", encoding="utf-8")
    ownership_store.write(
        dst, ownership.OwnershipRecord(owner=ownership.PLATFORM_OWNER, origin=ownership.CURATED)
    )

    first_run.seed_user_layout()

    assert (dst / "ui" / "vendor" / "sdk.js").read_text(encoding="utf-8") == "v2-NEW-SDK"
    assert (dst / "workspace" / "draft.md").read_text(encoding="utf-8") == "the user's work"


def test_a_recordless_copy_is_left_alone(world):
    """A pre-record install might carry user edits nobody can prove are ours — additive only,
    exactly the old behavior. (Such installs need one manual reseed; refreshes work forever
    after, because the reseed stamps the record.)"""
    home, _ = world
    dst = home / "agents" / "agent-builder"
    (dst / "ui" / "vendor").mkdir(parents=True)
    (dst / "ui" / "vendor" / "sdk.js").write_text("v1-ANCIENT-SDK", encoding="utf-8")

    first_run.seed_user_layout()

    assert (dst / "ui" / "vendor" / "sdk.js").read_text(encoding="utf-8") == "v1-ANCIENT-SDK"
    assert ownership_store.read(dst) is None, "not stamped — it was never proven ours"


def test_a_claimed_copy_is_never_refreshed(world):
    """owner=an account: the user forked our agent under the same id. Their files, their rules."""
    home, _ = world
    dst = home / "agents" / "agent-builder"
    dst.mkdir(parents=True)
    (dst / "agent.toml").write_text('name = "My Fork"\n', encoding="utf-8")
    ownership_store.write(dst, ownership.OwnershipRecord(owner="acct_a", origin="authored"))

    first_run.seed_user_layout()

    assert (dst / "agent.toml").read_text(encoding="utf-8") == 'name = "My Fork"\n'
    assert ownership_store.read(dst).owner == "acct_a"


def test_the_shared_skills_library_stays_additive(world):
    """main/skills is the USER's tree: their edits win, new shipped skills still arrive."""
    home, starter = world
    lib = home / "agents" / "main" / "skills"
    lib.mkdir(parents=True)
    (lib / "web.md").write_text("MY edited skill", encoding="utf-8")
    (starter / "main" / "skills" / "new.md").write_text("newly shipped", encoding="utf-8")

    first_run.seed_user_layout()

    assert (lib / "web.md").read_text(encoding="utf-8") == "MY edited skill"
    assert (lib / "new.md").read_text(encoding="utf-8") == "newly shipped"
