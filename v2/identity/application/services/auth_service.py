"""AuthService — login, refresh, logout. The orchestration, with no provider and no SQL in sight.

Everything here is expressed in terms of the five ports (provider, principal resolution, token
issuer, refresh store, account directory). That is what makes the Cognito swap a configuration
change: this file does not know whether the password was checked by PBKDF2 in our own table or by
somebody else's hosted user pool.

THE ONE SUBTLETY IS REFRESH. It has to do four things in a fixed order, and getting the order wrong
is how refresh flows leak:

  1. consume the presented token (single-use; the store raises on reuse)
  2. on reuse -> revoke the whole family, then fail
  3. re-read the ACCOUNT, not the old token's claims — a deactivated account, or one whose email
     changed, must not be able to mint a fresh 10-minute token off a 30-day-old assertion
  4. issue a new pair inside the SAME family

Step 3 is the one people skip. Skipping it means a refresh token is effectively a permanent grant
of whatever the user was when they first signed in, and deactivating an account stops working.
"""

from __future__ import annotations

import time
import uuid

from identity.application.interfaces.account_directory import AccountDirectory
from identity.application.interfaces.identity_provider import IdentityProvider
from identity.application.interfaces.refresh_store import RefreshStore
from identity.application.interfaces.token_issuer import TokenIssuer
from identity.application.services.principal_service import DEFAULT_SCOPES, PrincipalService
from identity.domain.errors import (
    AccountDisabled,
    AuthenticationFailed,
    RefreshReuseDetected,
    TokenInvalid,
)
from identity.domain.principal import Principal
from identity.domain.token import AccessClaims, TokenPair


