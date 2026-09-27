"""Turn what a person pasted into a connection record — by asking the address what it is.

TWO THINGS A PERSON PASTES:

  * their Vast machine's Instance Portal link — the "Open" button, `http://IP:PORT/?token=…`.
    The portal lists its services with their public addresses (GET /capabilities/services, the
    same Bearer token), ComfyUI among them. That gives everything a rented GPU has: ComfyUI's
    address, the portal that runs our downloader, and the one token that opens both.
  * any ComfyUI address — `http://host:8188`, with `?token=…` or `user:pass@` if it has one.
    Answering /api/system_stats is what makes it a ComfyUI.

Nothing is saved until the address has answered as one of the two.
"""

from __future__ import annotations

import base64
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

from account_connection_repository import USER_URL, USER_VAST


class ComfyConnectionProbe:
    def __init__(self, fetch) -> None:
        self._fetch = fetch

    def probe(self, pasted: str) -> dict:
        parts = urlsplit((pasted or "").strip())
        if parts.scheme not in ("http", "https") or not parts.hostname:
            raise ValueError("that is not a web address — paste a link that starts with http:// or https://")
        host = parts.hostname + (f":{parts.port}" if parts.port else "")
        origin = urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))
        query = parse_qs(parts.query)
        token = (query.get("token") or [""])[0]
        label = urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))

        if token:
            vast = self._vast(origin, token, label)
            if vast is not None:
                return {**vast, "link": pasted.strip()}
        auth = ""
        if parts.username:
            pair = f"{parts.username}:{parts.password or ''}".encode()
            auth = "Basic " + base64.b64encode(pair).decode()
        folded = origin + (f"#q={urlencode({'token': token})}" if token else "")
        self._comfy(origin, token, auth)
        return {"kind": USER_URL, "url": folded, "auth": auth, "portal_url": "", "label": label,
                "link": pasted.strip()}

    def _vast(self, portal: str, token: str, label: str) -> dict | None:
        """A Vast Instance Portal answers its services list; anything else is not one."""
        bearer = f"Bearer {token}"
        res = self._fetch(f"{portal}/capabilities/services", headers={"Authorization": bearer}, timeout_s=20.0)
        if res.error:
            raise ValueError(f"could not reach {portal}: {res.error}")
        if res.status in (401, 403):
            raise ValueError(f"{portal} refused the token (HTTP {res.status})")
        if not res.ok:
            return None  # answers, but is not a Vast portal — a plain ComfyUI link
        try:
            services = res.json()
        except ValueError:
            return None
        if not isinstance(services, list):
            return None
        comfy = next(
            (s for s in services
             if isinstance(s, dict) and re.sub(r"[^a-z]", "", str(s.get("name") or "").lower()) == "comfyui"),
            None,
        )
        if comfy is None or not comfy.get("direct_url"):
            raise ValueError(
                "that is a Vast machine, but it lists no ComfyUI with a public address — rent it "
                "with Vast's ComfyUI template, or paste the ComfyUI link itself"
            )
        url = str(comfy["direct_url"]).rstrip("/")
        self._comfy(url, "", bearer)
        return {"kind": USER_VAST, "url": url, "auth": bearer, "portal_url": portal, "label": label}

    def _comfy(self, origin: str, token: str, auth: str) -> None:
        """Raise unless `origin` answers as a ComfyUI."""
        res = self._fetch(
            f"{origin}/api/system_stats",
            params={"token": token} if token else None,
            headers={"Authorization": auth} if auth else None,
            timeout_s=20.0,
        )
        if res.error:
            raise ValueError(f"could not reach {origin}: {res.error}")
        if res.status in (401, 403):
            raise ValueError(
                f"{origin} refused the login (HTTP {res.status}) — paste the link with its token, "
                "e.g. the one Vast's Open button gives"
            )
        if not res.ok:
            raise ValueError(f"{origin} answered HTTP {res.status}, not as a ComfyUI")
        try:
            stats = res.json()
        except ValueError:
            stats = None
        if not isinstance(stats, dict) or "system" not in stats:
            raise ValueError(f"{origin} answered, but it is not a ComfyUI (no system_stats)")
