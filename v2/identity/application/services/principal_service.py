"""Turning a provider's assertion into one of OUR accounts.

This is the only code that decides "which account is this person?", and it is deliberately separate
from the thing that checks passwords. A provider proves *who authenticated*; this decides *whose
account that is*. Merging the two would mean every new provider re-implements the linking rules,
and the linking rules are where account takeover lives.

THE RESOLUTION ORDER, AND WHY:

  1. An existing LINK for ``(provider, subject)`` wins outright. This is the steady state — after
     someone's first login, nothing else is ever consulted, so a later email change cannot detach
     them from their account.

  2. Otherwise, if the provider VERIFIED the email and we already have an account with it, link to
     that account. This is what makes "I signed up with a password, now I click Sign in with
     Google" do the obvious thing instead of silently creating a second account with a second
     credit balance.

  3. If the email is TAKEN but the provider would not vouch for it, refuse — see below.

  4. Otherwise create a new account and link it.

Step 2 is gated on ``email_verified`` and that gate is the whole security of this function. An
unverified email is a string the caller typed; honouring it would let anyone claim any account by
registering with its address at a sloppy provider.

STEP 3 IS WHAT THAT GATE USED TO COST. Failing the check fell through to step 4, which minted a
second account on the same address — its own credits, its own workspace, its own orgs — and said
nothing about it. The person signs in with Google, lands in an empty account where their work used
to be, and has no way to find out the first one is still there. A refusal they can act on is worth
more than a silent duplicate they cannot, so an email that is already taken and not verified by
the provider stops here rather than quietly forking the user in two.
"""

from __future__ import annotations

import time

from identity.application.interfaces.account_directory import AccountDirectory, AccountRecord
from identity.application.interfaces.identity_link_store import IdentityLinkStore
from identity.application.interfaces.identity_provider import Assertion
from identity.domain.errors import AccountDisabled
from identity.domain.principal import Principal

#: What a normal interactive login may do. Space-delimited on the wire.
#: `chat` = talk to agents, `spend` = incur metered model cost. Nothing enforces these yet
#: (see domain/principal.py) — they are emitted so that enforcing them later does not require
#: re-issuing tokens that are already in circulation.
DEFAULT_SCOPES: tuple[str, ...] = ("chat", "spend")


class PrincipalService:
    def __init__(self, directory: AccountDirectory, links: IdentityLinkStore, *, clock=time.time):
        self._directory = directory
        self._links = links
        self._clock = clock

    def resolve(self, assertion: Assertion, *, create_missing: bool = True) -> Principal:
        """Assertion -> Principal, creating and linking the account when this is a first login."""
        record = self._account_for(assertion, create_missing=create_missing)
        if not record.active:
            # Correct credential, deactivated account. Distinct from AuthenticationFailed on
            # purpose: "your account is disabled" is actionable and is not an enumeration leak,
            # because the caller already proved they hold the credential.
            raise AccountDisabled("this account is deactivated")

        # Keep the link's email attributes fresh — a provider legitimately changes them between
        # logins, and a stale copy here would make the "connected accounts" screen lie.
        self._links.link(
            provider=assertion.provider,
            subject=assertion.subject,
            account_id=record.account_id,
            email=assertion.email or record.email,
            email_verified=assertion.email_verified,
        )
        return Principal(
            account_id=record.account_id,
            email=record.email or assertion.email,
            email_verified=assertion.email_verified,
            scopes=DEFAULT_SCOPES,
            amr=assertion.amr or (assertion.provider,),
        )

    # -- resolution order (see the module note) --------------------------------------------

    def _account_for(self, assertion: Assertion, *, create_missing: bool) -> AccountRecord:
        existing = self._links.find(assertion.provider, assertion.subject)
        if existing is not None:
            record = self._directory.find_by_id(existing.account_id)
            if record is not None:
                return record
            # A link pointing at an account that no longer exists. Falling through to create a
            # fresh one is right: the alternative is a permanently un-loggable-in credential.

        # THE ACCOUNT THE ASSERTION ALREADY NAMES. The local password provider mints our own ids,
        # so its `subject` IS the account — and on a first signup there is no link to find yet,
        # because `resolve` writes the link only AFTER this method returns. Without this, signup
        # fell through to `create` a second time and died on the row `register` had just inserted
        # ("there is already an account with that email"), rolling the whole signup back: the API
        # answered 409 and no account existed afterwards. It also repairs the milder case of an
        # account whose link was lost, which used to mint a duplicate account instead.
        if assertion.subject_is_account_id and assertion.subject:
            record = self._directory.find_by_id(assertion.subject)
            if record is not None:
                return record

        email = (assertion.email or "").strip().lower()
        if email:
            by_email = self._directory.find_by_email(email)
            if by_email is not None:
                if assertion.email_verified:
                    return by_email
                # THE EMAIL IS TAKEN AND THE PROVIDER WOULD NOT VOUCH FOR IT. Linking here is the
                # takeover this function exists to prevent, so that is not on the table — but
                # falling through to `create` is not the safe alternative it looks like. It mints a
                # SECOND account on the same address, with its own credit balance, its own
                # workspace and its own orgs, and tells the person nothing: they sign in with
                # Google, find an empty account where their work was, and have no way to discover
                # that the first one still exists. Nobody can be expected to keep two accounts
                # because they used two doors.
                #
                # So: refuse, and say which door works. 403 rather than 401 — we know exactly who
                # this is, and the credential was fine; what is refused is the account.
                raise AccountDisabled(
                    f"{email} already has an account here, and {assertion.provider} did not "
                    "confirm you own that address. Sign in the way you did the first time, or "
                    "verify the address with your provider and try again."
                )

        if not create_missing:
            raise AccountDisabled("no account for this identity")
        return self._directory.create(email=email)
