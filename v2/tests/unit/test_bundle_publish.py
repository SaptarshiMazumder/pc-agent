"""`agentd bundle publish` — the release command, and the four ways it refuses.

Publishing is a read-modify-write of a SIGNED index, not an upload, so the tests that matter are
not "does a file arrive" but "does it refuse when proceeding would break something already out
there". Each refusal below corresponds to a failure that is invisible at publish time and only
surfaces on a stranger's machine:

  * rebuilding the index from just this run's packages    -> every other agent silently unpublished
  * publishing unsigned over a signed registry            -> pinned clients reject everything
  * publishing with a different key                       -> same, and it looks like corruption
  * a corrupt existing index treated as "empty"           -> the first failure, from the other side
"""

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.cli.commands import bundle as bundle_cli
from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace.index_builder import build_index


def _agent(tmp_path: Path, bundle_id: str, version: str = "1.0.0") -> Path:
    agent_dir = tmp_path / "agents" / bundle_id
    agent_dir.mkdir(parents=True)
    (agent_dir / "bundle.toml").write_text(
        "[bundle]\n"
        f'id = "{bundle_id}"\n'
        f'name = "{bundle_id}"\n'
        f'version = "{version}"\n'
        'description = "a test agent"\n',
        encoding="utf-8",
    )
    (agent_dir / "IDENTITY.md").write_text("# test agent\n", encoding="utf-8")
    return agent_dir


def _keypair(tmp_path: Path, name: str = "key.json") -> tuple[Path, str]:
    """-> (keypair file, public key b64)."""
    private_b64, public_b64 = signing.generate_keypair()
    path = tmp_path / name
    path.write_text(
        json.dumps({"private_key": private_b64, "public_key": public_b64}), encoding="utf-8"
    )
    return path, public_b64


def _args(**over) -> argparse.Namespace:
    base = {
        "agent_dir": [],
        "to": "",
        "key": "",
        "name": "",
        "publisher": "",
        "version": "",
        "rotate_key": False,
        "unsigned": False,
        "dry_run": False,
    }
    base.update(over)
    return argparse.Namespace(**base)


@pytest.fixture(autouse=True)
def _no_publisher_env(monkeypatch):
    """The command falls back to env for both target and key; a developer's real values in the
    environment would make these tests publish somewhere real."""
    monkeypatch.delenv("AGENTD_PUBLISH_TARGET", raising=False)
    monkeypatch.delenv("AGENTD_PUBLISHER_KEYFILE", raising=False)


def _index(registry: Path) -> dict:
    return json.loads((registry / "index.json").read_text(encoding="utf-8"))


# ─────────────────────────── the happy path ───────────────────────────


def test_publishes_a_signed_registry_to_a_directory(tmp_path):
    agent = _agent(tmp_path, "alpha")
    key, public_b64 = _keypair(tmp_path)
    registry = tmp_path / "registry"

    assert bundle_cli.run_publish(_args(agent_dir=[str(agent)], to=str(registry), key=str(key))) == 0

    index = _index(registry)
    assert index["publisher_key"] == public_b64
    assert [b["id"] for b in index["bundles"]] == ["alpha"]
    entry = index["bundles"][0]
    assert (registry / entry["url"]).is_file()
    # The signature covers the sha256, which is what makes a rewritten index.json detectable.
    assert signing.verify(public_b64, entry["sha256"].encode("ascii"), entry["sig"])


def test_second_publish_carries_the_first_forward(tmp_path):
    """THE regression this command exists for: publishing beta must not unpublish alpha."""
    key, public_b64 = _keypair(tmp_path)
    registry = tmp_path / "registry"

    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(key))
        )
        == 0
    )
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "beta"))], to=str(registry), key=str(key))
        )
        == 0
    )

    index = _index(registry)
    assert sorted(b["id"] for b in index["bundles"]) == ["alpha", "beta"]
    # alpha's artifact is still there and still verifies — it was carried by its recorded digest,
    # never re-read, so this also proves carrying needs no access to the original file.
    alpha = next(b for b in index["bundles"] if b["id"] == "alpha")
    assert (registry / alpha["url"]).is_file()
    assert signing.verify(public_b64, alpha["sha256"].encode("ascii"), alpha["sig"])


