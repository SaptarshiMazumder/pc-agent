"""RosterAdminService — admission and revocation as a SERVICE, not a laptop.

WHAT MOVED, AND WHY. Admitting a creator means re-signing the roster with the platform root key,
and the root key used to exist in exactly one place: a file on one operator's machine. Maximum
security, minimum operability — no other admin could approve anyone, and losing that one disk
would have frozen admissions forever. This service holds the root key in a VAULT (KMS-wrapped,
decrypted only inside the signing call) so that ANY authorised admin, from any machine, can admit
with one authenticated request. The explicit trade: an attacker inside the AWS account can now
sign rosters while inside. That is the standard posture of every real store, and the registry,
the Lambda and the tables already live in that same account.

WHO MAY CALL THIS. An allowlist of admin identities, injected — never derived here. The check is
two layers: the token must resolve to a real account (the same Authenticator publishing uses),
and that account must be on the list. An empty allowlist means NOBODY, fail-closed: a deployment
that forgot to configure admins must refuse admissions, not accept them from anyone signed in.

THE ORDER INSIDE admit() IS LOAD-BEARING, same rule as everywhere in publishing:

    1. roster re-signed and WRITTEN to the registry   <- clients now trust the creator's key
    2. creator flipped to `listed`                    <- the intake service now accepts them
    3. their PARKED packages published                <- signed with a key clients already trust

Flip before write, and the service signs bundles no installed client can verify — fail-closed
failures on every machine with nothing anywhere saying why. Publish parked before flip, and the
intake refuses its own completion. The one order above is the only one with no such window.
"""

from __future__ import annotations

import logging

from agent_runtime.application.interfaces.publish_intake import (
    FORBIDDEN,
    LISTED,
    OK,
    PENDING_REVIEW,
    UNAUTHORIZED,
    IntakeResult,
)

log = logging.getLogger("agentd")

NOT_FOUND = 404


