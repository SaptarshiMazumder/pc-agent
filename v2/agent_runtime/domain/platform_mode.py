"""Local vs Cloud — WHO PAYS for model calls, as a pure rule.

    local — the user's own provider keys. No proxy, no metering.
    cloud — platform keys, through the model proxy, metered to the signed-in account.

This is one machine-wide fact, not a per-agent one: the model proxy is process state and every
agent on the daemon shares it. That is worth stating where the rule lives, because a Cloud toggle
sitting inside one agent's settings flips every other agent too.

DEFAULT IS CLOUD, AND AN EXPLICIT LOCAL MUST STICK. Those two requirements fight each other, and
the fight is the reason this is a function rather than a boolean. Signing in should land you in
Cloud with nothing to press — but if a user has chosen Local, the next sign-in and the next
restart must not quietly undo it, or "default cloud" would really mean "you can never stay local".
So an unset preference means cloud; a set one is obeyed.

The two capability inputs are not preferences and are never remembered. A daemon with no proxy
URL, or with nobody signed in, has no cloud to be in — it reports local because that is what is
actually happening, not because anyone chose it.
"""

from __future__ import annotations

LOCAL = "local"
CLOUD = "cloud"

#: No stored preference. Distinct from LOCAL: it means "never chosen", which is what makes the
#: default applicable. Once someone presses Local, this is no longer the value.
UNSET = ""

VALID_PREFERENCES = (UNSET, LOCAL, CLOUD)


def normalize(value) -> str:
    """Anything off the wire or out of a file -> a preference we recognise, else UNSET.

    A junk value becomes "no choice yet" rather than an error: the preference is a convenience,
    and a daemon that refuses to start because a settings file has `mode = clod` in it is a worse
    outcome than one that falls back to the default.
    """
    text = str(value or "").strip().lower()
    return text if text in VALID_PREFERENCES else UNSET


def resolve(preference: str, proxy_available: bool, signed_in: bool) -> str:
    """The mode this daemon is actually in.

    :param preference: the user's stored choice (UNSET => they have not chosen).
    :param proxy_available: is a model-proxy URL configured for this build?
    :param signed_in: is there a session token to pay with?
    """
    if normalize(preference) == LOCAL:
        return LOCAL
    # Cloud needs both halves. Without them there is nothing to switch TO, and reporting cloud
    # would promise metered platform keys while the calls quietly go out on the user's own.
    if not proxy_available or not signed_in:
        return LOCAL
    return CLOUD
