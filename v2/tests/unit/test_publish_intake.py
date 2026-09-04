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


def agentpkg(bundle_id="weather", version="1.2.0", delivery="", app=True) -> bytes:
    """:param delivery: extra TOML appended to bundle.toml (e.g. a [bundle.delivery] table).
    :param app: whether agent.toml declares [app] — web delivery's precondition."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        zf.writestr(
            "bundle.toml",
            f'[bundle]\nid = "{bundle_id}"\nname = "Weather"\nversion = "{version}"\n'
            'description = "Forecasts."\n' + delivery,
        )
        zf.writestr(
            "agent/agent.toml",
            f'name = "Weather"\nversion = "{version}"\n' + ("[app]\n" if app else ""),
        )
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
    """The registry's storage. Scoped views are REAL here — a fake that returned ``self`` for
    every scope would make the one mistake worth testing for (an org bundle on the public shelf)
    impossible to catch."""

    def __init__(self, index=None):
        self.index = index if index is not None else {}
        self.artifacts: dict[str, bytes] = {}
        self.order: list[str] = []
        #: scope -> the store standing in for that organization's registry.
        self.scopes: dict[str, "FakeStore"] = {}

    def scoped(self, scope: str):
        scope = (scope or "").strip()
        if not scope:
            return self
        return self.scopes.setdefault(scope, FakeStore())

    def read_index(self):
        return json.loads(json.dumps(self.index)) if self.index else {}

    def write_index(self, index):
        self.index = index
        self.order.append("index")

    def put_artifact(self, name, data, content_type):
        self.artifacts[name] = data
        self.order.append(name)
        return f"https://cdn.example/{name}"

    def presign(self, name, expires_s=3600):
        return f"https://signed.example/{name}?exp={expires_s}"


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


class FakeVault:
    """The platform root key. A REAL keypair, because build_roster genuinely signs — a stub that
    returned a fixed string would let a roster the clients could never verify pass the tests."""

    def __init__(self):
        from agent_runtime.infrastructure import signing

        self._private, self._public = signing.generate_keypair()

    def private_key(self):
        return self._private

    def public_key(self):
        return self._public


def service(**kw):
    parts = {
        "authenticator": FakeAuth(),
        "creators": FakeCreators(),
        "signer": FakeSigner(),
        "index_store": FakeStore(),
        "lock": FakeLock(),
        "product_service": FakeProducts(),
        "root_vault": FakeVault(),
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


WEB_ONLY = '\n[bundle.delivery]\nweb = true\nexe = false\n'


def test_a_web_only_bundle_builds_no_installer_and_the_message_says_so():
    svc, parts = service()
    result = svc.submit(Submission(package=agentpkg(delivery=WEB_ONLY), token="tok"))

    assert result.status == OK
    assert "does not offer exe delivery" in result.message
    assert parts["product_service"].sources == []  # the builder was never even asked
    entry = next(b for b in parts["index_store"].index["bundles"] if b["id"] == "weather")
    assert "installers" not in entry
    assert entry["delivery"] == {"web": True, "exe": False}


def test_web_delivery_without_an_app_table_is_refused_naming_the_fix():
    svc, parts = service()
    result = svc.submit(Submission(package=agentpkg(delivery=WEB_ONLY, app=False), token="tok"))

    assert result.status == BAD_REQUEST
    assert "[app]" in result.message
    assert parts["index_store"].index == {}  # nothing was published


def test_a_bundle_without_a_delivery_table_keeps_meaning_what_it_always_meant():
    """Every already-published bundle predates this field: exe on, web off."""
    svc, parts = service()
    assert svc.submit(Submission(package=agentpkg(), token="tok")).status == OK

    entry = next(b for b in parts["index_store"].index["bundles"] if b["id"] == "weather")
    assert entry["delivery"] == {"web": False, "exe": True}
    assert entry["installers"], "the default still builds the installer"


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


# ────────────────────────────── reserved names ──────────────────────────────
#
# Under a wildcard app-host domain a bundle id IS a public hostname (<id>.<domain>), and
# bundle_owners is first-come-forever — so the platform's own labels must be unclaimable, not
# merely unroutable. domain/reserved_hosts.py is the one shared set; the gateway's refusal to
# DERIVE these labels is tested beside _host_alias (test_platform_protocol.py).


def test_a_reserved_bundle_id_is_refused_and_never_claimed():
    svc, parts = service()

    result = svc.submit(Submission(package=agentpkg("admin"), token="tok"))

    assert result.status == BAD_REQUEST
    assert "reserved by the platform" in result.message
    assert parts["creators"].owners == {}, "the id must never enter bundle_owners"
    assert parts["index_store"].artifacts == {}


def test_a_reserved_id_is_refused_at_parking_time_not_at_admission():
    """Same timing rule as the broken-zip check: a pending creator hears the refusal NOW, on
    their own screen, instead of the package parking and dying weeks later on the operator's."""
    parker = FakeParker()
    svc, _ = service(creators=FakeCreators(state=PENDING_REVIEW), parker=parker)

    result = svc.submit(Submission(package=agentpkg("api"), token="tok"))

    assert result.status == BAD_REQUEST
    assert "reserved by the platform" in result.message
    assert parker.slots == {}, "a reserved id must never park"