def test_republishing_the_same_id_replaces_its_entry(tmp_path):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    agent = _agent(tmp_path, "alpha")

    assert bundle_cli.run_publish(_args(agent_dir=[str(agent)], to=str(registry), key=str(key))) == 0
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(agent)], to=str(registry), key=str(key), version="2.0.0")
        )
        == 0
    )

    bundles = _index(registry)["bundles"]
    assert [(b["id"], b["version"]) for b in bundles] == [("alpha", "2.0.0")]


def test_publishes_multiple_agents_in_one_run(tmp_path):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    dirs = [str(_agent(tmp_path, "alpha")), str(_agent(tmp_path, "beta"))]

    assert bundle_cli.run_publish(_args(agent_dir=dirs, to=str(registry), key=str(key))) == 0
    assert sorted(b["id"] for b in _index(registry)["bundles"]) == ["alpha", "beta"]


def test_unsigned_publish_is_allowed_when_asked_for(tmp_path):
    registry = tmp_path / "registry"
    args = _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), unsigned=True)

    assert bundle_cli.run_publish(args) == 0
    index = _index(registry)
    assert index["publisher_key"] == ""
    assert "sig" not in index["bundles"][0]


def test_dry_run_writes_nothing(tmp_path):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    args = _args(
        agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(key), dry_run=True
    )

    assert bundle_cli.run_publish(args) == 0
    assert not registry.exists()


def test_keeps_the_registry_name_when_not_given(tmp_path):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    first = _args(
        agent_dir=[str(_agent(tmp_path, "alpha"))],
        to=str(registry),
        key=str(key),
        name="agentd marketplace",
        publisher="agentd",
    )
    assert bundle_cli.run_publish(first) == 0

    # Publishing again WITHOUT --name must not rename the marketplace to the temp dir's name.
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "beta"))], to=str(registry), key=str(key))
        )
        == 0
    )
    index = _index(registry)
    assert index["name"] == "agentd marketplace" and index["publisher"] == "agentd"


# ─────────────────────────── the refusals ───────────────────────────


def test_refuses_without_a_signing_key(tmp_path, capsys):
    args = _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(tmp_path / "registry"))

    assert bundle_cli.run_publish(args) == 1
    assert "refusing to publish without a signing key" in capsys.readouterr().out


def test_refuses_without_a_target(tmp_path, capsys):
    key, _public = _keypair(tmp_path)
    assert bundle_cli.run_publish(_args(agent_dir=[str(_agent(tmp_path, "alpha"))], key=str(key))) == 1
    assert "no registry target" in capsys.readouterr().out


def test_refuses_to_unsign_a_signed_registry(tmp_path, capsys):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(key))
        )
        == 0
    )

    args = _args(agent_dir=[str(_agent(tmp_path, "beta"))], to=str(registry), unsigned=True)
    assert bundle_cli.run_publish(args) == 1
    assert "is SIGNED" in capsys.readouterr().out
    assert [b["id"] for b in _index(registry)["bundles"]] == ["alpha"]  # untouched


def test_refuses_a_different_key_without_rotate(tmp_path, capsys):
    first_key, _first_public = _keypair(tmp_path, "first.json")
    other_key, other_public = _keypair(tmp_path, "other.json")
    registry = tmp_path / "registry"
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(first_key))
        )
        == 0
    )

    args = _args(agent_dir=[str(_agent(tmp_path, "beta"))], to=str(registry), key=str(other_key))
    assert bundle_cli.run_publish(args) == 1
    assert "KEY MISMATCH" in capsys.readouterr().out
    assert _index(registry)["publisher_key"] != other_public  # nothing was rewritten


