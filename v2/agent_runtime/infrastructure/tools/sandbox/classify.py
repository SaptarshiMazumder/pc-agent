"""The CLASSIFIER — one decision point: which tools are untrusted, and wrap those in a sandbox.

``classify_origin`` decides a tool's ``PluginOrigin`` from PROVENANCE — *where the code came
from*, not where it happens to sit on disk. The threat is code that rode in inside SOMEONE ELSE'S
agent package; owning tools is not itself suspicious, so an agent that ships with the product or
that the user authored on this machine keeps its tools trusted.

The untrusted test is therefore: is this agent in the MARKETPLACE LEDGER — i.e. did
``marketplace.install`` lay it down from a ``.agentpkg``? That fact is recorded by the installer,
never by the package, so an agent cannot vouch for itself: nothing in its ``agent.toml`` is
consulted. (Bundle id IS agent id — ``unpack_bundle`` writes to ``agents_dir/<manifest.id>``.)

Order of decision:
  1. no ``_agent_id`` tag (an ordinary shared plugin)     -> FIRST_PARTY  (not a sandboxed tier)
  2. ``config.sandbox_untrusted_agents`` names the agent  -> THIRD_PARTY_BUNDLE  (forced; below)
     (or contains ``*``, meaning every agent)
  3. ``config.sandbox_trusted_plugins`` names the plugin  -> FIRST_PARTY  (operator override)
  4. ``config.sandbox_trusted_agents`` names the agent    -> FIRST_PARTY  (operator override)
  5. the agent is in the installed ledger                 -> THIRD_PARTY_BUNDLE
  6. otherwise (shipped with the product, or authored here) -> FIRST_PARTY

``sandbox_untrusted_agents`` OUTRANKS every exemption, and that is the one asymmetry worth
stating: every other knob here RELAXES, so allowing one of them to beat it would produce a switch
whose whole purpose — "show me what a buyer gets" — silently does nothing. It can only ever
tighten, so nothing about it is a security decision; without it the only way to exercise the
sandbox on your own agent is to pack and install it before every run, which is enough friction
that the sandbox stops being tested.

FAIL CLOSED: the ledger is supplied by the composition root. When it cannot be read the caller
passes ``None``, and every agent-private tool is treated as untrusted — a ledger that fails to
load must never silently promote downloaded code.

``wrap_untrusted`` is the single place the wrapping happens: untrusted tools become ``SandboxedTool``
bound to the given backend + resolver; trusted tools pass through unchanged. Off => identity, so the
seam is dormant until ``config.sandbox_untrusted_plugins`` is enabled.
"""

from __future__ import annotations

import logging

from agent_runtime.application.interfaces.plugin_sandbox import CapabilityResolver, PluginSandbox
from agent_runtime.application.interfaces.tool import Tool
from agent_runtime.domain.sandbox import PluginOrigin

from .sandboxed_tool import SandboxedTool

log = logging.getLogger("agentd")


#: In ``sandbox_untrusted_agents``, this means "every agent". Accepted ONLY there — on the two
#: trusting knobs a wildcard would switch the sandbox off wholesale from a config line, which is
#: the one thing none of these should be able to do.
ALL = "*"


def _config_ids(config, attr: str) -> set[str]:
    return {str(x).strip() for x in (getattr(config, attr, None) or ()) if str(x).strip()}


def _forced_untrusted(config, agent_id: str) -> bool:
    """Is this agent forced to the untrusted path? ``*`` covers every agent.

    The list exists to answer "what does a buyer get?", and typing an id per agent is exactly
    the friction that stopped the sandbox being exercised while it was built. ``*`` makes the
    answer permanent. It can only ever TIGHTEN, so there is nothing to weigh."""
    ids = _config_ids(config, "sandbox_untrusted_agents")
    return ALL in ids or agent_id in ids


def plugin_presumed_untrusted(
    config, agent_id: str, plugin_id: str, installed_agent_ids: frozenset[str] | None
) -> bool:
    """The SAME trust decision as ``classify_origin``, answerable BEFORE any tool object exists —
    from the manifest's id and the agent's, which is all a pre-load caller has.

    It exists because trust must be decided before IMPORT, not just before execution: importing a
    module runs its module-level code, so enumerating an untrusted plugin's tools by importing it
    executes a stranger's code in the daemon one step earlier than the tool call the sandbox
    guards. Discovery asks this first and, for an untrusted plugin, enumerates through the
    sandbox instead of loading. One function, delegated to by ``classify_origin``, so the
    pre-load answer and the wrap-time answer can never disagree.
    """
    if not agent_id:
        return False  # an ordinary shared plugin — never a sandboxed tier
    if config is not None and _forced_untrusted(config, agent_id):
        # FORCED, ahead of every exemption. Tightening only, so there is nothing to weigh.
        return True
    if config is not None and plugin_id and plugin_id in _config_ids(config, "sandbox_trusted_plugins"):
        return False
    if config is not None and agent_id in _config_ids(config, "sandbox_trusted_agents"):
        return False  # the operator vouches for this agent
    if installed_agent_ids is None:
        return True  # ledger unknown -> fail closed
    if agent_id in installed_agent_ids:
        return True  # it came from someone else's package
    # Shipped with the product, or authored on this machine — same trust as a drop-in plugin.
    return False


def classify_origin(
    tool: Tool, config=None, installed_agent_ids: frozenset[str] | None = None
) -> PluginOrigin:
    """Trust provenance for one tool (see the module docstring for the full order).

    ``installed_agent_ids`` is the marketplace ledger's roster — the agents that arrived in a
    ``.agentpkg``. ``None`` means "unknown", and every agent-private tool is then untrusted.
    """
    plugin_id = getattr(tool, "_plugin_id", "") or ""
    agent_id = getattr(tool, "_agent_id", "") or ""
    untrusted = plugin_presumed_untrusted(config, agent_id, plugin_id, installed_agent_ids)
    return PluginOrigin.THIRD_PARTY_BUNDLE if untrusted else PluginOrigin.FIRST_PARTY


def wrap_untrusted(
    tools: list[Tool],
    *,
    sandbox: PluginSandbox,
    resolver: CapabilityResolver,
    config=None,
    installed_agent_ids: frozenset[str] | None = None,
) -> list[Tool]:
    """Wrap every untrusted tool in a SandboxedTool; leave trusted tools as-is.

    Returns the list unchanged when sandboxing is disabled — the caller can always route tools
    through here; the flag decides whether anything is actually wrapped.

    ``installed_agent_ids`` is resolved by the composition root (it owns the marketplace store)
    and passed straight through to ``classify_origin``; ``None`` fails closed.
    """
    if not getattr(config, "sandbox_untrusted_plugins", False):
        return tools
    out: list[Tool] = []
    for t in tools:
        origin = classify_origin(t, config, installed_agent_ids)
        if origin.is_untrusted:
            plugin_id = getattr(t, "_plugin_id", "") or "unknown"
            out.append(
                SandboxedTool(t, sandbox, resolver, plugin_id=plugin_id, origin=origin)
            )
            log.info(
                "sandbox: wrapping untrusted tool '%s' (plugin '%s') via %s backend",
                getattr(t, "name", "?"),
                plugin_id,
                getattr(sandbox, "name", "?"),
            )
        else:
            out.append(t)
    return out
