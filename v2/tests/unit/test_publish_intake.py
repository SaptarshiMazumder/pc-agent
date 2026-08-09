"""The publish SERVICE: an author's upload becomes a signed listing plus its installer.

This is the piece that makes "users can publish to the marketplace" true. Until it existed,
publishing needed the registry's private key and S3 credentials on the publisher's own machine —
correct for a release engineer, impossible for an author.

Tested entirely with fakes: no AWS, no network, no makensis. That is the point of the ports.

The guards that matter most, in order:
  * a creator's FIRST publish is 202 pending, not an error
  * a bundle id belongs to the first creator who published it — atomically
  * a version that is not newer is refused, with the reason stated
  * artifacts are written BEFORE the index, under a lock
  * another creator's rows are carried EXACTLY — never re-signed, never rebuilt
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest

from agent_runtime.application.interfaces.publish_intake import (
    BAD_REQUEST,
    CONFLICT,
    FORBIDDEN,
    LISTED,
    OK,
    PENDING,
    PENDING_REVIEW,
    REVOKED,
    SERVER_ERROR,
    TOO_LARGE,
    UNAUTHORIZED,
    Creator,
    Submission,
)
from agent_runtime.application.services.publish_intake_service import PublishIntakeService


def agentpkg(bundle_id="weather", version="1.2.0") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "bundle.toml",
            f'[bundle]\nid = "{bundle_id}"\nname = "Weather"\nversion = "{version}"\n'
            'description = "Forecasts."\n',
        )
        zf.writestr("agent/agent.toml", f'name = "Weather"\nversion = "{version}"\n[app]\n')
    return buffer.getvalue()


# ────────────────────────────── fakes ──────────────────────────────


class FakeAuth:
    def __init__(self, accounts=None):
        self._accounts = accounts if accounts is not None else {"tok": {"account_id": "acc-1"}}

    def account(self, token):
        return self._accounts.get(token)


class FakeCreators:
    def __init__(self, state=LISTED):
        self.creator = Creator(id="c-abc", account_id="acc-1", name="Bob", state=state)
        self.owners: dict[str, str] = {}
        self.created: list[str] = []

    def for_account(self, account_id, display_name=""):
        self.created.append(account_id)
        return self.creator

    def claim(self, bundle_id, creator_id):
        return self.owners.setdefault(bundle_id, creator_id)


class FakeSigner:
    def __init__(self):
        self.signed: list[bytes] = []

    def sign(self, creator_id, message):
        self.signed.append(message)
        return f"sig({creator_id}:{message.decode()[:8]})"

    def public_key(self, creator_id):
        return "pub"


class FakeStore:
    def __init__(self, index=None):
        self.index = index if index is not None else {}
        self.artifacts: dict[str, bytes] = {}
        self.order: list[str] = []

    def read_index(self):
        return json.loads(json.dumps(self.index)) if self.index else {}

    def write_index(self, index):
        self.index = index
        self.order.append("index")

    def put_artifact(self, name, data, content_type):
        self.artifacts[name] = data
        self.order.append(name)
        return f"https://cdn.example/{name}"


class FakeLock:
    def __init__(self, fail=False):
        self.entered = 0
        self.exited = 0
        self._fail = fail

    def __enter__(self):
        if self._fail:
            raise TimeoutError("another publish is in progress")
        self.entered += 1
        return self

    def __exit__(self, *exc):
        self.exited += 1


class FakeProducts:
    """Stands in for BuildProductService. Writes a fake installer where the real one would."""

    def __init__(self, stub_name="weather-1.2.0-setup.exe", warnings=(), boom=False):
        self._name = stub_name
        self._warnings = list(warnings)
        self._boom = boom
        #: every ProductSource it was handed — the payload's bundle name comes off this path
        self.sources = []

    def build(self, source, payload_dir, stub_dir=None, overrides=None):
        from types import SimpleNamespace

        self.sources.append(source)
        if self._boom:
            raise RuntimeError("makensis exploded")
        if self._name is None:
            return SimpleNamespace(stub=None, warnings=self._warnings)
        target = stub_dir if stub_dir is not None else payload_dir
        target.mkdir(parents=True, exist_ok=True)
        stub = target / self._name
        stub.write_bytes(b"MZ-fake-installer")
        return SimpleNamespace(stub=stub, warnings=self._warnings)


class FakeParker:
    """In-memory IntakeParker: one slot per (creator, bundle id), like the S3 adapter."""

    def __init__(self):
        self.slots: dict[tuple[str, str], bytes] = {}

    def park(self, creator_id, bundle_id, package):
        self.slots[(creator_id, bundle_id)] = package

    def parked(self, creator_id):
        from agent_runtime.application.interfaces.publish_intake import ParkedPackage

        return [
            ParkedPackage(creator_id=c, bundle_id=b, size=len(data))
            for (c, b), data in sorted(self.slots.items())
            if c == creator_id
        ]

    def retrieve(self, creator_id, bundle_id):
        return self.slots.get((creator_id, bundle_id), b"")

    def remove(self, creator_id, bundle_id):
        self.slots.pop((creator_id, bundle_id), None)


def service(**kw):
    parts = {
        "authenticator": FakeAuth(),
        "creators": FakeCreators(),
        "signer": FakeSigner(),
        "index_store": FakeStore(),
        "lock": FakeLock(),
        "product_service": FakeProducts(),
    }
    parts.update(kw)
    return PublishIntakeService(**parts), parts


# ────────────────────────────── the gate ──────────────────────────────


def test_no_package_is_refused_before_anything_else():
    svc, _ = service()
    assert svc.submit(Submission(package=b"", token="tok")).status == BAD_REQUEST


def test_an_oversized_package_is_refused_without_reading_it():
    svc, parts = service()
    svc._max_bytes = 10  # noqa: SLF001 — the ceiling is the thing under test
    result = svc.submit(Submission(package=agentpkg(), token="tok"))
    assert result.status == TOO_LARGE
    assert parts["creators"].created == []  # no identity was minted for a rejected request


def test_an_unknown_token_is_401_and_mints_nothing():
    svc, parts = service(authenticator=FakeAuth({}))
    result = svc.submit(Submission(package=agentpkg(), token="nope"))
    assert result.status == UNAUTHORIZED
    assert parts["creators"].created == []


def test_a_session_with_no_account_is_401():
    svc, _ = service(authenticator=FakeAuth({"tok": {"email": "b@example.com"}}))
    assert svc.submit(Submission(package=agentpkg(), token="tok")).status == UNAUTHORIZED


def test_a_revoked_creator_is_403():
    svc, _ = service(creators=FakeCreators(state=REVOKED))
    assert svc.submit(Submission(package=agentpkg(), token="tok")).status == FORBIDDEN


# ────────────────────────────── first publish = review ──────────────────────────────


def test_a_first_publish_is_pending_review_and_builds_nothing():
    """202, and it is a SUCCESS. Listing a creator is an operator decision by construction —
    calling it a failure sends an author looking for a bug in an agent that is fine."""
    svc, parts = service(creators=FakeCreators(state=PENDING_REVIEW))
    result = svc.submit(Submission(package=agentpkg(), token="tok"))

    assert result.status == PENDING
    assert result.ok and result.pending
    assert "awaiting review" in result.message
    assert parts["index_store"].artifacts == {}  # nothing uploaded
    assert parts["lock"].entered == 0  # the index was never touched


def test_a_first_publish_parks_the_package_for_admission_to_complete():
    """The upload used to be THROWN AWAY on 202 — while the message told the author they would
    not need to publish again. Parking is what makes that sentence true: admission completes the
    publish, and the author's single click is the whole flow."""
    parker = FakeParker()
    svc, parts = service(creators=FakeCreators(state=PENDING_REVIEW), parker=parker)

    result = svc.submit(Submission(package=agentpkg(), token="tok"))

    assert result.status == PENDING
    assert parker.slots == {("c-abc", "weather"): agentpkg()}
    assert "published automatically" in result.message
    assert "do not need to publish again" in result.message
    assert parts["index_store"].artifacts == {}  # parked is NOT published