def test_rotate_key_re_signs_the_whole_registry(tmp_path):
    """Rotation must leave NO entry only the old key can verify — including carried ones."""
    first_key, _first_public = _keypair(tmp_path, "first.json")
    new_key, new_public = _keypair(tmp_path, "new.json")
    registry = tmp_path / "registry"
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(first_key))
        )
        == 0
    )

    args = _args(
        agent_dir=[str(_agent(tmp_path, "beta"))],
        to=str(registry),
        key=str(new_key),
        rotate_key=True,
    )
    assert bundle_cli.run_publish(args) == 0

    index = _index(registry)
    assert index["publisher_key"] == new_public
    assert len(index["bundles"]) == 2
    for entry in index["bundles"]:
        assert signing.verify(new_public, entry["sha256"].encode("ascii"), entry["sig"]), entry["id"]


def test_refuses_a_corrupt_existing_index(tmp_path, capsys):
    key, _public = _keypair(tmp_path)
    registry = tmp_path / "registry"
    registry.mkdir()
    (registry / "index.json").write_text("{not json", encoding="utf-8")

    args = _args(agent_dir=[str(_agent(tmp_path, "alpha"))], to=str(registry), key=str(key))
    assert bundle_cli.run_publish(args) == 1
    out = capsys.readouterr().out
    assert "not valid JSON" in out and "unpublish" in out
    # The unreadable file is left exactly as found — clobbering it is the thing being prevented.
    assert (registry / "index.json").read_text(encoding="utf-8") == "{not json"


def test_refuses_a_missing_agent_directory(tmp_path, capsys):
    key, _public = _keypair(tmp_path)
    args = _args(
        agent_dir=[str(tmp_path / "nope")], to=str(tmp_path / "registry"), key=str(key)
    )
    assert bundle_cli.run_publish(args) == 1
    assert "not a directory" in capsys.readouterr().out


# ─────────────────────────── target parsing ───────────────────────────


@pytest.mark.parametrize(
    ("target", "expected"),
    [
        ("s3://bucket", ("bucket", "")),
        ("s3://bucket/", ("bucket", "")),
        ("s3://bucket/prefix", ("bucket", "prefix")),
        ("s3://bucket/prefix/", ("bucket", "prefix")),
        ("s3://bucket/a/b", ("bucket", "a/b")),
    ],
)
def test_s3_target_parsing(target, expected):
    assert bundle_cli._split_s3(target) == expected


def test_s3_uri_joins_without_double_slashes():
    assert bundle_cli._s3_uri("b", "", "index.json") == "s3://b/index.json"
    assert bundle_cli._s3_uri("b", "p", "index.json") == "s3://b/p/index.json"


# ─────────────────────── carrying, at the builder level ───────────────────────


def test_carry_drops_entries_that_cannot_be_signed(tmp_path, caplog):
    """A prior entry is carried on its recorded digest alone, so an entry without one cannot be
    carried — and must be dropped loudly rather than written into the index as garbage."""
    staging = tmp_path / "staging"
    staging.mkdir()
    private_b64, public_b64 = signing.generate_keypair()

    index_path = build_index(
        staging,
        private_key_b64=private_b64,
        public_key_b64=public_b64,
        carry_entries=(
            {"id": "good", "version": "1.0.0", "url": "good-1.0.0.agentpkg", "sha256": "ab" * 32},
            {"id": "no-digest", "version": "1.0.0", "url": "x.agentpkg"},
            {"id": "no-url", "version": "1.0.0", "sha256": "cd" * 32},
            {"id": "", "version": "1.0.0", "url": "y.agentpkg", "sha256": "ef" * 32},
            "not even a dict",
        ),
    )

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert [b["id"] for b in index["bundles"]] == ["good"]
    assert signing.verify(public_b64, ("ab" * 32).encode("ascii"), index["bundles"][0]["sig"])


def test_unsigned_rebuild_strips_carried_signatures(tmp_path):
    """An index with no publisher_key must not carry signatures it cannot vouch for."""
    staging = tmp_path / "staging"
    staging.mkdir()

    index_path = build_index(
        staging,
        carry_entries=(
            {
                "id": "good",
                "version": "1.0.0",
                "url": "good-1.0.0.agentpkg",
                "sha256": "ab" * 32,
                "sig": "whatever",
            },
        ),
    )

    index = json.loads(index_path.read_text(encoding="utf-8"))
    assert index["publisher_key"] == ""
    assert "sig" not in index["bundles"][0]
