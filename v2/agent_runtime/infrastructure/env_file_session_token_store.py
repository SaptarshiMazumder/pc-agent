"""EnvFileSessionTokenStore — the signed-in identity, kept in the user's ``.env``.

Same channel as every other secret this daemon holds, for the same reasons: it is already 0600
on the user's own machine, it is already loaded into the environment at boot, and it is already
the place a person looks when they want to know what their install is holding.

TWO KEYS, NOT ONE.

    AGENTD_SESSION_TOKEN   who you are
    AGENTD_MODEL_PROXY_KEY who pays        (owned by the desktop Cloud switch, untouched here)

They were the same value. That is why signing in was impossible without also buying something,
and why ``publish_agent`` could only tell whether you were allowed to publish by checking whether
you had a billing credential.

The email rides along in ``AGENTD_SESSION_EMAIL`` so a restarted daemon can still say WHO is
signed in without a round trip to the accounts service. It is not a credential; the token is.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.domain.account_session import ANONYMOUS, AccountSession
from agent_runtime.infrastructure.env_file import EnvFile

TOKEN_KEY = "AGENTD_SESSION_TOKEN"
EMAIL_KEY = "AGENTD_SESSION_EMAIL"
ACCOUNT_KEY = "AGENTD_SESSION_ACCOUNT_ID"


class EnvFileSessionTokenStore:
    """:param env_path: the ``.env`` beside agentd.config.json."""

    def __init__(self, env_path: Path):
        self._env = EnvFile(env_path)

    def read(self) -> AccountSession:
        token = self._env.read(TOKEN_KEY)
        if not token:
            return ANONYMOUS
        return AccountSession(
            token=token,
            email=self._env.read(EMAIL_KEY),
            account_id=self._env.read(ACCOUNT_KEY),
        )

    def write(self, session: AccountSession) -> None:
        wrote = self._env.update(
            {
                TOKEN_KEY: session.token,
                EMAIL_KEY: session.email,
                ACCOUNT_KEY: session.account_id,
            }
        )
        if not wrote:
            # Loud, not best-effort. A store that quietly fails signs the user in for as long as
            # this process lives and then logs them out at the next restart with no explanation.
            raise OSError(f"could not persist the session to {self._env.path}")

    def clear(self) -> None:
        self._env.update({TOKEN_KEY: "", EMAIL_KEY: "", ACCOUNT_KEY: ""})
