"""Attenuated UI credentials. A shipped app never receives an account-wide JWT.

The signed bearer names an account, definition namespace and one agent. The parent
credential stays in the trusted gateway and is used only for authorized billing calls.
"""
from __future__ import annotations

import hashlib
import secrets
import time

import jwt


class AppCredentials:
    def __init__(self):
        self._key = secrets.token_urlsafe(48)
        self._parents: dict[str, tuple[dict, float]] = {}
        self._issued: dict[tuple[str, str, str], tuple[str, float, str]] = {}

    @staticmethod
    def host(account_id: str, namespace: str, agent_id: str, suffix: str) -> str:
        digest = hashlib.sha256(f"{account_id}:{namespace}:{agent_id}".encode()).hexdigest()[:16]
        return f"{agent_id[:40]}--{digest}.{suffix}"

    def mint(self, account: dict, namespace: str, agent_id: str) -> str:
        now = time.time()
        cache_key = (account["account_id"], namespace, agent_id)
        previous = self._issued.get(cache_key)
        signature = str(account.get("session_token") or "")
        if previous and previous[1] > now + 60 and previous[2] == signature:
            return previous[0]
        for key, (_, expiry) in list(self._parents.items()):
            if expiry <= now:
                self._parents.pop(key, None)
        for key, (_, expiry, _) in list(self._issued.items()):
            if expiry <= now:
                self._issued.pop(key, None)
        if len(self._parents) >= 10000:
            raise RuntimeError("app session capacity reached")
        expiry = min(now + 900, float(account.get("expires_at") or now + 900))
        if expiry <= now:
            raise PermissionError("parent session expired")
        key = secrets.token_urlsafe(32)
        self._parents[key] = (account, expiry)
        token = jwt.encode({
            "iss": "agentd-app", "aud": "agentd-app", "sub": account["account_id"],
            "jti": key, "agent": agent_id, "namespace": namespace, "exp": expiry,
        }, self._key, algorithm="HS256")
        self._issued[cache_key] = (token, expiry, signature)
        return token

    def resolve(self, token: str) -> dict | None:
        try:
            claims = jwt.decode(token, self._key, algorithms=["HS256"],
                                audience="agentd-app", issuer="agentd-app")
            parent, expiry = self._parents[claims["jti"]]
            if expiry <= time.time() or parent.get("account_id") != claims["sub"]:
                return None
            return {**parent, "app_scope": claims["agent"], "app_namespace": claims["namespace"],
                    "app_expires_at": expiry,
                    "expires_at": min(expiry, float(parent.get("expires_at") or expiry))}
        except (jwt.InvalidTokenError, KeyError, TypeError, ValueError):
            return None


def is_app_token(token: str) -> bool:
    try:
        return jwt.decode(token, options={"verify_signature": False}).get("iss") == "agentd-app"
    except (jwt.InvalidTokenError, AttributeError):
        return False