# ────────────────────────────── the enterprise destination ──────────────────────────────
#
# An org publish is the SAME pipeline against a different shelf: same packing, same signing, same
# version rules, same lock. Two things differ, and both are load-bearing:
#
#   * the platform's review does not apply (the company already vouched for its own staff), and
#   * the bundle must not land in the public registry, ever.
#
# The second is the one worth most of these tests: it fails silently and it cannot be undone.

ORG = "org_82bdccbd70a0ffa7"
MEMBER = {"account_id": "acc-1", "orgs": [{"id": ORG, "role": "owner"}]}


def org_service(**kw):
    parts = {"authenticator": FakeAuth({"tok": MEMBER})}
    parts.update(kw)
    return service(**parts)


def test_an_org_publish_lands_in_the_org_registry_and_never_the_public_one():
    svc, parts = org_service()
    result = svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    assert result.status == OK, result.message
    public = parts["index_store"]
    assert public.index == {}, "the public registry was written to by an internal publish"
    assert public.artifacts == {}
    shelf = public.scopes[ORG]
    assert [b["id"] for b in shelf.index["bundles"]] == ["weather"]
    assert "weather-1.2.0.agentpkg" in shelf.artifacts


def test_an_org_publish_skips_the_review_that_gates_the_marketplace():
    """A creator with no roster admission still publishes INTERNALLY. On the public path the same
    creator gets 202 and parks — that contrast is the whole feature."""
    svc, _ = org_service(creators=FakeCreators(state=PENDING_REVIEW))
    result = svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    assert result.status == OK, result.message

    svc2, _ = org_service(creators=FakeCreators(state=PENDING_REVIEW))
    assert svc2.submit(Submission(package=agentpkg(), token="tok")).status == PENDING


def test_an_org_you_do_not_belong_to_is_refused_and_nothing_is_written():
    svc, parts = org_service()
    result = svc.submit(Submission(package=agentpkg(), token="tok", org_id="org_someone_else"))
    assert result.status == FORBIDDEN
    assert ORG in result.message  # says which orgs ARE yours, so the fix is obvious
    assert parts["index_store"].scopes == {}
    assert parts["index_store"].index == {}


def test_an_account_in_no_org_is_pointed_at_the_marketplace_instead():
    svc, _ = service(authenticator=FakeAuth({"tok": {"account_id": "acc-1"}}))
    result = svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    assert result.status == FORBIDDEN
    assert "marketplace" in result.message


def test_membership_is_read_from_the_token_never_from_the_request():
    """The account resolved from the token says which orgs are real. A submission naming an org
    the account is not in must not create that org's registry as a side effect."""
    svc, parts = service(authenticator=FakeAuth({"tok": {"account_id": "acc-1", "orgs": []}}))
    assert svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG)).status == FORBIDDEN
    assert ORG not in parts["index_store"].scopes