def test_a_broken_zip_is_refused_at_parking_time_not_at_admission():
    """The author is on the other end NOW. Park garbage silently and it fails weeks later on the
    operator's screen, where the one person who can fix it cannot see it."""
    parker = FakeParker()
    svc, _ = service(creators=FakeCreators(state=PENDING_REVIEW), parker=parker)

    result = svc.submit(Submission(package=b"PK\x03\x04 not a zip", token="tok"))

    assert result.status == BAD_REQUEST
    assert parker.slots == {}


def test_admission_publishes_what_was_parked_and_clears_the_slot():
    parker = FakeParker()
    parker.slots[("c-abc", "weather")] = agentpkg()
    svc, parts = service(parker=parker)  # creator now LISTED

    results = svc.complete_parked(parts["creators"].creator)

    assert [r.status for r in results] == [OK]
    assert "weather-1.2.0.agentpkg" in parts["index_store"].artifacts
    assert parker.slots == {}  # the queue drains


def test_a_definitively_refused_parked_package_is_dropped_not_retried_forever():
    """CONFLICT is a final answer. Keeping the slot would retry it on every admission, forever,
    and the operator would read the same failure line each time."""
    parker = FakeParker()
    parker.slots[("c-abc", "weather")] = agentpkg(version="1.0.0")
    store = FakeStore(
        {"schema": 2, "bundles": [{"id": "weather", "version": "2.0.0"}], "publishers": {}}
    )
    svc, parts = service(parker=parker, index_store=store)

    results = svc.complete_parked(parts["creators"].creator)

    assert [r.status for r in results] == [CONFLICT]
    assert parker.slots == {}


