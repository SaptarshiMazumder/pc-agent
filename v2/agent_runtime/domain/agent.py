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

import re
from dataclasses import dataclass, field
from pathlib import Path

#: Inside an agent's folder, THESE SUBDIRECTORIES ARE THE USER'S, not the agent's definition.
#:
#: One folder holds an agent's definition, its `workspace/` and its `sessions/` together, so
#: every operation that replaces or removes a definition — uninstall, update, delete — has to
#: name what it must not touch. Stated ONCE, here, because the three call sites that got it
#: wrong each had their own hand-written idea of it (each preserved `workspace` alone, which
#: silently began deleting chat history the moment sessions moved into the same folder).
USER_DATA_DIRS = frozenset({"workspace", "sessions"})


def definition_entries(agent_dir) -> tuple[str, ...]:
    """The SHAREABLE part of one agent's folder: its top-level entries minus ``USER_DATA_DIRS``.

    One folder holds an agent's definition next to the ``workspace/`` and ``sessions/`` of
    whoever runs it, so every place a definition legitimately crosses an ownership boundary —
    the sandbox's shipped-data read grant, a hosted run's read scope, packing — needs "the
    folder, without the user's part". Enumerated per call rather than granted-with-carve-outs
    because a grant is a list of roots: subtraction is not expressible in one, and each caller
    inventing its own exclusion list is how ``USER_DATA_DIRS`` came to exist in the first place.

    A directory created after this call is simply not in the answer (fail closed for the run
    that asked; the next run sees it). Unreadable/missing dir => ``()``, same principle.
    """
    try:
        return tuple(
            str(item)
            for item in sorted(Path(agent_dir).iterdir())
            if item.name not in USER_DATA_DIRS
        )
    except OSError:
        return ()


def agent_dir_key(path) -> str:
    """ONE canonical spelling of an agent directory, for identity maps keyed by LOCATION.

    The agent-private tool map used to key by bare agent id, which collides the moment two
    accounts each hold an agent of the same id — the later layer's tools silently answered for
    both (a cross-tenant hole on a hosted daemon). Location is the identity that cannot collide;
    realpath + normcase so a symlinked or differently-cased spelling of the same folder still
    hits the same entry. Pure stdlib, importable from both application and composition code.
    """
    import os

    return os.path.normcase(os.path.realpath(str(path or "")))
#: What a settings field may be. `secret` is write-only in every UI — you see that it is set,
#: never what it is. `url` and `text` are ordinary values a user can read back and correct.
SETTING_KINDS = ("secret", "text", "url")

#: `${NAME}` — the one placeholder syntax, shared by plugin secrets, MCP env and MCP headers.
#: Matched here so the domain can answer "what does this declaration need?" without the caller
#: re-deriving the pattern and getting a subtly different one.
PLACEHOLDER_NAMES = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class SettingField:
    """One thing an agent needs from whoever runs it: an API key, a database URL, an endpoint.

    THE DECLARATION SHIPS; THE VALUE NEVER DOES. This lives in ``agent.toml``, so it travels in
    the ``.agentpkg`` to every installer. The value lives in that machine's ``.env``, which
    packaging already excludes — so a downloader opens Settings, sees the fields empty with the
    author's own labels and help text, and fills them in on their own machine.

    It doubles as a PERMISSION. Writing to ``.env`` is app-callable (``config.set``), and it used
    to accept any name at all: an installed agent's settings page — code somebody else wrote —
    could overwrite ``OPENAI_API_KEY``, or repoint the model proxy at a server it controlled and
    read every prompt. There was no way to scope that, because there was no way for an agent to
    say what it legitimately needed. Now there is, and it is the same list.
    """

    key: str  # the env var name, e.g. "COINBASE_API_KEY"
    label: str = ""  # what the settings page shows; defaults to the key
    kind: str = "text"  # one of SETTING_KINDS
    required: bool = False  # the agent cannot work without it
    help: str = ""  # one line: where the user gets this value

    @property
    def secret(self) -> bool:
        return self.kind == "secret"


