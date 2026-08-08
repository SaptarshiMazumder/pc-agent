"""Which agents a HOSTED daemon may offer at all — decided from data, never from a name in code.

A desktop install is one person on their own machine: an agent there can write anywhere, run a
shell, and hot-load Python, because all of that is the owner doing it to their own computer. A
hosted daemon is the same binary serving STRANGERS on ONE shared container, where the same three
abilities are a way into everybody else's files.

Agent Builder is the concrete case — `create_tool` writes Python and loads it into the running
process — but hardcoding its id here would be exactly the wrong shape. Any agent can have that
problem, an operator may reach a different conclusion about a given agent, and the next one will
not be called `agent-builder`. So the decision is DECLARED and CONFIGURABLE, in three layers:

    agent.toml   requires_local = true      the AUTHOR: "this agent needs a machine of its own"
    config       hosted_agents_deny = [..]  the OPERATOR withholding an agent that did not say so
    config       hosted_agents_allow = [..] the OPERATOR overriding an agent that did

Deny beats allow beats the declaration, and all of it is inert on a desktop install: `is_hosted`
is false there, so `withheld_reason` returns None for everything and the path is unchanged.

WITHHELD MEANS ABSENT, NOT BLOCKED. The registry drops the agent at scan time, so it is not in the
roster, not resolvable by session key, has no app to serve, and its private tools are never
discovered. That is the same mechanism that keeps `create_agent` away from `main` — the tool is
not in its catalog rather than being refused when called — and it leaves no surface to get wrong.

Pure: config + spec in, a reason string or None out. No IO, no imports from infrastructure.
"""

from __future__ import annotations


def _matches(name: str, pattern: str) -> bool:
    """Exact, or a trailing-``*`` prefix — the same glob rule as ``[subagents] allow``."""
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def _ids(config, attr: str) -> tuple[str, ...]:
    return tuple(str(x).strip() for x in (getattr(config, attr, None) or ()) if str(x).strip())


def is_hosted(config) -> bool:
    """Is this daemon serving people other than its operator?

    Read from ``config.hosted``, which ``load_config`` derives once from the things that actually
    imply it (``multi_tenant``, an accounts URL) so every caller gets the same answer. Asking each
    caller to re-derive it is how two parts of a system come to disagree about which mode they
    are in.
    """
    return bool(getattr(config, "hosted", False))


def withheld_reason(agent_id: str, requires_local: bool, config) -> str | None:
    """Why this agent must not be offered here, or None if it may be.

    The reason is returned rather than a bare bool because it gets LOGGED: an agent that silently
    fails to appear is indistinguishable from a broken install, and the operator who set the
    config is not usually the person staring at the empty sidebar.
    """
    if not is_hosted(config):
        return None
    if agent_id == "main":
        return None  # the default agent is the daemon itself; withholding it leaves nothing
    for pattern in _ids(config, "hosted_agents_deny"):
        if _matches(agent_id, pattern):
            return f"config hosted_agents_deny matches '{pattern}'"
    for pattern in _ids(config, "hosted_agents_allow"):
        if _matches(agent_id, pattern):
            return None  # the operator vouches for it despite what it declares
    if requires_local:
        return "its agent.toml declares requires_local = true"
    return None
