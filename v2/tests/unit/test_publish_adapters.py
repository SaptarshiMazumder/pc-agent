"""The publish service's AWS adapters, against fake boto tables/clients.

Two things here are worth more than the rest, because both are races that fail SILENTLY:

  * bundle-id ownership — a conditional put. "Read then write" would let two creators both win an
    id, and the loser's agent would then receive updates published by a stranger.
  * the index lock — S3 has no transactions, so without it two publishes each read the index, add
    a row, and write back; the second deletes the first author's bundle with no error anywhere.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.application.interfaces.publish_intake import LISTED, PENDING_REVIEW, REVOKED
from agent_runtime.infrastructure.publish.creator_directory import (
    DynamoCreatorDirectory,
    creator_id_for,
)
from agent_runtime.infrastructure.publish.index_store import DynamoIndexLock, S3IndexStore
from agent_runtime.infrastructure.publish.signer import DirectoryBundleSigner, KmsEnvelopeSigner


class ConditionalCheckFailedException(Exception):
    """Named to match botocore's, which the adapters detect by name (no boto3 dependency here)."""


class FakeTable:
    """A DynamoDB table with just enough behaviour to exercise the conditional writes."""

    def __init__(self, key_name: str):
        self._key = key_name
        self.items: dict[str, dict] = {}

    def get_item(self, Key):  # noqa: N803 — boto's casing
        item = self.items.get(Key[self._key])
        return {"Item": dict(item)} if item else {}

    def put_item(self, Item, ConditionExpression="", ExpressionAttributeValues=None):  # noqa: N803
        key = Item[self._key]
        existing = self.items.get(key)
        if existing and "attribute_not_exists" in ConditionExpression:
            # The one clause the lock needs beyond plain existence: an expired lease is takeable.
            takeable = "expires <" in ConditionExpression and existing.get("expires", 0) < (
                (ExpressionAttributeValues or {}).get(":now", 0)
            )
            if not takeable:
                raise ConditionalCheckFailedException(key)
        self.items[key] = dict(Item)

    def update_item(self, Key, UpdateExpression, ExpressionAttributeNames=None, ExpressionAttributeValues=None):  # noqa: N803
        item = self.items.setdefault(Key[self._key], dict(Key))
        names = ExpressionAttributeNames or {}
        for assignment in UpdateExpression.replace("SET", "").split(","):
            field, _, value = assignment.partition("=")
            field, value = field.strip(), value.strip()
            item[names.get(field, field)] = (ExpressionAttributeValues or {}).get(value)

    def delete_item(self, Key, ConditionExpression="", ExpressionAttributeNames=None, ExpressionAttributeValues=None):  # noqa: N803
        key = Key[self._key]
        item = self.items.get(key)
        if item is None:
            return
        if ConditionExpression:
            expected = (ExpressionAttributeValues or {}).get(":token")
            if item.get("token") != expected:
                raise ConditionalCheckFailedException(key)
        self.items.pop(key, None)

    def scan(self, **kwargs):
        return {"Items": [dict(v) for v in self.items.values()]}


def directory(key_factory=None) -> DynamoCreatorDirectory:
    return DynamoCreatorDirectory(
        FakeTable("account_id"),
        FakeTable("bundle_id"),
        key_factory=key_factory or (lambda: ("priv-1", "pub-1")),
        now=lambda: "2026-08-09T00:00:00+00:00",
    )


# ────────────────────────────── creator identity ──────────────────────────────


def test_a_creator_id_is_derived_stably_and_reveals_nothing():
    """It goes into a PUBLIC index and a signed roster, both permanent. So: not the email, not the
    raw account id, and unchanged when someone updates their profile."""
    first = creator_id_for("acc-1")
    assert first == creator_id_for("acc-1")
    assert first != creator_id_for("acc-2")
    assert "acc-1" not in first
    assert first.startswith("c-")