@dataclass(frozen=True)
class McpServerDecl:
    """An MCP server THIS agent needs, declared in ``agent.toml`` so it travels with the package.

    The gap this closes: ``mcp.add`` writes the machine's ``agentd.config.json``, which is not
    packaged. An author would wire up a server, watch their agent work, publish it — and the
    installer would get ``[tools] allow = ["aws__*"]`` matching nothing. An agent that looks
    installed and silently has no tools is the worst shape a failure can take, because the only
    symptom is the model saying it cannot do the thing.

    A declaration, never a connection: pure data, no session, no subprocess. ``env``/``headers``
    hold ``${NAME}`` PLACEHOLDERS naming ``[[settings]]`` keys — an author who inlines a real
    credential here ships it to everyone who installs the agent.

    Per agent, deliberately. Two agents may both call a server ``aws`` and mean two different AWS
    accounts; their tools live in separate per-agent sets, so the names cannot collide the way one
    flat ``config.mcp_servers`` list would force them to.
    """

    name: str  # the tool namespace: <name>__<tool>
    command: tuple[str, ...] = ()  # stdio launch argv — mutually exclusive with url
    url: str = ""  # http(s) endpoint
    env: dict | None = None  # for the stdio child; values may be ${SETTING}
    headers: dict | None = None  # for the url; values may be ${SETTING}
    auth: str = ""  # "oauth:<name>" (Part 2b) — empty means static credentials

    @property
    def transport(self) -> str:
        return "http" if self.url else "stdio"

    @property
    def placeholders(self) -> tuple[str, ...]:
        """Every ``${NAME}`` this declaration references, in command, env and headers alike.

        The connector needs the whole set BEFORE connecting: a server whose credential is still
        empty must be refused rather than launched, because the subprocess would inherit the
        daemon's environment and quietly run on whatever account the daemon happens to hold.
        """
        found: list[str] = []
        for value in (*self.command, *(self.env or {}).values(), *(self.headers or {}).values()):
            found.extend(PLACEHOLDER_NAMES.findall(str(value)))
        return tuple(dict.fromkeys(found))


#: The tool that lets the MODEL wire up an MCP server from chat. Named here because both the
#: prompt (which describes the capability) and the toolset (which grants it) have to mean the
#: same thing by "MCP workshop".
MCP_WORKSHOP_TOOL = "add_mcp"


def capability_enabled(agent, attr: str, global_default) -> bool:
    """Resolve one capability gate: the agent's own answer if it gave one, else the daemon's.

    ONE rule, used by the prompt (what the agent is told it can do) and by the toolset (what it
    can actually do). Those drifting apart produces an agent that has been told it can schedule
    work and has no `cron`, which reads to a user as the model lying.

    ``None`` means "did not say" and inherits — which is why these are ``bool | None`` and not
    ``bool``. An explicit ``false`` in agent.toml therefore beats a daemon-wide ``true``: the
    author of an agent knows what it needs, and the operator's default is only a default.
    """
    own = getattr(agent, attr, None) if agent is not None else None
    return bool(global_default) if own is None else bool(own)


def setting_env_name(agent_id: str, key: str) -> str:
    """Where an agent's declared setting is STORED: ``<agent-id>__<KEY>``.

    THE PREFIX IS A STORAGE DETAIL, applied on write and stripped on read. The author still
    declares ``key = "AWS_ACCESS_KEY_ID"``, the settings page still shows "AWS access key", and
    the MCP server or plugin that consumes it still sees the bare name. Nobody types the prefix.

    It exists because ``.env`` is ONE file shared by every agent on the machine. Without it, a
    cost-monitoring agent and a provisioning agent — both wanting ``AWS_ACCESS_KEY_ID``, each for
    a DIFFERENT AWS account — overwrite each other, and neither the user nor the code can tell
    which account is answering. Two prefixed names cannot collide, so the second agent is simply
    a second agent rather than a corruption of the first.

    The agent id goes in VERBATIM, not upper-cased or otherwise normalised: ``aws-provisioner``
    and ``aws_provisioner`` are both valid ids, and folding them together would recreate the exact
    collision this prevents. Safe because nothing here needs a shell identifier — ``.env`` is read
    by splitting on the first ``=`` (``config._load_dotenv`` / ``EnvFile.update``), and an id is
    already restricted to letters, digits, ``-`` and ``_``.

    PROVIDER KEYS ARE NOT PREFIXED, and that is not an exception — ``ANTHROPIC_API_KEY`` is one
    machine-wide credential by design, shared by every agent. Only what an agent DECLARES is
    private to it.
    """
    return f"{agent_id}__{key}" if agent_id and key else key


