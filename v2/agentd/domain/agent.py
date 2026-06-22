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

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AgentSpec:
    id: str                                       # "main", "support", ...
    name: str                                     # persona name (identity in the prompt)
    workspace: Path                               # working dir for file/exec tools
    state_dir: Path                               # sessions live under <state_dir>/sessions/
    instructions: str = ""                        # bootstrap text (IDENTITY/AGENTS/... block)
    model: str | None = None                      # per-agent model override (carried; wired later)
    tools_allow: tuple[str, ...] | None = None    # None = all tools
    tools_deny: tuple[str, ...] = ()
    skills_allow: tuple[str, ...] | None = None   # None = all (global) skills
    skills_dir: Path | None = None                # the agent's OWN skills dir (<workspace>/skills/)
    # Capability gates — None = inherit the global config default; True/False = per-agent.
    # These drive the "What you are" self-knowledge section so a definition is self-describing.
    autonomy_enabled: bool | None = None          # may schedule (cron) + wake on a heartbeat
    notify_enabled: bool | None = None            # may reach the user (notifications)
    channels_enabled: bool | None = None          # may be reached on a messaging channel
    heartbeat: str | None = None                  # autonomy interval, e.g. "15m" (Phase 2)
    heartbeat_instructions: str = ""              # HEARTBEAT.md, injected only on a tick
    version: str = "1"                            # agent-definition version (S18, from agent.toml)


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
    CHANNEL = "channel"        # replying to a peer on a messaging channel (Phase 5b)


# tool name -> the ONLY run mode it is exposed in (absent from this map = every mode).
_MODE_ONLY = {
    "heartbeat_respond": RunMode.HEARTBEAT,
    "report_outcome": RunMode.CRON,        # scheduled runs declare done/blocked/failed
}


def apply_mode(tools: list, mode: str) -> list:
    """Drop mode-only tools that don't match the current run mode — e.g. hide
    ``heartbeat_respond`` outside a heartbeat tick. Tools absent from the map pass
    in every mode. Duck-typed on ``.name`` so it stays in the domain layer.
    """
    return [t for t in tools if _MODE_ONLY.get(getattr(t, "name", "")) in (None, mode)]
