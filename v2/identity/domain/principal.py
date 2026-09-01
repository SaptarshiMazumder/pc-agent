"""Principal — an authenticated caller, as the rest of the system is allowed to see one.

THE ACCOUNT ID IS THE IDENTITY, NOT THE EMAIL. Every table downstream (``usage``,
``credit_grants``, ``entitlements``), every state path (``<state_dir>/accounts/<acct>/…``), every
ownership check and every memory partition already keys on ``acct_<hex>``. Keeping that string as
the subject of everything identity issues is what makes this whole change non-destructive: no row
moves, no directory is renamed.

Email is an ATTRIBUTE here, deliberately. One account can end up with a local password, a Google
login and a Microsoft login, each asserting its own email; if email were the key, linking a second
provider would be a migration instead of a row.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Principal:
    """Who the caller is, once something has vouched for them."""

    #: ``acct_<hex>`` — the existing accounts row id. Becomes the token's ``sub``.
    account_id: str
    email: str = ""
    email_verified: bool = False
    #: What this caller may do. Space-delimited on the wire (OAuth2 convention), a tuple here.
    #: Issued from day one even though nothing enforces it yet: retrofitting a claim into tokens
    #: that are already in circulation is the expensive half, and costs nothing to emit now.
    scopes: tuple[str, ...] = field(default_factory=tuple)
    #: HOW they proved it — ``("pwd",)`` today, ``("google",)`` later. Carried so a future policy
    #: can demand a stronger method for a sensitive action without re-authenticating everything.
    amr: tuple[str, ...] = field(default_factory=tuple)
    #: The auth-session this principal belongs to (one login = one id). Survives refresh rotation,
    #: which is what makes it usable as the key for a future revocation list.
    session_id: str = ""
    #: ORG MEMBERSHIPS at mint time, as ``(org_id, role)`` pairs — the token's ``orgs`` claim.
    #: Carried in the token so the daemon learns membership with ZERO extra hops at connect
    #: (it already verifies tokens locally against JWKS). Stale by at most the access TTL,
    #: the same revocation latency the platform already accepts for account state. Empty for
    #: every personal-only account — the degenerate case costs nothing on the wire.
    orgs: tuple[tuple[str, str], ...] = field(default_factory=tuple)

    def with_scopes(self, scopes: tuple[str, ...]) -> "Principal":
        """A copy narrowed to ``scopes`` — the seam for downscoped tokens (an agent-app
        connection that may chat but not spend). Declared now so the shape exists; nothing
        narrows yet."""
        return Principal(
            account_id=self.account_id,
            email=self.email,
            email_verified=self.email_verified,
            scopes=scopes,
            amr=self.amr,
            session_id=self.session_id,
            orgs=self.orgs,
        )