def resolve_setting_env(name: str, agent_id: str, declared) -> str:
    """The env var to actually read for ``${name}`` on behalf of ``agent_id``.

    Declared by this agent -> its private prefixed name. Not declared -> the bare name, which is
    the machine-wide variable it has always been (``FAL_KEY``, ``GOOGLE_OAUTH_CLIENT_ID``, a
    provider key, anything the operator exported).

    A LOOKUP, NOT A FALLBACK CHAIN. It never tries the prefixed name and then quietly settles for
    the bare one: that would let an agent whose own value is unset run on the daemon's credentials
    and report success — the silent-wrong-account failure this whole scheme exists to stop. The
    declaration decides which name is read, and if that name is empty the caller sees it empty.
    """
    return setting_env_name(agent_id, name) if name in (declared or ()) else name


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
    # FILESYSTEM WRITE SCOPE, from agent.toml `[tools.fs] write_roots` / `deny`. Verbatim as
    # authored — tokens like <agents_dir> are still unexpanded here, because this is the parsed
    # DEFINITION and the expansion needs config the domain must not import.
    #
    # EMPTY = UNRESTRICTED, so every agent that says nothing keeps today's behaviour and only an
    # agent that opts in is constrained. Reading is never restricted by these; a tool that only
    # reads has no way to damage anything, and an agent must be able to read its own skill.
    write_roots: tuple[str, ...] = ()
    write_denies: tuple[str, ...] = ()  # carved out of the roots — deny always wins
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
    # [[settings]] — what this agent needs from whoever runs it (see SettingField). Empty for an
    # agent that needs nothing, which is most of them. Also the allowlist of env names its own
    # settings page may write.
    settings: tuple[SettingField, ...] = ()
    # [[mcp]] — MCP servers THIS agent brings with it (see McpServerDecl). Kept out of
    # config.mcp_servers on purpose: that list is one flat machine-wide namespace, so two agents
    # could not both declare an "aws". These connect lazily, per agent, on first run.
    mcp: tuple[McpServerDecl, ...] = ()
    # [[oauth]] — third-party sign-ins this agent needs (see domain/oauth_connection.py). The
    # declaration ships with the package; the tokens are per user, per machine, and never do.
    oauth: tuple = ()
    # [capabilities] mcp_workshop — may the MODEL wire up an MCP server mid-conversation
    # (the add_mcp tool)? None = inherit the global default; True/False = per-agent. An agent
    # that DECLARES its servers does not need this; it is for the one being built.
    mcp_workshop_enabled: bool | None = None
    # [app] — this agent ships its OWN client UI (an "app agent"): {entry, title}. The UI
    # lives at <dir>/ui/ and the daemon serves it at /apps/<id>/ (see docs/PROTOCOL.md §9).
    # None = a plain chat agent (rendered by the shared client). The definition stays pure
    # data — the gateway does the serving; nothing here executes.
    app: dict | None = None
    # requires_local — the AUTHOR declaring "this agent needs a machine of its own". A hosted
    # daemon does not offer it AT ALL: not listed, not resolvable, no app served, its private
    # tools never discovered (see domain/agent_availability.py). Meaningless on a desktop
    # install, which is every install that is not serving strangers.
    requires_local: bool = False
    # OWNERSHIP, resolved once at scan time (domain/ownership.py): the `.agentd-meta.json`
    # record when the dir has one, else the presumed owner of the layer it was found in. On the
    # spec so that visibility and `mine` are dict lookups per call, not disk reads. Empty owner
    # = an unscanned/test-constructed spec, which every consumer treats as unrestricted.
    owner: str = ""
    origin: str = "authored"


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
