"""Refuse a request whose host resolves to anything but the public internet.

WHY. On a hosted daemon a plugin's request is dialled FROM THE SERVER, and some URLs come from a
person — "connect my own ComfyUI". Pointed at `169.254.169.254` (the cloud's metadata service,
which hands out the server's credentials) or `localhost:4100` (the accounts service), the server
would fetch its own internals and show the answer back in the chat. A desktop daemon dials from
the person's own machine, where `localhost` is exactly what they mean, so this is hosted-only
(the broker decides; see fetch_broker).

EVERY HOP. Checked on each request httpx sends, redirects included — a public URL that answers
"302 → http://169.254.169.254/" is the classic way round a first-host-only check.

HONEST LIMIT: the name is resolved here and again by the connection itself, so a DNS answer that
changes between the two (rebinding) is not caught. It closes the plain hole, not that one.
"""

from __future__ import annotations

import ipaddress
import socket


class PublicAddressRefused(ValueError):
    """The host is private, loopback, link-local or otherwise not on the public internet."""


class PublicAddressPolicy:
    def __init__(self, resolve=socket.getaddrinfo) -> None:
        self._resolve = resolve

    def check(self, host: str) -> None:
        """Raise PublicAddressRefused unless every address `host` resolves to is public."""
        name = (host or "").strip().strip("[]")
        if not name:
            raise PublicAddressRefused("the request has no host")
        try:
            addresses = {ipaddress.ip_address(name)}
        except ValueError:
            try:
                infos = self._resolve(name, None)
            except OSError as e:
                raise PublicAddressRefused(f"'{name}' does not resolve: {e}") from e
            addresses = {ipaddress.ip_address(info[4][0].split("%", 1)[0]) for info in infos}
        for address in addresses:
            if not address.is_global:
                raise PublicAddressRefused(
                    f"'{name}' is a private or internal address ({address}); this server only "
                    "connects to addresses on the public internet"
                )