def test_a_first_submission_mints_a_pending_creator_with_a_key():
    d = directory()
    creator = d.for_account("acc-1", "Bob")

    assert creator.state == PENDING_REVIEW
    assert not creator.may_publish  # the review step, by construction
    assert creator.public_key == "pub-1"
    assert d.private_key(creator.id) == "priv-1"


def test_the_same_account_gets_the_same_identity_and_key_forever():
    d = directory()
    first = d.for_account("acc-1", "Bob")
    minted = []
    d._key_factory = lambda: minted.append(1) or ("priv-2", "pub-2")  # noqa: SLF001

    again = d.for_account("acc-1", "Bob renamed")

    assert again.id == first.id and again.public_key == "pub-1"
    assert minted == [], "a second key would orphan every bundle already signed with the first"


def test_a_racing_first_submission_returns_the_winners_identity():
    """Two concurrent first publishes must not mint two identities for one account."""
    d = directory()
    winner = d.for_account("acc-1", "Bob")

    def racing():
        # Simulate the other request having already written the row between our read and our put.
        d._creators.items[winner.account_id] = {  # noqa: SLF001
            "account_id": "acc-1", "creator_id": winner.id, "state": PENDING_REVIEW,
            "public_key": "pub-1", "private_key": "priv-1", "name": "Bob",
        }
        raise ConditionalCheckFailedException("acc-1")

    d._creators.items.clear()  # noqa: SLF001
    d._creators.put_item = lambda **kw: racing()  # noqa: SLF001
    assert d.for_account("acc-1", "Bob").id == winner.id


def test_admit_flips_to_listed_and_revoke_to_revoked():
    d = directory()
    creator = d.for_account("acc-1", "Bob")

    d.admit(creator.id)
    assert d.for_account("acc-1").state == LISTED
    assert d.for_account("acc-1").may_publish

    d.revoke(creator.id)
    assert d.for_account("acc-1").state == REVOKED
    assert not d.for_account("acc-1").may_publish


def test_pending_lists_only_those_awaiting_admission():
    d = directory()
    waiting = d.for_account("acc-1", "Bob")
    admitted = d.for_account("acc-2", "Carol")
    d.admit(admitted.id)

    assert [c.id for c in d.pending()] == [waiting.id]


def test_admitting_an_unknown_creator_raises():
    with pytest.raises(KeyError):
        directory().admit("c-nobody")


# ────────────────────────────── owning a bundle id ──────────────────────────────


def test_the_first_claim_wins_and_later_ones_report_the_real_owner():
    d = directory()
    assert d.claim("weather", "c-bob") == "c-bob"
    assert d.claim("weather", "c-eve") == "c-bob", "the id belongs to whoever published it first"
    assert d.owner_of("weather") == "c-bob"


def test_reclaiming_your_own_id_is_allowed():
    d = directory()
    d.claim("weather", "c-bob")
    assert d.claim("weather", "c-bob") == "c-bob"


def test_an_unclaimed_id_has_no_owner():
    assert directory().owner_of("nothing") == ""


# ────────────────────────────── signing ──────────────────────────────


def test_the_directory_signer_signs_with_a_real_ed25519_key():
    from agent_runtime.infrastructure import signing

    private, public = signing.generate_keypair()
    d = directory(key_factory=lambda: (private, public))
    creator = d.for_account("acc-1", "Bob")

    signature = DirectoryBundleSigner(d).sign(creator.id, b"digest")

    assert signing.verify(public, b"digest", signature)


def test_signing_for_a_creator_with_no_key_is_refused():
    d = directory(key_factory=lambda: ("", ""))
    creator = d.for_account("acc-1", "Bob")
    with pytest.raises(ValueError, match="no signing key"):
        DirectoryBundleSigner(d).sign(creator.id, b"digest")