class AuthService:
    def __init__(
        self,
        *,
        provider: IdentityProvider,
        principals: PrincipalService,
        issuer: TokenIssuer,
        refresh: RefreshStore,
        directory: AccountDirectory,
        access_ttl_s: int = 600,
        audience: tuple[str, ...] = ("agentd-daemon", "agentd-proxy"),
        clock=time.time,
        org_resolver=None,
    ):
        self._provider = provider
        self._principals = principals
        self._issuer = issuer
        self._refresh = refresh
        self._directory = directory
        self._access_ttl_s = int(access_ttl_s)
        self._audience = audience
        self._clock = clock
        # ``account_id -> ((org_id, role), ...)`` — the org memberships that ride the access
        # token as the ``orgs`` claim (enterprise tenancy E1). A CALLABLE, not a table, for the
        # same reason the other five ports are ports: identity does not know where memberships
        # live. Resolved at EVERY mint (login AND refresh), so a removed member's next token —
        # at most one access-TTL away — no longer carries the org. None = no orgs anywhere,
        # which is every deployment that has not composed one in: byte-identical behaviour.
        self._org_resolver = org_resolver

    # -- sign in / sign up ------------------------------------------------------------------

    def login(self, *, email: str, password: str, client_id: str = "", device_label: str = "") -> TokenPair:
        assertion = self._provider.authenticate(email=email, password=password)
        principal = self._principals.resolve(assertion)
        return self._issue_pair(principal, client_id=client_id, device_label=device_label)

    def register(self, *, email: str, password: str, client_id: str = "", device_label: str = "") -> TokenPair:
        """Create the credential and sign in, in one step.

        Signing in as part of signup is not a convenience: making the caller do a second round trip
        means the password crosses the wire twice for one intent, and gives a failure mode
        ("account created but login failed") that has no good UI.
        """
        assertion = self._provider.register(email=email, password=password)
        principal = self._principals.resolve(assertion)
        return self._issue_pair(principal, client_id=client_id, device_label=device_label)

    def login_external(
        self, *, assertion, client_id: str = "", device_label: str = ""
    ) -> TokenPair:
        """Sign in from an already-VERIFIED external assertion (Google, Microsoft, Cognito).

        Takes an Assertion rather than raw provider output on purpose: validating an id_token is
        the OIDC adapter's job and deciding which account it maps to is PrincipalService's, so by
        the time execution reaches here there is nothing provider-specific left. That is what makes
        this method identical for every provider we will ever add — and why `login` above and this
        one converge on the same two lines.
        """
        principal = self._principals.resolve(assertion)
        return self._issue_pair(principal, client_id=client_id, device_label=device_label)

    # -- refresh ----------------------------------------------------------------------------

    def refresh_tokens(self, *, refresh_token: str, client_id: str = "", device_label: str = "") -> TokenPair:
        try:
            record = self._refresh.consume(refresh_token)
        except RefreshReuseDetected as reuse:
            # The token was already rotated, so two parties hold it and we cannot tell which is
            # the thief. Killing the family signs both out; anything narrower leaves the attacker
            # holding a working credential.
            family = getattr(reuse, "family_id", "") or ""
            if family:
                self._refresh.revoke_family(family)
            raise

        # Re-read the account (see the module note, step 3). The old token proves the session was
        # real; it does not prove the account still is.
        account = self._directory.find_by_id(record.account_id)
        if account is None:
            self._refresh.revoke_family(record.family_id)
            raise TokenInvalid("the account behind this session no longer exists")
        if not account.active:
            self._refresh.revoke_family(record.family_id)
            raise AccountDisabled("this account is deactivated")

        principal = Principal(
            account_id=account.account_id,
            email=account.email,
            scopes=DEFAULT_SCOPES,
            amr=("refresh",),
            session_id=record.family_id,
        )
        return self._issue_pair(
            principal,
            family_id=record.family_id,
            parent_row_id=record.row_id,
            client_id=client_id or record.client_id,
            device_label=device_label or record.device_label,
        )

    # -- a session of one's own -------------------------------------------------------------

    def derive_session(
        self, *, access_token: str, client_id: str = "", device_label: str = ""
    ) -> TokenPair:
        """Mint a NEW, INDEPENDENT session for the account a live access token already proves.

        WHAT IT IS FOR. A window opened by the desktop app is handed an access token on its launch
        URL and nothing else. It cannot renew — renewing needs a refresh token — so ten minutes
        later it goes anonymous, which the daemon does not refuse: it accepts the reconnect with no
        account, and the user watches their agents disappear. The old answer was for the shell to
        keep pushing fresh access tokens down forever. This is the better one: the window mints its
        OWN key, once, and then looks after itself like every other client.

        A NEW FAMILY, NEVER A COPY. Refresh tokens are single-use and rotating, so two holders of
        one token is not a shortcut — the second to spend it looks exactly like theft, and the
        server revokes the family and signs both out. Every holder therefore needs a chain of its
        own. That is also what makes a single window revocable on its own, and what makes it show
        up in the account's device list as itself rather than as a second copy of the shell.

        IT PROVES THE ACCOUNT, IT DOES NOT INHERIT IT. The presented token must still be valid —
        an expired one proves nothing, and accepting it would make expiry meaningless. The
        principal is then re-read from the directory rather than copied from the token's claims,
        for the same reason refresh does it (step 3 in the module note): a deactivated account must
        not be able to mint a fresh session off an assertion made before it was deactivated.

        THE COST, STATED. A live access token can now buy a long-lived one, so a stolen access
        token is worth more than it was — it is a way in for as long as the theft goes unnoticed,
        instead of ten minutes. `ttl_s` is what bounds that: callers give a window session a
        shorter life than a password login gets, so an unattended one dies on its own.
        """
        claims = self._issuer.verify(access_token)  # raises TokenInvalid when expired or forged

        account = self._directory.find_by_id(claims.account_id)
        if account is None:
            raise TokenInvalid("the account behind this token no longer exists")
        if not account.active:
            raise AccountDisabled("this account is deactivated")

        principal = Principal(
            account_id=account.account_id,
            email=account.email,
            scopes=DEFAULT_SCOPES,
            # How this session came to exist, kept on the token: it was derived from another
            # session rather than from a password, and an auditor should be able to see that.
            amr=("derived",),
        )
        # No family_id and no parent_row_id: this is a NEW chain, not a rotation of the caller's.
        return self._issue_pair(
            principal, client_id=client_id, device_label=device_label
        )

    # -- sign out ---------------------------------------------------------------------------

    def logout(self, *, refresh_token: str) -> bool:
        """End THIS device's session. Best-effort by design: a token that is already unknown or
        expired means the caller is signed out, which is what they asked for — reporting an error
        would leave a client stuck unable to complete a sign-out."""
        try:
            record = self._refresh.consume(refresh_token)
        except RefreshReuseDetected as reuse:
            family = getattr(reuse, "family_id", "") or ""
            if family:
                self._refresh.revoke_family(family)
            return True
        except TokenInvalid:
            return False
        self._refresh.revoke_family(record.family_id)
        return True

    def logout_all(self, *, account_id: str) -> int:
        """Every device. Also the correct response to a password change."""
        return self._refresh.revoke_account(account_id)

    def sessions(self, *, account_id: str) -> list[dict]:
        return [
            {
                "family_id": r.family_id,
                "client_id": r.client_id,
                "device_label": r.device_label,
                "issued_at": r.issued_at,
                "expires_at": r.expires_at,
            }
            for r in self._refresh.list_families(account_id)
        ]

    # -- verification -----------------------------------------------------------------------

    def verify_access(self, token: str) -> AccessClaims:
        """Local signature check. No database, no network — this is the method that keeps the
        accounts service off the hot path once the daemon and proxy adopt it."""
        return self._issuer.verify(token)

    def jwks(self) -> dict:
        return self._issuer.public_jwks()

    # -- internals --------------------------------------------------------------------------

    def _issue_pair(
        self,
        principal: Principal,
        *,
        family_id: str = "",
        parent_row_id: int | None = None,
        client_id: str = "",
        device_label: str = "",
    ) -> TokenPair:
        # The session id must be settled BEFORE the access token is signed: `sid` is a claim, so
        # minting the refresh row afterwards would either omit it or force a second signature.
        session_id = family_id or uuid.uuid4().hex
        # Memberships are read at mint time — never copied forward from an old token's claims,
        # the same re-read rule step 3 of refresh applies to the account itself. A resolver
        # failure means NO orgs on this token (fail closed), not a failed sign-in: a person
        # must still be able to log into their own account when the org tables are having a day.
        orgs: tuple = ()
        if self._org_resolver is not None:
            try:
                orgs = tuple(self._org_resolver(principal.account_id) or ())
            except Exception:  # noqa: BLE001 — membership is an extra, the login is the point
                orgs = ()
        scoped = Principal(
            account_id=principal.account_id,
            email=principal.email,
            email_verified=principal.email_verified,
            scopes=principal.scopes or DEFAULT_SCOPES,
            amr=principal.amr,
            session_id=session_id,
            orgs=orgs,
        )
        access, claims = self._issuer.issue(scoped, audience=self._audience)
        plaintext, _record = self._refresh.issue(
            account_id=principal.account_id,
            family_id=session_id,
            parent_row_id=parent_row_id,
            client_id=client_id,
            device_label=device_label,
        )
        expires_in = max(1, int(claims.expires_at - self._clock()))
        return TokenPair(
            access_token=access,
            refresh_token=plaintext,
            expires_in=expires_in,
            account_id=principal.account_id,
            email=scoped.email,
            session_id=session_id,
        )


__all__ = ["AuthService", "AuthenticationFailed"]
