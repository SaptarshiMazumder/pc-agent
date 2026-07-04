"""FileAgentRegistry — discover agents from `agents/<id>/` directories.

"An agent is a directory": each `<agents_dir>/<id>/` holds an optional `agent.toml`
(model, tool allow/deny, skill allowlist, workspace, heartbeat) plus bootstrap
markdown (IDENTITY/AGENTS/USER/MEMORY) read by `load_bootstrap`.

`main` is ALWAYS present and is now a FIRST-CLASS agent like any other: rooted at
`agents/main/` (workspace at `agents/main/workspace/`, skills at `agents/main/skills/`)
and partitioned to `<state_dir>/agents/main/sessions/`. If no `agents/main/` dir exists
it is SYNTHESIZED with those same paths. main's `skills/` is the SHARED/global library
every agent inherits; each named agent's `skills/` is private to it. One bad agent dir
never breaks the rest.
"""

from __future__ import annotations

import logging
import tomllib
from pathlib import Path

from agentd.domain.agent import AgentSpec, agent_id_from_session_key
from agentd.infrastructure.agents.bootstrap import load_bootstrap, load_heartbeat

log = logging.getLogger("agentd")


def _valid_id(s: str) -> bool:
    return bool(s) and all(c.isalnum() or c in "-_" for c in s)


