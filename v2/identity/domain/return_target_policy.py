"""Where a finished external sign-in is allowed to send the browser back to.

WHY THIS EXISTS AT ALL. The provider is given ONE redirect URI for the whole deployment — the
accounts service's own ``/auth/return`` — because the alternative does not survive contact with
the product: every surface computes its redirect from the page it is served at, so the web app,
the daemon's ``/apps/<id>/`` windows, each vanity hostname and every desktop loopback port would
each need registering by hand in the provider's console. Agent Builder GENERATES agents. A
generated agent cannot add an entry to somebody's Google console, so that design does not merely
scale badly, it has no path to working at all.

One registered URI means ``/auth/return`` has to forward the browser onward to whichever page
started the flow — and THAT is an open redirect with an authorization code attached unless
something says which destinations are ours. This is that something.

CHECKED WHEN THE FLOW STARTS, not when it comes back. A refusal at return time happens after the
provider has already issued a code against a destination we were never going to honour; a refusal
at ``/auth/authorize`` costs nothing and the flow never exists. The return path checks again
anyway — the stored value is ours by then, but a policy that is only enforced in one place is one
refactor away from being enforced in none.

LOOPBACK IS ALWAYS ALLOWED, and that is not a hole. ``http://127.0.0.1:<port>`` reaches a listener
on the user's OWN machine (RFC 8252, and how every desktop app does this); an attacker who could
read it could read the browser too. It is also the one destination whose port cannot be known in
advance, so listing it is not an option.

Pure: strings in, a string out or a refusal. No IO, no config reading — the deployment's list is
handed in by the factory.
"""

from __future__ import annotations

from typing import Iterable
from urllib.parse import urlsplit

from identity.domain.errors import AuthenticationFailed

#: Hosts that are this machine, whatever port they answer on.
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "[::1]", "localhost"})

#: Schemes a browser will actually come back on. A ``javascript:`` or ``data:`` "origin" carrying
#: a code is the whole attack in one string, so the check is a whitelist rather than a blacklist.
_SCHEMES = frozenset({"http", "https"})


def _origin_of(url: str) -> tuple[str, str, str]:
    """``(scheme, host, port)`` for a URL, lowercased. Raises on anything a browser would not
    return to."""
    parts = urlsplit((url or "").strip())
    scheme = (parts.scheme or "").lower()
    if scheme not in _SCHEMES:
        raise AuthenticationFailed("a sign-in can only return to an http(s) address")
    # USERINFO IS REFUSED OUTRIGHT. `https://ours.example@evil.example/` has host `evil.example`
    # and reads, to a human skimming an allowlist, as ours.
    if parts.username or parts.password:
        raise AuthenticationFailed("a sign-in cannot return to an address carrying credentials")
    host = (parts.hostname or "").lower()
    if not host:
        raise AuthenticationFailed("a sign-in needs somewhere to return to")
    port = str(parts.port) if parts.port else ""
    return scheme, host, port


class ReturnTargetPolicy:
    """Decides whether a sign-in may return to a given address.

    ``allowed`` is a list of origins, each ``scheme://host[:port]``, with two wildcards:

      ``https://example.com:*``    that host on any port  (the daemon moves port between envs)
      ``https://*.example.com``    any subdomain          (per-agent vanity hostnames)

    An entry with no port matches only the scheme's default port, because "no port" and "any port"
    meaning the same thing is how an allowlist silently stops being one.
    """

    def __init__(self, allowed: Iterable[str] = ()):
        rules: list[tuple[str, str, str, bool]] = []
        for raw in allowed:
            entry = (raw or "").strip()
            if not entry:
                continue
            any_port = entry.endswith(":*")
            # ":0" only so urlsplit will parse the wildcard form; the flag is what is consulted.
            scheme, host, port = _origin_of(entry[:-2] + ":0" if any_port else entry)
            rules.append((scheme, host, port, any_port))
        # ONE list, not two zipped: the parse above can skip an entry, and two sequences filtered
        # by the same condition in two places is a desync waiting for the first blank line in the
        # environment variable.
        self._rules: tuple[tuple[str, str, str, bool], ...] = tuple(rules)

    def check(self, return_to: str) -> str:
        """The address, unchanged, if it is one of ours — otherwise refuse and say so."""
        scheme, host, port = _origin_of(return_to)
        if host in _LOOPBACK_HOSTS:
            return return_to.strip()
        for a_scheme, a_host, a_port, any_port in self._rules:
            if scheme != a_scheme:
                continue
            if not (host == a_host or (a_host.startswith("*.") and _under(host, a_host))):
                continue
            if any_port or port == a_port:
                return return_to.strip()
        # NAMES THE ORIGIN, not the whole URL: the path can carry a session token on this product
        # (an agent window's launch url does), and refusals get logged and pasted into issues.
        where = f"{scheme}://{host}{':' + port if port else ''}"
        raise AuthenticationFailed(
            f"{where} is not a sign-in destination for this deployment. "
            "Add it to AGENTD_OIDC_RETURN_ORIGINS if it should be."
        )


def _under(host: str, pattern: str) -> bool:
    """``a.example.com`` is under ``*.example.com``; ``example.com`` and ``evilexample.com`` are
    not. The leading dot is what stops the suffix match from being a substring match."""
    suffix = pattern[1:]  # ".example.com"
    return host.endswith(suffix) and len(host) > len(suffix)
