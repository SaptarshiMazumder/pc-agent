"""FileTokenStore — where a connected account's tokens live on this machine.

    <state_dir>/oauth/<agent-id>/<connection>.json

PER AGENT, and that is the cheap answer rather than the clever one: the declaration already lives
in one agent's ``agent.toml``, so ``(agent, name)`` is the key that falls out of the design.
Sharing one connection between agents would need an extra rule about who owns it and what happens
when the owner is uninstalled — a rule nobody has asked for. Two agents connecting the same
provider get two logins, which is also the honest answer when they are two different accounts.

NOT ENCRYPTED, deliberately and on the record. The file is 0600 in the daemon's own state dir,
beside a ``.env`` that already holds provider keys in plain text — encrypting one and not the
other would be theatre. An OS keychain (DPAPI / Keychain / libsecret) is the real answer and is a
later step; until then this is the same protection everything else here has, which is a statement
someone can check rather than an assurance they cannot.
"""

from __future__ import annotations

import logging
import os
import stat
from pathlib import Path

from agent_runtime.domain.oauth_connection import OAuthTokens

log = logging.getLogger("agentd")


class FileTokenStore:
    """:param root: the directory to keep connections under (``<state_dir>/oauth``)."""

    def __init__(self, root: Path):
        self._root = Path(root)

    def _path(self, agent_id: str, name: str) -> Path:
        import hashlib

        # JSON scope keys include all ownership dimensions; no caller-controlled path parts.
        import re

        folder = agent_id if re.fullmatch(r"[A-Za-z0-9_-]+", agent_id) else hashlib.sha256(agent_id.encode()).hexdigest()
        filename = name if re.fullmatch(r"[A-Za-z0-9_-]+", name) else hashlib.sha256(name.encode()).hexdigest()
        return self._root / folder / f"{filename}.json"

    def load(self, agent_id: str, name: str) -> OAuthTokens | None:
        path = self._path(agent_id, name)
        if not path.is_file():
            return None
        try:
            from agent_runtime.infrastructure import secret_storage

            data = secret_storage.read(path)
        except (OSError, ValueError):
            # A corrupt record is NOT silently treated as "not connected": that would send the
            # user round the sign-in loop with no idea why it never sticks.
            log.warning("oauth: %s is unreadable — treating '%s' as disconnected", path, name)
            return None
        return OAuthTokens(
            access_token=str(data.get("access_token") or ""),
            refresh_token=str(data.get("refresh_token") or ""),
            expires_at=float(data.get("expires_at") or 0.0),
            scopes=tuple(str(s) for s in (data.get("scopes") or ())),
            account=str(data.get("account") or ""),
        )

    def save(self, agent_id: str, name: str, tokens: OAuthTokens) -> bool:
        path = self._path(agent_id, name)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            from agent_runtime.infrastructure import secret_storage
            secret_storage.write(path, {
                "access_token": tokens.access_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
                "scopes": list(tokens.scopes),
                "account": tokens.account,
            })
            # Owner-only. A no-op on Windows, where the state dir is already per-user — done
            # anyway because the same daemon runs on machines where it is not.
            os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)
        except OSError as e:
            log.warning("oauth: could not write %s: %s", path, e)
            return False
        return True

    def delete(self, agent_id: str, name: str) -> bool:
        try:
            self._path(agent_id, name).unlink(missing_ok=True)
        except OSError as e:
            log.warning("oauth: could not remove %s/%s: %s", agent_id, name, e)
            return False
        return True

    def connected(self, agent_id: str) -> list[str]:
        """Which connections this agent has tokens for."""
        folder = self._path(agent_id, "_").parent
        if not folder.is_dir():
            return []
        return sorted(p.stem for p in folder.glob("*.json"))