def test_the_org_registry_carries_a_root_signed_roster_naming_the_publisher():
    from agent_runtime.domain.bundle import parse_publisher_roster, roster_signing_payload
    from agent_runtime.infrastructure import signing

    vault = FakeVault()
    svc, parts = org_service(root_vault=vault)
    svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))

    block = parts["index_store"].scopes[ORG].index["publishers"]
    assert [r["id"] for r in block["roster"]] == ["c-abc"]
    roster = parse_publisher_roster(block)
    assert signing.verify(
        vault.public_key(),
        roster_signing_payload(roster.entries, roster.revoked, roster.issued),
        block["sig"],
    ), "the org roster is not verifiable with the pinned root key"


def test_a_second_publish_leaves_the_roster_untouched():
    """Idempotent: re-signing on every publish would churn the rows that vouch for everyone
    else's bundles, for no gain."""
    svc, parts = org_service()
    svc.submit(Submission(package=agentpkg(version="1.0.0"), token="tok", org_id=ORG))
    first = json.dumps(parts["index_store"].scopes[ORG].index["publishers"], sort_keys=True)
    svc.submit(Submission(package=agentpkg(version="1.1.0"), token="tok", org_id=ORG))
    second = json.dumps(parts["index_store"].scopes[ORG].index["publishers"], sort_keys=True)
    assert first == second


def test_without_a_root_key_an_org_publish_is_refused_rather_than_unverifiable():
    """Fail closed. An org index whose roster does not name the signer would be listed and refuse
    to install on every member's machine, with nothing anywhere saying why."""
    svc, parts = org_service(root_vault=None)
    result = svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    assert result.status == SERVER_ERROR
    assert parts["index_store"].scopes == {}


def test_the_public_path_still_needs_no_vault():
    """The marketplace's roster is the admin service's business; intake never signs one."""
    svc, parts = service(root_vault=None)
    result = svc.submit(Submission(package=agentpkg(), token="tok"))
    assert result.status == OK, result.message
    assert [b["id"] for b in parts["index_store"].index["bundles"]] == ["weather"]


# ────────────────────────────── reading a private shelf ──────────────────────────────
#
# `orgs/*` is carved out of the registry bucket's public-read grant, so this endpoint is the ONLY
# way a member reads their company's registry. What it must get right: who is asking, whether they
# belong, and handing back links that work without making the prefix readable to anyone who
# learns an org id.


def test_reading_an_org_shelf_needs_a_session():
    svc, _ = org_service()
    status, body = svc.org_index("nope", ORG)
    assert status == UNAUTHORIZED
    assert "bundles" not in body


def test_reading_someone_elses_org_is_refused():
    svc, _ = org_service()
    status, body = svc.org_index("tok", "org_not_mine")
    assert status == FORBIDDEN
    assert "bundles" not in body


def test_an_org_that_never_published_reads_as_empty_not_an_error():
    """The client merges shelves, so an empty one contributes nothing. An error here would make
    every member's store fail until somebody published."""
    svc, _ = org_service()
    status, body = svc.org_index("tok", ORG)
    assert status == OK
    assert body["bundles"] == []


def test_the_artifacts_come_back_presigned():
    svc, parts = org_service()
    svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    status, body = svc.org_index("tok", ORG)
    assert status == OK
    row = body["bundles"][0]
    assert row["url"].startswith("https://signed.example/weather-1.2.0.agentpkg")
    assert parts["index_store"].scopes[ORG].index["bundles"][0]["url"] == "weather-1.2.0.agentpkg"


def test_an_absolute_url_is_left_alone():
    """Someone else put it there; it is not ours to re-sign."""
    svc, parts = org_service()
    svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    shelf = parts["index_store"].scopes[ORG]
    shelf.index["bundles"][0]["url"] = "https://elsewhere.example/weather.agentpkg"
    _, body = svc.org_index("tok", ORG)
    assert body["bundles"][0]["url"] == "https://elsewhere.example/weather.agentpkg"


def test_the_roster_and_signatures_are_returned_verbatim():
    """A member verifies an org bundle exactly as strictly as a marketplace one, so this service
    must not be trusted to vouch for anything it did not sign."""
    svc, parts = org_service()
    svc.submit(Submission(package=agentpkg(), token="tok", org_id=ORG))
    stored = parts["index_store"].scopes[ORG].index
    _, body = svc.org_index("tok", ORG)
    assert body["publishers"] == stored["publishers"]
    assert body["bundles"][0]["sig"] == stored["bundles"][0]["sig"]
