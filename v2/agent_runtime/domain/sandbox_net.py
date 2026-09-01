"""The rules a sandboxed plugin's outbound request must satisfy. Pure — no sockets, no config.

A sandboxed plugin has no network. That was the right default and the wrong end state: "wrap an
API" is most of what people build, and a plugin that calls one works for its author and is dead
for everyone who installs the agent. So network is INVERTED, exactly as model calls were — the
plugin asks, the host performs.

That inversion is what makes the allowlist real. Blocking `socket.connect` from inside the child
is an interpreter-level control that a compiled extension steps around; refusing to DIAL is not a
control the caller can be talked out of, because the caller is the one holding the socket.

TWO DECISIONS LIVE HERE, and both are declarative:

  * WHERE it may go — ``[sandbox] net`` in plugin.toml, narrowed by operator config. Host
    matching only: a scheme and a path are not a security boundary, the host is.
  * WHAT CREDENTIAL rides along — ``[sandbox] secrets`` names, referenced in the request as
    ``${NAME}`` placeholders. The host substitutes; the VALUE never crosses the boundary, so a
    plugin cannot read the key, cannot keep it, and cannot post it somewhere it was not granted.

WHAT THIS DOES NOT CLAIM. A granted host is still a channel out — a plugin can encode data into a
path on a host it legitimately calls. This bounds who can be talked to and keeps credentials on
the host; it is not exfiltration prevention, and the declaration exists so a human can read what
an agent intends to contact BEFORE installing it.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

#: ``${NAME}`` — the only way a plugin refers to a credential. Deliberately not ``$NAME``: an
#: unbraced form collides with ordinary shell-ish text in a URL or header and would make
#: "did the author mean a placeholder?" a guess.
PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

#: Only these reach the network. A plugin asking for ``file://`` is asking the HOST to read a
#: path on its behalf — the same file-read oracle the model broker's image paths had to close.
ALLOWED_SCHEMES = frozenset({"http", "https"})


def host_of(url: str) -> str:
    """The lowercase hostname of ``url``, or "" when it has none."""
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def scheme_of(url: str) -> str:
    try:
        return (urlsplit(url).scheme or "").lower()
    except ValueError:
        return ""


def matches(host: str, pattern: str) -> bool:
    """Does ``host`` satisfy one allowlist entry?

    Exact, or a leading ``*.`` wildcard that covers SUBDOMAINS ONLY — ``*.acme.com`` matches
    ``api.acme.com`` and not ``acme.com.evil.net``, which a plain prefix/suffix test would wave
    through. A bare ``*`` is deliberately NOT special here: an operator writes that in the deny
    list, where matching everything is the intent.
    """
    host = (host or "").lower().strip()
    pattern = (pattern or "").lower().strip()
    if not host or not pattern:
        return False
    if pattern == host:
        return True
    if pattern.startswith("*."):
        suffix = pattern[1:]  # ".acme.com"
        return host.endswith(suffix) and len(host) > len(suffix)
    return False


def matches_any(host: str, patterns) -> bool:
    return any(matches(host, p) for p in (patterns or ()))


def deny_matches(host: str, patterns) -> bool:
    """Deny entries additionally honour a bare ``*`` — an operator switching outbound off."""
    for p in patterns or ():
        if str(p).strip() == "*" or matches(host, p):
            return True
    return False


def resolve_allowlist(declared, operator_allow, operator_deny) -> tuple[str, ...]:
    """What this plugin may actually reach: its declaration, narrowed by the operator.

    NARROWED, never widened. The plugin's declaration is a ceiling the operator can lower and
    can never raise — an operator allowlist that ADDED hosts would let a deployment grant reach
    that the installed package never disclosed, which is the one thing the declaration is for.

    A `${SETTING}` ENTRY SURVIVES UNRESOLVED. It means "the host the person running this put in
    that setting" — kept as-is because this layer is pure and the value is per-caller; the
    broker resolves it at call time. It is not a hole: the plugin names a SETTING, never a
    host, so it reaches only where its user pointed it, and a reader still sees before
    installing which of their own values it will dial. Without it, an agent wrapping a service
    the user hosts — their ComfyUI, their database, their internal API — becomes unbuildable
    the moment it is installed, because the host is unknowable when the plugin is written.
    """
    out = []
    strict = tuple(p for p in (operator_allow or ()) if str(p).strip())
    for entry in declared or ():
        raw = str(entry).strip()
        # CASE IS PRESERVED for a placeholder and folded for a host: setting names are
        # case-sensitive (`${COMFYUI_URL}` is not `${comfyui_url}`), hostnames are not.
        if PLACEHOLDER.fullmatch(raw):
            if raw not in out:
                out.append(raw)
            continue
        host = raw.lower()
        if not host or host == "*":
            continue  # a plugin may not declare "everything"; name the hosts you call
        if deny_matches(host, operator_deny):
            continue
        if strict and not matches_any(host, strict):
            continue
        if host not in out:
            out.append(host)
    return tuple(out)


def placeholders_in(value: str) -> set[str]:
    """Every ``${NAME}`` referenced in one string."""
    return set(PLACEHOLDER.findall(value or ""))


def undeclared_placeholders(values, declared) -> set[str]:
    """Names a request references but the plugin never declared.

    Refusing these is the point: without it a plugin could ask for ``${OPENAI_API_KEY}`` at call
    time and read back whatever the host happened to hold, which is precisely the blindness the
    empty ``secrets`` grant exists to guarantee.
    """
    allowed = {str(d).strip() for d in (declared or ()) if str(d).strip()}
    wanted: set[str] = set()
    for v in values:
        wanted |= placeholders_in(v)
    return wanted - allowed


def substitute(value: str, secrets: dict) -> str:
    """Replace every ``${NAME}`` with its value. An unknown name is left ALONE rather than
    blanked: a request that silently loses its credential fails as a confusing 401, while one
    that arrives with the literal text is obviously wrong and says so in the provider's error."""
    return PLACEHOLDER.sub(lambda m: secrets.get(m.group(1), m.group(0)), value or "")