class RosterAdminService:
    def __init__(
        self,
        authenticator,
        admins,
        creators,
        vault,
        index_store,
        lock,
        intake=None,
        parker=None,
        now=None,
    ):
        """:param admins: iterable of admin identities — account ids and/or emails, matched
        case-insensitively. The FALLBACK, used when no authority can be reached. Empty => every
        admin call is refused (fail-closed).
        :param vault: a RootKeyVault. The ONLY consumer of the root private key in this process.
        :param intake: the PublishIntakeService, for completing parked publishes on admit. None =>
        admission still works; parked packages wait for the author's next publish.
        :param now: () -> ISO-8601 string, injected so a signed roster's `issued` is testable."""
        self._auth = authenticator
        self._admins = frozenset(str(a).strip().lower() for a in admins if str(a).strip())
        self._creators = creators
        self._vault = vault
        self._store = index_store
        self._lock = lock
        self._intake = intake
        self._parker = parker
        self._now = now or self._default_now

    # ================================================================== auth
    def authorize(self, token: str) -> IntakeResult | None:
        """None when the caller is an admin; the refusal to return otherwise.

        TWO SOURCES, IN THIS ORDER, and the order is the point. The accounts service is the
        platform's ONE authority on who is an admin — it is where the dashboard promotes and
        demotes people — so it is asked first and its answer is final in BOTH directions. Only
        when it cannot answer at all (an outage, or a build that predates the admin surface) does
        this fall back to the identities this service was configured with.

        Without that ordering the two would disagree the moment anyone was promoted in the
        dashboard: this service would keep enforcing a list frozen at container start, inside a
        Lambda that stays warm for hours. Falling back rather than failing closed on an outage is
        deliberate too — the configured list is deploy data, so it is a trustworthy answer, just a
        staler one than asking.
        """
        account = self._auth.account(token)
        if not account:
            return IntakeResult(UNAUTHORIZED, "not signed in, or the session expired.")
        decided = getattr(self._auth, "is_admin", lambda _t: None)(token)
        if decided is None:
            identities = {
                str(account.get(field) or "").strip().lower()
                for field in ("account_id", "id", "email")
            } - {""}
            decided = bool(identities & self._admins)
        if not decided:
            # Deliberately the same words whether the list is empty or the caller is just not on
            # it: "who administers this registry" is not information for a non-admin.
            return IntakeResult(FORBIDDEN, "this account is not a registry admin.")
        return None

    # ================================================================== queries
    def pending(self, token: str) -> tuple[IntakeResult | None, list[dict]]:
        refusal = self.authorize(token)
        if refusal:
            return refusal, []
        out = []
        for creator in self._creators.pending():
            parked = self._parker.parked(creator.id) if self._parker is not None else []
            out.append(
                {
                    "creator_id": creator.id,
                    "name": creator.name,
                    "public_key": creator.public_key,
                    "parked": [
                        {"bundle_id": p.bundle_id, "size": p.size, "parked_at": p.parked_at}
                        for p in parked
                    ],
                }
            )
        return None, out

    def creators(self, token: str) -> tuple[IntakeResult | None, list[dict]]:
        """EVERY creator and their state — what an admin dashboard lists.

        `pending()` answers "who is waiting", which is the right question for the approval CLI and
        the wrong one for a dashboard: a reviewer also needs to see who was already admitted and
        who was revoked, or the page reads as "there are no creators" on a healthy registry.

        Parked packages are attached to the pending ones only. Listing them for an admitted
        creator would be misleading — admission publishes whatever was parked, so anything still
        showing there afterwards is not waiting on a decision.
        """
        refusal = self.authorize(token)
        if refusal:
            return refusal, []
        lister = getattr(self._creators, "all", None)
        if lister is None:
            # A directory implementation that predates this listing. Degrade to what every
            # implementation has rather than failing the page.
            _, waiting = self.pending(token)
            return None, waiting
        rows = lister()
        for row in rows:
            if row.get("state") == PENDING_REVIEW and self._parker is not None:
                row["parked"] = [
                    {"bundle_id": p.bundle_id, "size": p.size, "parked_at": p.parked_at}
                    for p in self._parker.parked(row["creator_id"])
                ]
            else:
                row["parked"] = []
        return None, rows

    # ================================================================== admit
    def admit(self, token: str, creator_ids: list[str] | None = None) -> IntakeResult:
        """Admit the named pending creators (all of them when none are named)."""
        refusal = self.authorize(token)
        if refusal:
            return refusal

        waiting = {c.id: c for c in self._creators.pending()}
        wanted = [i for i in (creator_ids or []) if i]
        if wanted:
            missing = [i for i in wanted if i not in waiting]
            if missing:
                return IntakeResult(
                    NOT_FOUND, f"not pending (or unknown): {', '.join(sorted(missing))}"
                )
            chosen = [waiting[i] for i in wanted]
        else:
            chosen = list(waiting.values())
        if not chosen:
            return IntakeResult(OK, "nothing to admit.")

        for creator in chosen:
            if not creator.public_key:
                return IntakeResult(
                    NOT_FOUND, f"{creator.id} has no public key recorded - cannot admit them."
                )

        # 1. The roster, re-signed and live. Under the index lock: the roster rides inside
        # index.json, so this is the same read-modify-write every publish serialises on.
        self._rewrite_roster(
            lambda block, builder, issued, private, public: self._with_all(
                block, builder, chosen, issued, private, public
            )
        )

        # 2. Only now are they listed — the key clients trust went first.
        for creator in chosen:
            self._creators.admit(creator.id)
            log.info("admitted creator %s (%s)", creator.id, creator.name)

        # 3. Their parked uploads become real publishes, each under its own lock.
        published: list[str] = []
        failed: list[str] = []
        if self._intake is not None:
            for creator in chosen:
                for result in self._intake.complete_parked(self._listed(creator)):
                    label = f"{result.bundle_id} {result.version}".strip()
                    (published if result.status == OK else failed).append(
                        label if result.status == OK else f"{label}: {result.message}"
                    )

        message = f"admitted {len(chosen)} creator(s)."
        if published:
            message += f" Published from parking: {', '.join(published)}."
        if failed:
            message += f" Parked publishes that did not list: {'; '.join(failed)}"
        return IntakeResult(OK, message)

    # ================================================================== revoke
    def revoke(self, token: str, creator_id: str) -> IntakeResult:
        refusal = self.authorize(token)
        if refusal:
            return refusal
        creator_id = (creator_id or "").strip()
        if not creator_id:
            return IntakeResult(NOT_FOUND, "revoke needs a creator_id.")

        self._rewrite_roster(
            lambda block, builder, issued, private, public: builder.without_publisher(
                block,
                publisher_id=creator_id,
                issued=issued,
                root_private_b64=private,
                root_public_b64=public,
            )
        )
        try:
            self._creators.revoke(creator_id)
        except KeyError:
            # Not in the directory (an operator-era creator admitted by hand). The roster edit
            # above is the part that actually blocks installs, so this is a note, not a failure.
            log.info("revoked %s: on the roster only, not in the creator directory", creator_id)
        return IntakeResult(
            OK,
            f"revoked {creator_id}. Clients refuse everything they signed on the next index "
            "fetch. Already-installed copies stay installed - this stops new installs, it is "
            "not a remote uninstall.",
        )

    # ================================================================== internals
    # ================================================================== unlist
    def unlist(self, token: str, bundle_ids: list[str]) -> IntakeResult:
        """Take agents OFF the marketplace: their rows leave index.json.

        WHAT THIS IS AND IS NOT. It removes the LISTING, not the artifacts and not the installs.
        No store shows the agent and no client can resolve it, so nothing new can be installed —
        but the `.agentpkg` and its installers stay in the bucket, and copies already on machines
        are untouched. That is what makes this reversible: republishing the same version puts the
        row back, pointing at files that never went anywhere.

        DELETING THE ARTIFACTS IS DELIBERATELY NOT HERE. `agentd bundle unlist --purge-artifacts`
        does that, from an operator's machine, because it is the one step with no way back and a
        dashboard button is the wrong shape for it.

        Under the SAME LOCK as publishing and roster edits: index.json is one document, and an
        unlist racing a publish would otherwise silently resurrect the row it just removed. Writing
        through the index store also regenerates catalog.json, so the public storefront stops
        showing the card in the same operation rather than on someone's next publish.
        """
        refusal = self.authorize(token)
        if refusal:
            return refusal
        wanted = list(dict.fromkeys(i for i in (bundle_ids or []) if i))
        if not wanted:
            return IntakeResult(NOT_FOUND, "name at least one bundle to unlist.")

        with self._lock:
            index = self._store.read_index() or {}
            rows = [b for b in (index.get("bundles") or []) if isinstance(b, dict)]
            present = {str(b.get("id") or "") for b in rows}
            missing = [i for i in wanted if i not in present]
            if missing:
                known = ", ".join(sorted(present)) or "none"
                return IntakeResult(
                    NOT_FOUND,
                    f"not in this registry: {', '.join(sorted(missing))} (it lists: {known})",
                )
            removed = [
                f"{b.get('id')} {b.get('version')}".strip()
                for b in rows
                if str(b.get("id") or "") in set(wanted)
            ]
            index["bundles"] = [b for b in rows if str(b.get("id") or "") not in set(wanted)]
            self._store.write_index(index)

        log.info("unlisted %s", ", ".join(removed))
        return IntakeResult(
            OK,
            f"unlisted {', '.join(removed)}. The listing is gone from the marketplace; the files "
            "are kept, so republishing the same version restores it. Copies already installed are "
            "unaffected.",
        )

    def _rewrite_roster(self, mutate) -> None:
        """Read index -> mutate its publishers block -> re-sign -> write. Under the lock, because
        index.json is one document and publishes are rewriting it too."""
        from agent_runtime.infrastructure.marketplace import roster_builder

        private = self._vault.private_key()  # raises when the vault is empty — deliberately
        public = self._vault.public_key()
        with self._lock:
            index = self._store.read_index() or {}
            block = index.get("publishers") or {}
            block = mutate(block, roster_builder, self._now(), private, public)
            index["publishers"] = block
            index["schema"] = 2
            # The builder records the root PUBLIC key in the block; the index's top-level
            # publisher_key must be the same key or clients see two answers to one question.
            if public:
                index["publisher_key"] = public
            self._store.write_index(index)

    @staticmethod
    def _with_all(block, builder, creators, issued, private, public):
        for creator in creators:
            block = builder.with_publisher(
                block,
                publisher_id=creator.id,
                name=creator.name,
                key=creator.public_key,
                issued=issued,
                root_private_b64=private,
                root_public_b64=public,
            )
        return block

    @staticmethod
    def _listed(creator):
        """The creator as the intake must see them during completion. The directory row IS
        already flipped by now; this just avoids a re-read for a field we know."""
        from dataclasses import replace

        return replace(creator, state=LISTED)

    @staticmethod
    def _default_now() -> str:
        from datetime import UTC, datetime

        return datetime.now(UTC).isoformat(timespec="seconds")