def test_an_infrastructure_failure_keeps_the_parked_package_for_a_retry():
    """A store outage is not a verdict on the package — the slot survives so the next admission
    (or a manual retry) publishes it once whatever broke is fixed."""

    class ExplodingStore(FakeStore):
        def put_artifact(self, name, data, content_type):
            raise RuntimeError("S3 is down")

    parker = FakeParker()
    parker.slots[("c-abc", "weather")] = agentpkg()
    svc, parts = service(parker=parker, index_store=ExplodingStore())

    results = svc.complete_parked(parts["creators"].creator)

    assert [r.status for r in results] == [SERVER_ERROR]
    assert ("c-abc", "weather") in parker.slots


# ────────────────────────────── the package itself ──────────────────────────────


def test_a_non_package_upload_is_400():
    svc, _ = service()
    result = svc.submit(Submission(package=b"not a zip", token="tok"))
    assert result.status == BAD_REQUEST
    assert "agentpkg" in result.message


def test_a_claimed_id_that_contradicts_the_manifest_is_refused():
    """A mismatch is what an id-squatting attempt looks like, and also a plain client bug. The
    manifest is the authority either way."""
    svc, _ = service()
    result = svc.submit(Submission(package=agentpkg("weather"), token="tok", bundle_id="notweather"))
    assert result.status == BAD_REQUEST
    assert "manifest says 'weather'" in result.message


# ────────────────────────────── owning an id ──────────────────────────────


def test_the_first_creator_to_publish_an_id_keeps_it():
    creators = FakeCreators()
    creators.owners["weather"] = "c-someone-else"
    svc, parts = service(creators=creators)

    result = svc.submit(Submission(package=agentpkg("weather"), token="tok"))

    assert result.status == CONFLICT
    assert "belongs to another creator" in result.message
    assert parts["index_store"].artifacts == {}


def test_publishing_your_own_id_again_is_fine():
    creators = FakeCreators()
    creators.owners["weather"] = creators.creator.id
    svc, _ = service(creators=creators)
    assert svc.submit(Submission(package=agentpkg(version="2.0.0"), token="tok")).status == OK


# ────────────────────────────── versions ──────────────────────────────


def test_republishing_the_same_version_is_refused_with_the_reason():
    store = FakeStore({"bundles": [{"id": "weather", "version": "1.2.0", "url": "x", "sha256": "y"}]})
    svc, _ = service(index_store=store)

    result = svc.submit(Submission(package=agentpkg(version="1.2.0"), token="tok"))

    assert result.status == CONFLICT
    assert "supersede BY VERSION" in result.message
    assert "reaches nobody" in result.message


def test_an_older_version_is_refused():
    store = FakeStore({"bundles": [{"id": "weather", "version": "2.0.0", "url": "x", "sha256": "y"}]})
    svc, _ = service(index_store=store)
    assert svc.submit(Submission(package=agentpkg(version="1.9.9"), token="tok")).status == CONFLICT


