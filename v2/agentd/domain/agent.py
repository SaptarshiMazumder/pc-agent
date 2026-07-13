"""AgentSpec — the immutable definition of one agent.

An *agent* is a named, scoped configuration: a persona (name + bootstrap
instructions), a workspace, where its sessions live, and which tools/skills it may
use. The definition is SEPARATE from its execution (sessions/runs). The single-agent
app is just the `main` agent synthesized from config — so adding agents is purely
additive and removing an agent dir removes the agent.

Pure domain: no IO, no framework imports. The file-backed registry (infrastructure)
produces these; the application layer consumes them. Tool/skill selection lives here
too (it only reads a `.name`, so it stays IO-free).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class AgentSpec:
    id: str  # "main", "support", ...
    name: str  # persona name (identity in the prompt)
    workspace: Path  # working dir for file/exec tools
    state_dir: Path  # sessions live under <state_dir>/sessions/
    instructions: str = ""  # bootstrap text (IDENTITY/AGENTS/... block)
    description: str = ""  # one-line "what this agent is for" — shown to
    #                                               orchestrators by agents_list for delegation
    # Display presentation — what a launcher UI shows for this agent. Authored in
    # agent.toml (tagline/suggestions) or auto-generated ONCE from the identity and
    # stored in a sidecar (see infrastructure/agents/presentation.py).
    tagline: str = ""  # short picker line, e.g. "finance · gmail"
    suggestions: tuple[str, ...] = ()  # up to 3 starter prompts for an empty chat
    color: str = ""  # avatar/dot colour (hex). Authored in agent.toml
    #                                               or assigned once (unique across agents) by the daemon
    model: str | None = None  # per-agent model override (carried; wired later)
    # Per-agent TOOL-model overrides (from agent.toml [plugins.*]), same plugin->tool->model shape as
    # global config.plugins: {plugin: {"model": ..., "tools": {tool: {"model": ...}}}}. Layered ABOVE
    # global config.plugins by resolve_tool_model, so an agent can point a plugin (or one tool) at a
    # different model without touching global config. Empty = inherit global.
    plugins: dict = field(default_factory=dict)
    tools_allow: tuple[str, ...] | None = None  # None = all tools
    tools_deny: tuple[str, ...] = ()
    # Delegation scope: ids/globs of the specialist agents THIS agent may spawn/delegate to
    # (from [subagents] allow). None = no restriction (may delegate to any existing agent).
    subagents_allow: tuple[str, ...] | None = None
    skills_allow: tuple[str, ...] | None = None  # None = all (global) skills
    skills_dir: Path | None = None  # the agent's OWN skills dir (agents/<id>/skills/)
    dir: Path | None = None  # the agent's DEFINITION dir (agents/<id>/) — lets
    #                                                a heartbeat tick re-read HEARTBEAT.md fresh
    google_account: str = ""  # the ONE Google account this agent acts as (workspace MCP)
    google_accounts: tuple[
        str, ...
    ] = ()  # OR several it may use (multi-account: pass user_google_email)
    audience: str = ""  # "external" => apply the safe-to-send privacy gate to
    #                                               this agent's channel replies. Absent / "internal" /
    #                                               anything else => NOT gated. From agent.toml.
    # Capability gates — None = inherit the global config default; True/False = per-agent.
    # These drive the "What you are" self-knowledge section so a definition is self-describing.
    autonomy_enabled: bool | None = None  # may schedule (cron) + wake on a heartbeat
    notify_enabled: bool | None = None  # may reach the user (notifications)
    channels_enabled: bool | None = None  # may be reached on a messaging channel
    heartbeat: str | None = None  # autonomy interval, e.g. "15m" (Phase 2)
    heartbeat_instructions: str = ""  # HEARTBEAT.md, injected only on a tick
    version: str = "1"  # agent-definition version (S18, from agent.toml)
    # [app] — this agent ships its OWN client UI (an "app agent"): {entry, title}. The UI
    # lives at <dir>/ui/ and the daemon serves it at /apps/<id>/ (see docs/PROTOCOL.md §9).
    # None = a plain chat agent (rendered by the shared client). The definition stays pure
    # data — the gateway does the serving; nothing here executes.
    app: dict | None = None


def agent_id_from_session_key(session_key: str) -> str:
    """Resolve the agent id encoded in a session key.

    Keys are ``agent:<id>:<channel>:<peer>``; anything else (legacy plain keys like
    "default") maps to ``main``. Keeps single-agent clients working unchanged.
    """
    if session_key.startswith("agent:"):
        parts = session_key.split(":")
        if len(parts) >= 2 and parts[1]:
            return parts[1]
    return "main"


def cron_session_key(agent_id: str, task_id: str) -> str:
    """The session key for one scheduled job's runs — the inverse of
    ``agent_id_from_session_key``. PER-TASK (``agent:<id>:cron:<task_id>``), not a shared
    ``agent:<id>:cron``, so different jobs run on INDEPENDENT sessions: they execute
    concurrently and keep separate transcripts instead of serializing on (and bloating) one
    shared thread. The agent is still ``parts[1]``, so routing/parsing is unchanged.
    """
    return f"agent:{agent_id}:cron:{task_id}"


def _matches(name: str, pattern: str) -> bool:
    # exact, or a simple trailing-* prefix (e.g. "google__*" to allow a whole MCP server)
    if pattern.endswith("*"):
        return name.startswith(pattern[:-1])
    return name == pattern


def select_tools(tools: list, spec: AgentSpec) -> list:
    """Filter a toolset to what ``spec`` permits (deny wins; allow=None means all).

    Duck-typed on ``.name`` so it stays in the domain layer with no infra import.
    """
    deny = tuple(spec.tools_deny or ())
    allow = spec.tools_allow
    out = []
    for t in tools:
        name = getattr(t, "name", "")
        if any(_matches(name, d) for d in deny):
            continue
        if allow is not None and not any(_matches(name, a) for a in allow):
            continue
        out.append(t)
    return out


def select_private_tools(tools: list, spec: AgentSpec) -> list:
    """Filter an agent's OWN (private, shipped-with-the-agent) tools: implicitly allowed —
    they never need to appear in ``tools_allow`` (they arrived WITH the agent, naming them
    again would be redundant) — but an explicit ``tools_deny`` still wins, so an operator
    can switch one off. The sibling of ``select_tools`` (which governs the SHARED catalog,
    where allow=None means all and an allowlist is a strict filter)."""
    deny = tuple(spec.tools_deny or ())
    return [t for t in tools if not any(_matches(getattr(t, "name", ""), d) for d in deny)]


def apply_enablement(tools: list, enabled=None, disabled=()) -> list:
    """The GLOBAL (platform-wide) tool on/off filter -- the sibling of ``select_tools``.

    ``select_tools`` is PER-AGENT (an agent's allow/deny). This is applied ONCE to the whole
    CATALOG at build time, uniformly across every source (internal/plugin, native/mcp): a tool
    is dropped if it matches ``disabled``; if ``enabled`` is a non-empty list it acts as a
    strict allowlist (None/empty => everything is allowed). ``disabled`` wins. Matches by exact
    name or trailing-``*`` glob via the same ``_matches`` -- IO-free, duck-typed on ``.name``.
    """
    deny = tuple(disabled or ())
    allow = tuple(enabled or ()) or None
    out = []
    for t in tools:
        name = getattr(t, "name", "")
        if any(_matches(name, d) for d in deny):
            continue
        if allow is not None and not any(_matches(name, a) for a in allow):
            continue
        out.append(t)
    return out


def apply_plugin_enablement(tools: list, plugins: dict | None) -> list:
    """Drop tools switched OFF in the plugins config block — plugins[<plugin>].tools[<tool>].enabled
    is explicitly False. The sibling of the plugin-level gate (discovery._gate, applied at load): that
    toggles a whole plugin, this toggles ONE tool. A tool's home plugin is its provenance tag
    (``_plugin_id``, set by discovery for every plugin tool; falls back to ``.plugin``). Absent/omitted
    ``enabled`` => kept (default on). IO-free, duck-typed on ``.name``."""
    plugins = plugins or {}
    out = []
    for t in tools:
        pid = getattr(t, "_plugin_id", "") or getattr(t, "plugin", "")
        pconf = plugins.get(pid)
        tconf = (
            (pconf.get("tools") or {}).get(getattr(t, "name", ""))
            if isinstance(pconf, dict)
            else None
        )
        if isinstance(tconf, dict) and tconf.get("enabled") is False:
            continue
        out.append(t)
    return out


def select_skills(skills: list, spec: AgentSpec) -> list:
    """Filter skills to ``spec.skills_allow`` (None = all). Skills expose ``.name``."""
    allow = spec.skills_allow
    if allow is None:
        return list(skills)
    return [s for s in skills if any(_matches(getattr(s, "name", ""), a) for a in allow)]


def merge_skills(base: list, overlay: list) -> list:
    """Layer two skill lists by name: an ``overlay`` skill REPLACES a ``base`` one of the
    same name. Used so an agent's OWN skills override the shared global library (OpenClaw's
    workspace-wins precedence). Duck-typed on ``.name``; order is base-then-new-overlay."""
    by_name: dict = {getattr(s, "name", ""): s for s in base}
    for s in overlay:
        by_name[getattr(s, "name", "")] = s
    return list(by_name.values())


class RunMode:
    """Why a turn is running — the second tool/bootstrap-scoping axis (Phase 2).

    ``interactive`` is a user/client message (today). ``heartbeat`` is an autonomous
    scheduler tick. ``cron`` is a due scheduled job. Mode drives which mode-only tools
    + bootstrap (HEARTBEAT.md) get assembled for the turn.
    """

    INTERACTIVE = "interactive"
    HEARTBEAT = "heartbeat"
    CRON = "cron"
    CHANNEL = "channel"  # replying to a peer on a messaging channel (Phase 5b)


# tool name -> the ONLY run mode it is exposed in (absent from this map = every mode).
_MODE_ONLY = {
    "heartbeat_respond": RunMode.HEARTBEAT,
    "report_outcome": RunMode.CRON,  # scheduled runs declare done/blocked/failed
}


def apply_mode(tools: list, mode: str) -> list:
    """Drop mode-only tools that don't match the current run mode — e.g. hide
    ``heartbeat_respond`` outside a heartbeat tick. Tools absent from the map pass
    in every mode. Duck-typed on ``.name`` so it stays in the domain layer.
    """
    return [t for t in tools if _MODE_ONLY.get(getattr(t, "name", "")) in (None, mode)]
