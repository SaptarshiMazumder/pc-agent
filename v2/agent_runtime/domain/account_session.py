"""AccountSession — WHO is signed in to this install.

A pure value object: an identity plus the token that proves it. It knows nothing about where
the token is kept, which service issued it, or whether anybody is paying for model calls.

That last clause is the whole reason this type exists. Identity used to be carried by
``AGENTD_MODEL_PROXY_KEY`` — a PAYMENT credential — so "who are you" could only be answered by
"you are the person whose bill we are settling". Everything downstream inherited the confusion:
a sign-in was reported as failed unless the paid proxy switched on, and ``publish_agent`` decided
whether you were allowed to publish by looking for a billing key.

Not to be confused with ``AuthProfile`` next door: that rotates PROVIDER credentials (an OpenAI
key, a Google login). This is the person using the product.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AccountSession:
    token: str
    email: str = ""
    account_id: str = ""

    @property
    def signed_in(self) -> bool:
        """A session is only real if it carries a token. An email with no token is the shape you
        get from a half-written store, and it must not read as signed in."""
        return bool(self.token)


#: Nobody is signed in. A named constant rather than ``None`` so callers never branch on it.
ANONYMOUS = AccountSession(token="")