def test_a_newer_version_replaces_the_published_row():
    # schema 2 explicitly: an index with no `schema` key parses as schema 1, which the service
    # refuses outright (a creator-signed entry is unverifiable there).
    store = FakeStore(
        {
            "schema": 2,
            "bundles": [{"id": "weather", "version": "1.0.0", "url": "old.agentpkg", "sha256": "y"}],
        }
    )
    svc, _ = service(index_store=store)

    assert svc.submit(Submission(package=agentpkg(version="1.2.0"), token="tok")).status == OK

    rows = [b for b in store.index["bundles"] if b["id"] == "weather"]
    assert len(rows) == 1 and rows[0]["version"] == "1.2.0"


# ────────────────────────────── the happy path ──────────────────────────────


def test_a_publish_uploads_artifacts_then_the_index_under_the_lock():
    svc, parts = service()

    result = svc.submit(Submission(package=agentpkg(), token="tok"))

    assert result.status == OK
    store, lock = parts["index_store"], parts["lock"]
    # ORDERING. The index is what makes a url public, so every artifact it names must already be
    # there — otherwise the store lists a download that 404s.
    assert store.order[-1] == "index"
    assert "weather-1.2.0.agentpkg" in store.order[:-1]
    assert "weather-1.2.0-setup.exe" in store.order[:-1]
    assert lock.entered == 1 and lock.exited == 1


def test_the_clients_filename_never_names_the_file_the_payload_is_built_from():
    """It shipped once and broke the product silently. The service wrote the upload to disk under
    the CLIENT's filename; the payload writer then copied that path into `bundles/` under the same
    name. A multipart bug made that name `weather-1.2.0-setup.exe`, the engine globs
    `bundles/*.agentpkg`, and the installed app opened with no agent in it.

    The manifest is the only authority on what anything is called — here, in S3, and in the
    payload, all from one string."""
    svc, parts = service()

    result = svc.submit(
        Submission(
            package=agentpkg(),
            token="tok",
            filename="weather-1.2.0-setup.exe",  # what the broken client sent
        )
    )

    assert result.status == OK
    source = parts["product_service"].sources[0]
    assert Path(source.package).name == "weather-1.2.0.agentpkg"
    assert "weather-1.2.0.agentpkg" in parts["index_store"].artifacts


def test_a_hostile_filename_cannot_escape_the_work_directory():
    """`../` in a filename used to choose where bytes landed. Now it chooses nothing."""
    svc, parts = service()

    result = svc.submit(
        Submission(package=agentpkg(), token="tok", filename="../../../evil.agentpkg")
    )

    assert result.status == OK
    assert Path(parts["product_service"].sources[0].package).name == "weather-1.2.0.agentpkg"


def test_the_entry_is_signed_with_the_creators_key_and_stamped_with_their_id():
    svc, parts = service()
    svc.submit(Submission(package=agentpkg(), token="tok"))

    entry = next(b for b in parts["index_store"].index["bundles"] if b["id"] == "weather")
    assert entry["publisher_id"] == "c-abc"
    assert entry["sig"].startswith("sig(c-abc:")
    assert entry["url"] == "weather-1.2.0.agentpkg"  # relative, like every registry url
    assert len(entry["sha256"]) == 64


def test_the_installer_row_is_signed_separately_from_the_bundle():
    """The entry signature covers only the .agentpkg digest. An unsigned installer row would let
    anyone able to write to the registry repoint the download without breaking a signature."""
    svc, parts = service()
    svc.submit(Submission(package=agentpkg(), token="tok"))

    entry = next(b for b in parts["index_store"].index["bundles"] if b["id"] == "weather")
    row = entry["installers"][0]
    assert row["platform"] == "win"
    assert row["url"] == "weather-1.2.0-setup.exe"
    assert row["sig"] and row["sig"] != entry["sig"]
    assert len(parts["signer"].signed) == 2  # bundle digest + installer digest


def test_the_response_carries_both_urls():
    svc, _ = service()
    result = svc.submit(Submission(package=agentpkg(), token="tok"))
    assert result.url.endswith("weather-1.2.0.agentpkg")
    assert result.installer_url.endswith("weather-1.2.0-setup.exe")
    assert result.body()["installer_url"] == result.installer_url


def test_the_index_is_always_schema_2():
    """Schema 1 has one key for everything, which is the shape that cannot support a second
    creator at all."""
    svc, parts = service()
    svc.submit(Submission(package=agentpkg(), token="tok"))
    assert parts["index_store"].index["schema"] == 2


# ────────────────────────────── other creators are untouchable ──────────────────────────────