class FakeKms:
    """Reversible 'encryption' — enough to prove the key is never stored in the clear."""

    def encrypt(self, KeyId, Plaintext):  # noqa: N803
        return {"CiphertextBlob": b"ENC:" + Plaintext}

    def decrypt(self, CiphertextBlob):  # noqa: N803
        assert CiphertextBlob.startswith(b"ENC:"), "asked to decrypt something never encrypted"
        return {"Plaintext": CiphertextBlob[4:]}


def test_a_kms_wrapped_key_is_never_stored_in_the_clear():
    """What this buys: a leaked table dump is worthless without the KMS grant."""
    from agent_runtime.infrastructure import signing

    private, public = signing.generate_keypair()
    envelope = KmsEnvelopeSigner(None, FakeKms(), "key-1")
    d = DynamoCreatorDirectory(
        FakeTable("account_id"),
        FakeTable("bundle_id"),
        key_factory=lambda: (envelope.encrypt(private), public),
        now=lambda: "t",
    )
    envelope._directory = d  # noqa: SLF001
    creator = d.for_account("acc-1", "Bob")

    stored = d.private_key(creator.id)
    assert private not in stored, "the plaintext key reached the table"

    assert signing.verify(public, b"digest", envelope.sign(creator.id, b"digest"))


# ────────────────────────────── the index store ──────────────────────────────


class FakeS3:
    class NoSuchKey(Exception):
        pass

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.puts: list[tuple[str, str]] = []
        self.meta = type("meta", (), {"region_name": "ap-northeast-1"})()

    def get_object(self, Bucket, Key):  # noqa: N803
        if Key not in self.objects:
            raise self.NoSuchKey(Key)
        return {"Body": type("body", (), {"read": lambda _s: self.objects[Key]})()}

    def put_object(self, Bucket, Key, Body, ContentType="", CacheControl=""):  # noqa: N803
        self.objects[Key] = Body
        self.puts.append((Key, CacheControl))


def test_a_missing_index_is_an_empty_registry_not_an_error():
    assert S3IndexStore(FakeS3(), "bucket").read_index() == {}


def test_a_corrupt_index_raises_rather_than_silently_starting_over():
    """Merging into something unparseable would publish a registry that drops every bundle in it."""
    s3 = FakeS3()
    s3.objects["index.json"] = b"{not json"
    with pytest.raises(ValueError):
        S3IndexStore(s3, "bucket").read_index()


def test_the_index_is_written_no_cache():
    """It changes on every publish and is read on every store open. Cached, a successful publish
    looks like it did nothing for hours."""
    s3 = FakeS3()
    store = S3IndexStore(s3, "bucket")
    store.write_index({"bundles": []})
    assert ("index.json", "no-cache") in s3.puts
    assert json.loads(s3.objects["index.json"])["bundles"] == []


def test_artifact_urls_are_absolute_and_prefix_aware():
    store = S3IndexStore(FakeS3(), "bucket", prefix="registry")
    url = store.put_artifact("weather-1.0.0.agentpkg", b"x", "application/octet-stream")
    assert url == (
        "https://bucket.s3.ap-northeast-1.amazonaws.com/registry/weather-1.0.0.agentpkg"
    )


def test_a_public_base_overrides_the_bucket_endpoint():
    """So a CDN or custom domain later changes one setting, not the index format."""
    store = S3IndexStore(FakeS3(), "bucket", public_base="https://cdn.example/")
    assert store.put_artifact("a.agentpkg", b"x", "t") == "https://cdn.example/a.agentpkg"


# ────────────────────────────── the index lock ──────────────────────────────


def clock():
    now = [1000.0]
    return now, (lambda: now[0])


def test_the_lock_is_exclusive():
    table = FakeTable("lock_id")
    _, now = clock()
    with DynamoIndexLock(table, now=now):
        second = DynamoIndexLock(table, attempts=1, wait_seconds=0, sleep=lambda _s: None, now=now)
        with pytest.raises(TimeoutError, match="another publish is in progress"):
            with second:
                pass


