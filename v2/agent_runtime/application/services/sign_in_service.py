"""SignInService — the use case: "log this person in to the product".

Orchestrates two ports and performs no I/O of its own: ask the accounts service, remember the
answer. That is the entire flow, and it is deliberately this small — the complexity that used to
live here was not sign-in, it was sign-in tangled up with billing.

WHAT THIS DOES NOT DO, and why each one matters:

  * It does not decide whether the model proxy runs. Signing in on your own API keys is a normal
    state. The old flow treated a login as unsuccessful until a paid proxy switched on, so a
    perfectly good sign-in raised "this device did not activate" on every BYOK install.
  * It does not hand the token back to the caller. The gateway reports WHETHER someone is signed
    in, never the credential — so an agent's web page cannot leak, log, or store it.
  * It does not know a URL. Where the accounts service lives is the adapter's business, which is
    what stops every agent UI from having to be told.
"""

from __future__ import annotations

from agent_runtime.application.interfaces.platform_accounts import PlatformAccounts
from agent_runtime.application.interfaces.session_token_store import SessionTokenStore
from agent_runtime.domain.account_session import AccountSession


class SignInService:
    def __init__(self, accounts: PlatformAccounts, tokens: SessionTokenStore):
        self._accounts = accounts
        self._tokens = tokens

    @property
    def available(self) -> bool:
        """Can anyone sign in to this daemon at all?"""
        return self._accounts.available

    def session(self) -> AccountSession:
        return self._tokens.read()

    async def login(self, email: str, password: str, signup: bool = False) -> AccountSession:
        """Sign in (creating the account first when ``signup``), remember it, and return who it is.

        Both blank-field checks raise rather than returning a signed-out session, for the same
        reason the adapter raises on a bad password: the caller renders an error message, and it
        needs something to render.
        """
        email = (email or "").strip().lower()
        if not email:
            raise ValueError("an email address is required")
        if not password:
            raise ValueError("a password is required")
        session = await self._accounts.login(email, password, signup)
        self._tokens.write(session)
        return session

    def logout(self) -> None:
        """Forget the identity. The model-proxy credential is NOT touched — signing out of your
        account and stopping the platform from billing you are two different acts, and the desktop
        Cloud switch owns the second one."""
        self._tokens.clear()
