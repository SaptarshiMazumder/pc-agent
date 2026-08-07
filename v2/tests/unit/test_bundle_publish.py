"""`agentd bundle publish` — the release command, and the ways it refuses.

Publishing is a read-modify-write of a SIGNED index, not an upload, so the tests that matter are
not "does a file arrive" but "does it refuse when proceeding would break something already out
there". Each refusal below corresponds to a failure that is invisible at publish time and only
surfaces on a stranger's machine:

  * rebuilding the index from just this run's packages    -> every other agent silently unpublished
  * publishing unsigned over a signed registry            -> pinned clients reject everything
  * publishing with a different key                       -> same, and it looks like corruption
  * a corrupt existing index treated as "empty"           -> the first failure, from the other side

The last section covers the multi-creator (schema 2) registry, where the same class of mistake has
more ways to happen — an unlisted creator, a revoked one, a listed creator using a key the roster
does not know — and all of them produce that identical symptom: the store lists it, the install
fails.
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
        "publisher_id": "",
        "roster": "",
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


# ─────────────────────────── multi-creator publishing (schema 2) ───────────────────────────
#
# The schema-1 refusals above all protect ONE key. These protect the case the roster exists for:
# several creators publishing into one registry, none of them holding the platform's root key, and
# every way of getting it wrong producing the same symptom — the store lists the bundle and every
# download fails signature verification.


def _roster_file(tmp_path: Path, root_private: str, root_public: str, creators: dict) -> Path:
    from agent_runtime.infrastructure.marketplace import roster_builder

    block = roster_builder.build_roster(
        [{"id": cid, "name": cid, "key": key, "added": "2026-08-08T00:00:00Z"} for cid, key in creators.items()],
        [],
        "2026-08-08T00:00:00Z",
        root_private,
        root_public,
    )
    path = tmp_path / "registry-roster.json"
    roster_builder.write_roster_file(path, block)
    return path


def test_a_creator_publishes_a_schema_2_index(tmp_path):
    agent = _agent(tmp_path, "figures")
    creator_key, creator_public = _keypair(tmp_path, "acme.json")
    root_private, root_public = signing.generate_keypair()
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": creator_public})
    registry = tmp_path / "registry"

    assert (
        bundle_cli.run_publish(
            _args(
                agent_dir=[str(agent)], to=str(registry), key=str(creator_key),
                publisher_id="acme", roster=str(roster),
            )
        )
        == 0
    )

    index = _index(registry)
    assert index["schema"] == 2
    # Clients pin the ROOT key, not the creator's — that is what stops a new creator needing a new
    # build of every installed app.
    assert index["publisher_key"] == root_public
    assert index["publishers"]["roster"][0]["id"] == "acme"
    entry = index["bundles"][0]
    assert entry["publisher_id"] == "acme"
    assert signing.verify(creator_public, entry["sha256"].encode("ascii"), entry["sig"])


def test_a_second_creator_does_not_re_sign_the_first_ones_bundle(tmp_path):
    """The bug this prevents is total: schema 1 re-signed every carried entry with the publishing
    key, which in a multi-creator registry would invalidate every other creator's bundles on each
    publish — and the publisher would have no way to notice."""
    root_private, root_public = signing.generate_keypair()
    acme_key, acme_public = _keypair(tmp_path, "acme.json")
    beta_key, beta_public = _keypair(tmp_path, "beta.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public, "beta": beta_public})
    registry = tmp_path / "registry"

    bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(registry),
              key=str(acme_key), publisher_id="acme", roster=str(roster))
    )
    acme_sig = _index(registry)["bundles"][0]["sig"]

    bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "decks"))], to=str(registry),
              key=str(beta_key), publisher_id="beta", roster=str(roster))
    )

    rows = {b["id"]: b for b in _index(registry)["bundles"]}
    assert rows["figures"]["sig"] == acme_sig  # untouched by beta's publish
    assert signing.verify(acme_public, rows["figures"]["sha256"].encode("ascii"), rows["figures"]["sig"])
    assert signing.verify(beta_public, rows["decks"]["sha256"].encode("ascii"), rows["decks"]["sig"])


def test_the_roster_is_carried_forward_when_not_passed(tmp_path):
    """Omitting --roster on a registry that has one would demote it back to a single key and
    silently un-trust every other creator."""
    root_private, root_public = signing.generate_keypair()
    acme_key, acme_public = _keypair(tmp_path, "acme.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})
    registry = tmp_path / "registry"

    bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(registry),
              key=str(acme_key), publisher_id="acme", roster=str(roster))
    )
    assert (
        bundle_cli.run_publish(
            _args(agent_dir=[str(_agent(tmp_path, "decks"))], to=str(registry),
                  key=str(acme_key), publisher_id="acme")  # no --roster
        )
        == 0
    )
    index = _index(registry)
    assert index["schema"] == 2
    assert index["publishers"]["roster"][0]["id"] == "acme"


def test_refuses_a_creator_who_is_not_on_the_roster(tmp_path, capsys):
    root_private, root_public = signing.generate_keypair()
    acme_key, acme_public = _keypair(tmp_path, "acme.json")
    stranger_key, _ = _keypair(tmp_path, "stranger.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})

    code = bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "evil"))], to=str(tmp_path / "registry"),
              key=str(stranger_key), publisher_id="stranger", roster=str(roster))
    )
    assert code == 1
    assert "not on this registry's roster" in capsys.readouterr().out


def test_refuses_a_creator_publishing_with_a_key_the_roster_does_not_know(tmp_path, capsys):
    root_private, root_public = signing.generate_keypair()
    _, listed_public = _keypair(tmp_path, "listed.json")
    other_key, _ = _keypair(tmp_path, "other.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": listed_public})

    code = bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(tmp_path / "registry"),
              key=str(other_key), publisher_id="acme", roster=str(roster))
    )
    assert code == 1
    assert "KEY MISMATCH for creator 'acme'" in capsys.readouterr().out


def test_refuses_publishing_to_a_roster_registry_without_a_publisher_id(tmp_path, capsys):
    root_private, root_public = signing.generate_keypair()
    acme_key, acme_public = _keypair(tmp_path, "acme.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})

    code = bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(tmp_path / "registry"),
              key=str(acme_key), roster=str(roster))
    )
    assert code == 1
    assert "--publisher-id is required" in capsys.readouterr().out


def test_refuses_an_unsigned_publish_to_a_roster_registry(tmp_path, capsys):
    root_private, root_public = signing.generate_keypair()
    _, acme_public = _keypair(tmp_path, "acme.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})

    code = bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(tmp_path / "registry"),
              unsigned=True, publisher_id="acme", roster=str(roster))
    )
    assert code == 1
    assert "must be SIGNED with your own key" in capsys.readouterr().out


def test_refuses_a_roster_file_that_cannot_be_read(tmp_path, capsys):
    code = bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(tmp_path / "registry"),
              key=str(_keypair(tmp_path)[0]), publisher_id="acme", roster=str(tmp_path / "missing.json"))
    )
    assert code == 1
    assert "cannot read roster" in capsys.readouterr().out


# ─────────────────────────── publishing the roster on its own ───────────────────────────
#
# A roster edit only takes effect once the registry serves it. Adding a creator can ride along with
# their first publish; a REVOCATION cannot, because the creator being revoked is precisely the
# person who can no longer publish. Without this command a revocation is unenforceable — which is
# how the gap was found: revoke, publish, and the bundle still installed.


def _roster_args(**over) -> argparse.Namespace:
    base = {"to": "", "file": "", "dry_run": False}
    base.update(over)
    return argparse.Namespace(**base)


def _published_registry(tmp_path: Path):
    """-> (registry dir, root private, root public, acme keypair path, acme public)."""
    root_private, root_public = signing.generate_keypair()
    acme_key, acme_public = _keypair(tmp_path, "acme.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})
    registry = tmp_path / "registry"
    bundle_cli.run_publish(
        _args(agent_dir=[str(_agent(tmp_path, "figures"))], to=str(registry),
              key=str(acme_key), publisher_id="acme", roster=str(roster))
    )
    return registry, root_private, root_public, roster


def test_publishing_a_revocation_takes_effect_without_touching_bundles(tmp_path, capsys):
    from agent_runtime.infrastructure.marketplace import roster_builder

    registry, root_private, root_public, roster = _published_registry(tmp_path)
    before = _index(registry)["bundles"]

    roster_builder.write_roster_file(
        roster,
        roster_builder.without_publisher(
            roster_builder.read_roster_file(roster), publisher_id="acme",
            issued="2026-09-01T00:00:00Z", root_private_b64=root_private, root_public_b64=root_public,
        ),
    )
    assert bundle_cli.run_roster_publish(_roster_args(to=str(registry), file=str(roster))) == 0

    after = _index(registry)
    assert after["publishers"]["revoked"] == ["acme"]
    assert after["bundles"] == before  # the artifacts and their signatures are untouched
    # The store still LISTS the bundle, so the user-visible failure is a download that stops
    # working. Saying so at publish time is the difference between a decision and a surprise.
    assert "will stop installing" in capsys.readouterr().out


def test_refuses_a_roster_that_goes_backwards(tmp_path, capsys):
    """Clients reject a roster older than one they have accepted, so publishing one would break the
    store for everyone who already saw the newer one — and leave it broken until someone noticed."""
    registry, root_private, root_public, _ = _published_registry(tmp_path)
    _, acme_public = _keypair(tmp_path, "acme.json")
    stale = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})
    json.loads(stale.read_text(encoding="utf-8"))  # sanity: readable
    current = _index(registry)["publishers"]["issued"]

    from agent_runtime.infrastructure.marketplace import roster_builder

    block = roster_builder.read_roster_file(stale)
    block["issued"] = "2020-01-01T00:00:00Z"
    block = roster_builder.build_roster(
        block["roster"], block["revoked"], block["issued"], root_private, root_public
    )
    roster_builder.write_roster_file(stale, block)

    assert bundle_cli.run_roster_publish(_roster_args(to=str(registry), file=str(stale))) == 1
    assert "OLDER than the one the registry already serves" in capsys.readouterr().out
    assert _index(registry)["publishers"]["issued"] == current


def test_refuses_to_publish_a_roster_to_an_empty_target(tmp_path, capsys):
    root_private, root_public = signing.generate_keypair()
    _, acme_public = _keypair(tmp_path, "acme.json")
    roster = _roster_file(tmp_path, root_private, root_public, {"acme": acme_public})

    assert bundle_cli.run_roster_publish(
        _roster_args(to=str(tmp_path / "nothing-here"), file=str(roster))
    ) == 1
    assert "publish a bundle first" in capsys.readouterr().out


def test_a_roster_dry_run_changes_nothing(tmp_path):
    from agent_runtime.infrastructure.marketplace import roster_builder

    registry, root_private, root_public, roster = _published_registry(tmp_path)
    before = _index(registry)
    roster_builder.write_roster_file(
        roster,
        roster_builder.without_publisher(
            roster_builder.read_roster_file(roster), publisher_id="acme",
            issued="2026-09-01T00:00:00Z", root_private_b64=root_private, root_public_b64=root_public,
        ),
    )
    assert bundle_cli.run_roster_publish(
        _roster_args(to=str(registry), file=str(roster), dry_run=True)
    ) == 0
    assert _index(registry) == before