def test_the_lock_is_released_on_exit_even_when_the_body_raises():
    table = FakeTable("lock_id")
    _, now = clock()
    with pytest.raises(RuntimeError):
        with DynamoIndexLock(table, now=now):
            raise RuntimeError("publish blew up")
    assert table.items == {}, "a lock left behind stops every future publish"


def test_an_expired_lease_is_taken_over():
    """A function killed mid-publish would otherwise lock the registry permanently."""
    table = FakeTable("lock_id")
    ticks, now = clock()
    lock = DynamoIndexLock(table, ttl_seconds=60, now=now)
    lock.__enter__()  # acquired, never released — simulating a dead holder

    ticks[0] += 120
    with DynamoIndexLock(table, attempts=1, wait_seconds=0, sleep=lambda _s: None, now=now):
        pass  # took over cleanly


def test_a_dead_holder_cannot_delete_a_lease_it_no_longer_owns():
    """Without the token condition, a publish that overran its TTL would release the lock a
    DIFFERENT publish is now holding."""
    table = FakeTable("lock_id")
    ticks, now = clock()
    stale = DynamoIndexLock(table, ttl_seconds=60, now=now)
    stale.__enter__()

    ticks[0] += 120
    fresh = DynamoIndexLock(table, attempts=1, wait_seconds=0, sleep=lambda _s: None, now=now)
    fresh.__enter__()

    stale.__exit__(None, None, None)  # the dead holder finally wakes up and tries to release
    assert "registry-index" in table.items, "the live publish lost its lock"
    fresh.__exit__(None, None, None)
    assert table.items == {}


def test_the_lock_waits_before_giving_up():
    table = FakeTable("lock_id")
    _, now = clock()
    slept: list[float] = []
    held = DynamoIndexLock(table, now=now)
    held.__enter__()
    waiting = DynamoIndexLock(
        table, attempts=3, wait_seconds=0.5, sleep=slept.append, now=now
    )
    with pytest.raises(TimeoutError):
        waiting.__enter__()
    assert slept == [0.5, 0.5, 0.5]


# ────────────────────────────── the author configures NOTHING ──────────────────────────────


def test_the_publish_target_comes_from_the_build_when_nothing_else_says():
    """The fix for the flaw that made this whole feature unreachable.

    An author installs the desktop app, signs in, and presses Publish. They never open a .env and
    have no idea a publish service exists. So the build carries its own marketplace — the same
    profile that already carries accounts_url and registry_url — and the resolution is:

        AGENTD_PUBLISH_TARGET  >  config.json  >  the distribution profile
    """
    import tomllib

    from agent_runtime.distribution import parse_profile, render_profile

    profile = parse_profile(
        {"store": {"publish_url": "https://api.example.com/"}}
    )
    assert profile.publish_url == "https://api.example.com", "trailing slash normalised"

    # and it survives a write/read round trip, so a generated payload keeps it
    assert parse_profile(tomllib.loads(render_profile(profile))).publish_url == profile.publish_url


def test_an_operator_can_still_override_the_baked_target(monkeypatch):
    """Publishing to an s3:// bucket or a directory is a release job and must beat the build."""
    from types import SimpleNamespace

    from agent_runtime.infrastructure.marketplace.publisher_factory import publisher_for
    from agent_runtime.infrastructure.marketplace.s3_publisher import S3RegistryPublisher

    baked = SimpleNamespace(publish_target="https://api.example.com", publisher_keyfile="k.json")
    assert isinstance(publisher_for(baked, "s3://bucket"), S3RegistryPublisher)


def test_a_build_with_no_marketplace_publishes_nowhere():
    """The empty default is load-bearing: a downloaded copy of this product must not be able to
    push into someone else's marketplace just because the tool is present."""
    from types import SimpleNamespace

    from agent_runtime.infrastructure.marketplace.publisher_factory import publisher_for

    assert publisher_for(SimpleNamespace(publish_target="")) is None


# ────────────────────────────── the parking area ──────────────────────────────