def test_another_creators_rows_are_carried_exactly():
    """Signed with a key this service does not have and must never have. Re-signing them would be
    impossible; DROPPING them is the accident that unpublishes a marketplace."""
    theirs = {
        "id": "comfyui",
        "version": "3.1.0",
        "url": "comfyui-3.1.0.agentpkg",
        "sha256": "ab" * 32,
        "sig": "their-signature",
        "publisher_id": "c-someone-else",
    }
    store = FakeStore({"bundles": [dict(theirs)], "schema": 2, "publishers": {"roster": []}})
    svc, _ = service(index_store=store)

    svc.submit(Submission(package=agentpkg(), token="tok"))

    carried = next(b for b in store.index["bundles"] if b["id"] == "comfyui")
    assert carried == theirs  # byte-for-byte, signature included
    assert store.index["publishers"] == {"roster": []}  # the roster survives too
    assert {b["id"] for b in store.index["bundles"]} == {"comfyui", "weather"}


def test_the_index_is_reread_inside_the_lock():
    """The version check reads the index BEFORE taking the lock. Reusing that read to merge would
    defeat the lock entirely: a publish committed in between would be lost."""
    reads: list[int] = []
    store = FakeStore()
    original = store.read_index

    def counting():
        reads.append(1)
        return original()

    store.read_index = counting
    svc, _ = service(index_store=store)
    svc.submit(Submission(package=agentpkg(), token="tok"))
    assert len(reads) >= 2


def test_a_held_lock_surfaces_as_a_retryable_error():
    svc, _ = service(lock=FakeLock(fail=True))
    with pytest.raises(TimeoutError):
        svc.submit(Submission(package=agentpkg(), token="tok"))


# ────────────────────────────── degraded, not failed ──────────────────────────────


def test_no_installer_still_publishes_the_bundle_and_says_so():
    """Refusing would take the marketplace down for an operator-side problem."""
    svc, parts = service(product_service=FakeProducts(stub_name=None, warnings=["no makensis"]))

    result = svc.submit(Submission(package=agentpkg(), token="tok"))

    assert result.status == OK
    assert "bundle only" in result.message
    assert "no makensis" in result.warnings
    entry = next(b for b in parts["index_store"].index["bundles"] if b["id"] == "weather")
    assert "installers" not in entry


def test_a_crashing_builder_does_not_lose_the_publish():
    svc, _ = service(product_service=FakeProducts(boom=True))
    result = svc.submit(Submission(package=agentpkg(), token="tok"))
    assert result.status == OK
    assert any("makensis exploded" in w for w in result.warnings)


def test_no_product_service_at_all_is_a_warning_not_a_failure():
    svc, _ = service(product_service=None)
    result = svc.submit(Submission(package=agentpkg(), token="tok"))
    assert result.status == OK
    assert any("no product builder" in w for w in result.warnings)


# ────────────────────────────── schema 1 is refused, not upgraded ──────────────────────────────


def test_publishing_into_a_schema_1_registry_is_refused_with_the_migration_command():
    """In schema 1 every client verifies every entry against the ONE pinned key; publisher_id does
    not exist. A creator-signed entry there fails verification on every machine, fail-closed — the
    store lists it and the download refuses, with nothing anywhere saying why."""
    store = FakeStore({"schema": 1, "publisher_key": "pinned", "bundles": []})
    svc, parts = service(index_store=store)

    result = svc.submit(Submission(package=agentpkg(), token="tok"))

    assert result.status == CONFLICT
    assert "schema 1" in result.message
    assert "roster publish" in result.message
    assert parts["index_store"].artifacts == {}, "nothing may be uploaded"
    assert store.index["schema"] == 1, "the registry is left exactly as it was"


def test_an_empty_registry_is_born_schema_2():
    svc, parts = service(index_store=FakeStore({}))
    assert svc.submit(Submission(package=agentpkg(), token="tok")).status == OK
    assert parts["index_store"].index["schema"] == 2


def test_a_schema_2_registry_stays_schema_2():
    store = FakeStore({"schema": 2, "bundles": [], "publishers": {"roster": [{"id": "c-x"}]}})
    svc, _ = service(index_store=store)
    assert svc.submit(Submission(package=agentpkg(), token="tok")).status == OK
    assert store.index["schema"] == 2
    assert store.index["publishers"] == {"roster": [{"id": "c-x"}]}