class FileAgentRegistry:
    """File-backed AgentRegistry. Discovers once at construction (cheap, cached)."""

    def __init__(self, config):
        self._config = config
        self._agents_dir = Path(getattr(config, "agents_dir", None)
                                or Path(config.state_dir).parent / "agents")
        self._specs = self._discover()

    def refresh(self) -> list[str]:
        """Re-scan agents/ and swap the cache — how a marketplace install (or any
        out-of-band drop of an agents/<id>/ dir) becomes visible WITHOUT a restart.
        Atomic swap: readers see the old dict or the new one, never a partial."""
        self._specs = self._discover()
        return sorted(self._specs)

    # ---- discovery ----------------------------------------------------------

    def _discover(self) -> dict[str, AgentSpec]:
        specs: dict[str, AgentSpec] = {}
        if self._agents_dir.is_dir():
            for d in sorted(self._agents_dir.iterdir()):
                if not d.is_dir():
                    continue
                agent_id = d.name.strip().lower()
                if not _valid_id(agent_id):
                    log.warning("agents: skipping invalid dir name %r", d.name)
                    continue
                try:
                    specs[agent_id] = self._load_dir(agent_id, d)
                except Exception as e:  # noqa: BLE001 — one bad agent must not break the rest
                    log.warning("agents: failed to load '%s': %s", agent_id, e)
        if "main" not in specs:
            specs["main"] = self._synthesize_main()
        log.info("agents: %d loaded (%s)", len(specs), ", ".join(sorted(specs)))
        return specs

    def _synthesize_main(self) -> AgentSpec:
        # main is a FIRST-CLASS agent rooted at agents/main/ (no agents/main/ dir on disk
        # yet -> use the same paths it WOULD load from, so behaviour is identical once the
        # dir exists). main's skills are the SHARED/global library every agent inherits.
        c = self._config
        d = self._agents_dir / "main"
        workspace = d / "workspace"
        try:
            workspace.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
        return AgentSpec(
            id="main",
            name=getattr(c, "agent_name", "") or "the assistant",
            workspace=workspace,
            state_dir=self._state_dir_for("main"),
            instructions="",
            model=None,
            tools_allow=None, tools_deny=(), skills_allow=None,
            skills_dir=d / "skills",              # main's skills = the global library
            dir=d,
        )

    def _state_dir_for(self, agent_id: str) -> Path:
        # every agent (main included) partitions to <state_dir>/agents/<id>/.
        return Path(self._config.state_dir) / "agents" / agent_id

    def _load_dir(self, agent_id: str, d: Path) -> AgentSpec:
        data: dict = {}
        toml_path = d / "agent.toml"
        if toml_path.is_file():
            with toml_path.open("rb") as f:
                data = tomllib.load(f)

        ws = data.get("workspace")
        if ws:
            workspace = Path(ws).expanduser()           # explicit path wins
        else:
            # EVERY agent (main included) gets its OWN isolated workspace at
            # agents/<id>/workspace/ (created on demand), so files never collide.
            workspace = d / "workspace"
            try:
                workspace.mkdir(parents=True, exist_ok=True)
            except OSError:
                pass

        tools = data.get("tools") or {}
        allow = tools.get("allow")
        deny = tools.get("deny") or []
        skills_allow = data.get("skills")
        model = data.get("model")
        # [plugins.*] per-agent model overrides, same plugin->tool->model shape as global
        # config.plugins. e.g.  [plugins.vision]  model = "..."  /  [plugins.vision.tools.verify_figure]
        # model = "...". Layered ABOVE global config.plugins by resolve_tool_model. Lowercase the
        # plugin keys so lookups (which lowercase the plugin) match; leave the nested body intact.
        plugins = {str(k).lower(): v for k, v in (data.get("plugins") or {}).items()
                   if isinstance(v, dict)}
        heartbeat = data.get("heartbeat")

        # [subagents] allow — which specialist agents this one may delegate to (ids/globs).
        subagents = data.get("subagents") or {}
        sub_allow = subagents.get("allow")

        # [capabilities] gates the "What you are" self-knowledge. Absent key => None =>
        # inherit the global default; an explicit true/false overrides it for this agent.
        caps = data.get("capabilities") or {}

        # [safe_to_send] audience = "external" => apply the privacy gate to this agent's channel
        # replies. Absent / "internal" / anything else => the gate is NOT applied to this agent.
        sts = data.get("safe_to_send") or {}
        audience = str(sts.get("audience") or "").strip().lower()

        return AgentSpec(
            id=agent_id,
            name=str(data.get("name") or agent_id),
            description=str(data.get("description") or ""),
            workspace=workspace,
            state_dir=self._state_dir_for(agent_id),
            instructions=load_bootstrap(d),
            model=str(model) if model else None,
            plugins=plugins,
            tools_allow=tuple(allow) if allow is not None else None,
            tools_deny=tuple(deny),
            subagents_allow=tuple(sub_allow) if sub_allow is not None else None,
            dir=d,
            skills_allow=tuple(skills_allow) if skills_allow is not None else None,
            skills_dir=d / "skills",          # the agent's OWN skills (agents/<id>/skills/)
            google_account=str(data.get("google_account") or ""),
            google_accounts=tuple(str(a) for a in (data.get("google_accounts") or [])),
            audience=audience,
            autonomy_enabled=caps.get("autonomy"),
            notify_enabled=caps.get("notify"),
            channels_enabled=caps.get("channels"),
            heartbeat=str(heartbeat) if heartbeat else None,
            heartbeat_instructions=load_heartbeat(d),
            version=str(data.get("version") or "1"),
        )

    @property
    def agents_dir(self) -> Path:
        """The root that holds ``<id>/`` definition dirs — so an authoring tool writes a new
        agent in the SAME place discovery reads from (single source of truth)."""
        return self._agents_dir

    def add(self, agent_id: str) -> AgentSpec:
        """(Re)load ONE ``agents/<id>/`` dir into the registry at runtime, so a newly-authored
        agent is resolvable WITHOUT a restart — the inverse of ``remove()``. ``resolve``/``get``
        read ``_specs`` live each turn, so the new agent is usable on the next message."""
        agent_id = (agent_id or "").strip().lower()
        if not _valid_id(agent_id):
            raise ValueError(f"invalid agent id: {agent_id!r}")
        d = self._agents_dir / agent_id
        if not d.is_dir():
            raise FileNotFoundError(str(d))
        spec = self._load_dir(agent_id, d)
        self._specs[agent_id] = spec
        log.info("agents: added '%s' at runtime (now: %s)", agent_id, ", ".join(sorted(self._specs)))
        return spec

    # ---- AgentRegistry ------------------------------------------------------

    def resolve(self, session_key: str) -> AgentSpec:
        return self._specs.get(agent_id_from_session_key(session_key)) or self._specs["main"]

    def get(self, agent_id: str) -> AgentSpec:
        return self._specs[agent_id]

    def list_ids(self) -> list[str]:
        return sorted(self._specs)

    def remove(self, agent_id: str) -> dict:
        """Delete an agent's DEFINITION dir (agent.toml/IDENTITY/… + its workspace) and
        its sessions dir, and forget it in-memory so no restart is needed. Refuses
        `main` (always-present default). The shared sqlite ledgers (memory/autonomy) are
        purged by their stores, not here. Returns what was removed.
        """
        import shutil

        agent_id = (agent_id or "").strip().lower()
        if agent_id == "main":
            raise ValueError("cannot delete the default agent 'main'")
        if agent_id not in self._specs:
            raise KeyError(agent_id)

        removed = {"id": agent_id, "definition": False, "sessions": False}
        def_dir = self._agents_dir / agent_id          # definition + workspace/ live here
        if def_dir.is_dir():
            shutil.rmtree(def_dir, ignore_errors=True)
            removed["definition"] = not def_dir.exists()
        state_dir = Path(self._state_dir_for(agent_id))  # <state_dir>/agents/<id>/ (sessions)
        if state_dir.is_dir():
            shutil.rmtree(state_dir, ignore_errors=True)
            removed["sessions"] = not state_dir.exists()
        del self._specs[agent_id]
        log.info("agents: removed '%s' (definition=%s sessions=%s)",
                 agent_id, removed["definition"], removed["sessions"])
        return removed