class ListingS3(FakeS3):
    """FakeS3 plus the three calls S3ParkedStore needs (list/delete, paged)."""

    def list_objects_v2(self, Bucket, Prefix="", ContinuationToken=None):  # noqa: N803
        keys = sorted(k for k in self.objects if k.startswith(Prefix))
        return {
            "Contents": [{"Key": k, "Size": len(self.objects[k]), "LastModified": "t"} for k in keys],
            "IsTruncated": False,
        }

    def delete_object(self, Bucket, Key):  # noqa: N803
        self.objects.pop(Key, None)  # deleting a missing key succeeds — S3's own semantics


def parked_store():
    from agent_runtime.infrastructure.publish.parked_store import S3ParkedStore

    s3 = ListingS3()
    return S3ParkedStore(s3, "bucket"), s3


def test_parked_packages_live_under_the_private_pending_prefix():
    """The key shape IS the security boundary: the bucket policy carves `pending/*` out of the
    public-read grant, so a parked key anywhere else would be world-readable."""
    store, s3 = parked_store()
    store.park("c-bob", "weather", b"PKG")
    assert list(s3.objects) == ["pending/c-bob/weather.agentpkg"]


def test_a_reupload_before_admission_replaces_the_previous_attempt():
    store, s3 = parked_store()
    store.park("c-bob", "weather", b"OLD")
    store.park("c-bob", "weather", b"NEW")
    assert s3.objects["pending/c-bob/weather.agentpkg"] == b"NEW"
    assert len(store.parked("c-bob")) == 1


def test_parked_lists_only_that_creators_packages():
    store, _ = parked_store()
    store.park("c-bob", "weather", b"PKG")
    store.park("c-eve", "other", b"PKG2")
    assert [p.bundle_id for p in store.parked("c-bob")] == ["weather"]


def test_retrieve_of_a_vanished_package_is_empty_not_an_error():
    store, _ = parked_store()
    assert store.retrieve("c-bob", "gone") == b""


def test_remove_is_idempotent():
    store, _ = parked_store()
    store.park("c-bob", "weather", b"PKG")
    store.remove("c-bob", "weather")
    store.remove("c-bob", "weather")  # second time: nothing to delete, still fine
    assert store.parked("c-bob") == []


# ────────────────────────────── the root key vault ──────────────────────────────


def vault(with_kms=True):
    from agent_runtime.infrastructure.publish.root_vault import DynamoRootKeyVault

    table = FakeTable("account_id")
    if not with_kms:
        return DynamoRootKeyVault(table), table
    envelope = KmsEnvelopeSigner(None, FakeKms(), "key-1")
    return DynamoRootKeyVault(table, decrypt=envelope.decrypt, encrypt=envelope.encrypt), table


def test_the_vaulted_root_key_is_wrapped_at_rest_and_round_trips():
    v, table = vault()
    v.store("ROOT-PRIVATE", "ROOT-PUBLIC")

    row = table.items["__root__"]
    assert "ROOT-PRIVATE" not in str(row["private_key"]), "the plaintext root key reached the table"
    assert v.private_key() == "ROOT-PRIVATE"
    assert v.public_key() == "ROOT-PUBLIC"


def test_the_root_row_can_never_publish():
    """It sits in the creators table, so it MUST be shaped so no publish path treats it as a
    creator: state `root` is not `listed`, and pending() must not offer it for admission."""
    v, table = vault()
    v.store("ROOT-PRIVATE", "ROOT-PUBLIC")

    d = DynamoCreatorDirectory(table, FakeTable("bundle_id"), now=lambda: "t")
    assert d.pending() == []
    root_as_creator = d.for_account("__root__")
    assert not root_as_creator.may_publish


def test_an_empty_vault_names_the_command_that_fills_it():
    v, _ = vault()
    try:
        v.private_key()
        raise AssertionError("an empty vault must refuse, not return ''")
    except RuntimeError as e:
        assert "upload-root" in str(e)
