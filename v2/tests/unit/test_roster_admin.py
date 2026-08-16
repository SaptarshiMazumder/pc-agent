"""Admission as a service: the roster is re-signed in the cloud, and Bob's one click completes.

What is pinned here, in order of what it would cost to lose:

  * FAIL-CLOSED ADMIN AUTH. An empty allowlist refuses everyone — a deployment that forgot to
    configure admins must not accept roster changes from anyone who happens to be signed in.
  * THE ORDER: roster written (and verifiable!) -> creator flipped -> parked publishes complete.
    Any other order has a window where something is signed with a key clients do not trust.
  * REAL SIGNATURES. The fakes stop at storage; the roster the service writes is verified with
    the actual ed25519 code every installed client runs. A test that trusted a fake signature
    would pass while shipping an unverifiable roster.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.interfaces.publish_intake import (
    FORBIDDEN,
    LISTED,
    OK,
    PENDING_REVIEW,
    UNAUTHORIZED,
    Creator,
)
from agent_runtime.application.services.roster_admin_service import (
    NOT_FOUND,
    RosterAdminService,
)
from agent_runtime.domain.bundle import parse_publisher_roster
from agent_runtime.infrastructure import signing
from agent_runtime.infrastructure.marketplace.trust import verify_roster

ROOT_PRIVATE, ROOT_PUBLIC = signing.generate_keypair()


# ────────────────────────────── fakes ──────────────────────────────


class FakeAuth:
    def __init__(self, accounts=None):
        self._accounts = accounts if accounts is not None else {
            "admin-tok": {"account_id": "acc-admin", "email": "Admin@Example.com"},
            "user-tok": {"account_id": "acc-user", "email": "user@example.com"},
        }

    def account(self, token):
        return self._accounts.get(token)


class FakeVault:
    def __init__(self, private=ROOT_PRIVATE, public=ROOT_PUBLIC):
        self._private, self._public = private, public

    def private_key(self):
        if not self._private:
            raise RuntimeError("the root key vault is empty")
        return self._private

    def public_key(self):
        return self._public


class FakeDirectory:
    def __init__(self, pending=()):
        self._pending = list(pending)
        self.admitted: list[str] = []
        self.revoked: list[str] = []
        self.events: list[str] = []  # shared order log, appended by the store too

    def pending(self):
        return list(self._pending)

    def admit(self, creator_id):
        self.admitted.append(creator_id)
        self.events.append(f"admit:{creator_id}")

    def revoke(self, creator_id):
        if creator_id == "c-ghost":
            raise KeyError(creator_id)
        self.revoked.append(creator_id)


class FakeStore:
    def __init__(self, index=None, events=None):
        self.index = index if index is not None else {}
        self.events = events if events is not None else []

    def read_index(self):
        import json

        return json.loads(json.dumps(self.index)) if self.index else {}

    def write_index(self, index):
        self.index = index
        self.events.append("index")


class FakeLock:
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass


class FakeIntake:
    """Records completion calls; the real completion path is covered in test_publish_intake."""

    def __init__(self):
        self.completed: list = []

    def complete_parked(self, creator):
        from agent_runtime.application.interfaces.publish_intake import IntakeResult

        self.completed.append(creator)
        return [IntakeResult(OK, "published", bundle_id="weather", version="1.2.0")]


BOB = Creator(id="c-bob", account_id="acc-b", name="Bob", state=PENDING_REVIEW, public_key="BOBKEY")


def service(**kw):
    parts = {
        "authenticator": FakeAuth(),
        "admins": ["admin@example.com"],
        "creators": FakeDirectory(pending=[BOB]),
        "vault": FakeVault(),
        "index_store": FakeStore(),
        "lock": FakeLock(),
        "intake": FakeIntake(),
        "parker": None,
        "now": lambda: "2026-08-09T00:00:00+00:00",
    }
    parts.update(kw)
    parts["index_store"].events = parts["creators"].events  # one shared order log
    return RosterAdminService(**parts), parts


# ────────────────────────────── auth ──────────────────────────────


def test_no_session_is_401():
    svc, _ = service()
    assert svc.admit("nope").status == UNAUTHORIZED


def test_a_signed_in_non_admin_is_403():
    svc, _ = service()
    assert svc.admit("user-tok").status == FORBIDDEN


def test_an_empty_allowlist_refuses_even_a_valid_account():
    """Fail-closed: no configured admins means NO admins, not 'everyone signed in'."""
    svc, _ = service(admins=[])
    assert svc.admit("admin-tok").status == FORBIDDEN


def test_admins_match_case_insensitively_on_email_or_account_id():
    by_email, _ = service(admins=["ADMIN@example.COM"])
    assert by_email.admit("admin-tok").status == OK
    by_account, _ = service(admins=["acc-admin"])
    assert by_account.admit("admin-tok").status == OK


# ────────────────────────────── admit ──────────────────────────────


def test_admit_writes_a_roster_every_installed_client_can_verify():
    """The whole point of the vault: what it signs must verify with the ed25519 code clients
    actually run, against the public key their installers pin."""
    svc, parts = service()

    result = svc.admit("admin-tok")

    assert result.status == OK
    index = parts["index_store"].index
    roster = parse_publisher_roster(index["publishers"])
    assert [e.id for e in roster.entries] == ["c-bob"]
    assert roster.entries[0].key == "BOBKEY"
    assert verify_roster(roster, ROOT_PUBLIC)
    assert index["schema"] == 2
    assert index["publisher_key"] == ROOT_PUBLIC


def test_the_roster_goes_live_before_the_creator_is_flipped():
    """Flipped first, the service would sign bundles no client can verify — fail-closed failures
    on every machine with nothing anywhere saying why. The shared event log pins the order."""
    svc, parts = service()

    svc.admit("admin-tok")

    assert parts["creators"].events == ["index", "admit:c-bob"]


def test_admit_completes_the_parked_publishes_as_a_listed_creator():
    svc, parts = service()

    result = svc.admit("admin-tok")

    completed = parts["intake"].completed
    assert [c.id for c in completed] == ["c-bob"]
    assert completed[0].state == LISTED  # the intake must not re-refuse its own completion
    assert "weather 1.2.0" in result.message


def test_admitting_a_name_nobody_is_waiting_under_is_404():
    svc, _ = service()
    result = svc.admit("admin-tok", ["c-nope"])
    assert result.status == NOT_FOUND
    assert "c-nope" in result.message


def test_admitting_with_an_empty_queue_is_ok_and_says_so():
    svc, _ = service(creators=FakeDirectory(pending=[]))
    result = svc.admit("admin-tok")
    assert result.status == OK
    assert "nothing to admit" in result.message


def test_an_empty_vault_stops_admission_before_anything_changes():
    svc, parts = service(vault=FakeVault(private=""))
    try:
        svc.admit("admin-tok")
        raise AssertionError("an unsigned roster must never be written")
    except RuntimeError:
        pass
    assert parts["creators"].admitted == []
    assert parts["index_store"].index == {}


# ────────────────────────────── revoke ──────────────────────────────


def test_revoke_re_signs_with_the_creator_on_the_revoked_list():
    existing = {
        "schema": 2,
        "publishers": {
            "roster": [{"id": "c-bob", "name": "Bob", "key": "BOBKEY", "added": "2026-01-01"}],
            "revoked": [],
            "issued": "2026-01-01",
        },
    }
    svc, parts = service(index_store=FakeStore(existing))

    result = svc.revoke("admin-tok", "c-bob")

    assert result.status == OK
    roster = parse_publisher_roster(parts["index_store"].index["publishers"])
    assert "c-bob" in roster.revoked
    assert [e.id for e in roster.entries] == ["c-bob"]  # the row STAYS, for the record
    assert verify_roster(roster, ROOT_PUBLIC)
    assert parts["creators"].revoked == ["c-bob"]


def test_revoking_someone_not_in_the_directory_still_revokes_on_the_roster():
    """An operator-era creator admitted by hand has a roster row and no table row. The roster
    edit is what blocks installs, so a missing table row is a note, not a failure."""
    svc, parts = service()
    result = svc.revoke("admin-tok", "c-ghost")
    assert result.status == OK
    roster = parse_publisher_roster(parts["index_store"].index["publishers"])
    assert "c-ghost" in roster.revoked


# ──────────────────── the accounts service is the authority ────────────────────
#
# The admin list used to be an environment variable frozen into this service at construction, in a
# Lambda that stays warm for hours. Two things were wrong with that at once: promoting someone in
# the dashboard did not reach a running container, and "who is an admin" had two answers that could
# disagree. Accounts owns identity, so accounts owns this — asked over the SAME call path that
# already turns a token into an account.


class OracleAuth(FakeAuth):
    """An authenticator that can also answer "is this an admin", like the real one."""

    def __init__(self, verdicts, **kw):
        super().__init__(**kw)
        self._verdicts = verdicts
        self.asked = []

    def is_admin(self, token):
        self.asked.append(token)
        return self._verdicts.get(token)


def test_the_accounts_answer_wins_over_the_configured_list():
    """Someone promoted in the dashboard is an admin here immediately, without a redeploy — even
    though the configured list has never heard of them."""
    auth = OracleAuth({"user-tok": True})
    svc, _ = service(authenticator=auth, admins=["admin@example.com"])
    assert svc.authorize("user-tok") is None
    assert auth.asked == ["user-tok"]


def test_the_accounts_answer_also_wins_when_it_is_no():
    """Final in BOTH directions. A demotion that only removed the dashboard row would be no
    demotion at all if a stale configured list could still let them in."""
    auth = OracleAuth({"admin-tok": False})
    svc, _ = service(authenticator=auth, admins=["admin@example.com"])
    assert svc.authorize("admin-tok").status == FORBIDDEN


def test_it_falls_back_to_config_when_accounts_cannot_answer():
    """None is not False. An accounts hiccup — or a build that predates /admin/whoami — must not
    lock every admin out of a registry they are configured to administer."""
    auth = OracleAuth({"admin-tok": None})
    svc, _ = service(authenticator=auth, admins=["admin@example.com"])
    assert svc.authorize("admin-tok") is None
    assert svc.authorize("user-tok").status == FORBIDDEN


def test_an_authenticator_without_the_oracle_still_works():
    """The offline CLI path builds this with a plain authenticator. It must keep using the
    configured list rather than crashing on a missing method."""
    svc, _ = service(authenticator=FakeAuth(), admins=["admin@example.com"])
    assert svc.authorize("admin-tok") is None
    assert svc.authorize("user-tok").status == FORBIDDEN


# ──────────────────────────── the full creator listing ────────────────────────────


def test_creators_lists_everyone_not_just_those_waiting():
    """`pending` answers the approval CLI's question. A dashboard also has to show who was already
    admitted and who was revoked, or a healthy registry reads as "there are no creators"."""

    class Directory(FakeDirectory):
        def all(self):
            return [
                {"creator_id": "c-bob", "name": "Bob", "state": PENDING_REVIEW, "wrapped": True},
                {"creator_id": "c-ann", "name": "Ann", "state": LISTED, "wrapped": True},
            ]

    svc, _ = service(creators=Directory(pending=[BOB]))
    refusal, rows = svc.creators("admin-tok")
    assert refusal is None
    assert {r["creator_id"] for r in rows} == {"c-bob", "c-ann"}
    # Parked packages belong to the ones still waiting; showing them for an admitted creator would
    # imply a decision is still outstanding when admission already published them.
    assert all(r["parked"] == [] for r in rows)


def test_creators_refuses_a_non_admin():
    svc, _ = service()
    refusal, rows = svc.creators("user-tok")
    assert refusal.status == FORBIDDEN
    assert rows == []


def test_creators_degrades_to_pending_on_an_older_directory():
    """A directory implementation without `all()` must render the page it can, not fail it."""
    svc, _ = service()  # FakeDirectory has no all()
    refusal, rows = svc.creators("admin-tok")
    assert refusal is None
    assert [r["creator_id"] for r in rows] == ["c-bob"]
