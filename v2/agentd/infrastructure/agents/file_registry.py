"""FileAgentRegistry — discover agents from `agents/<id>/` directories.

"An agent is a directory": each `<agents_dir>/<id>/` holds an optional `agent.toml`
(model, tool allow/deny, skill allowlist, workspace, heartbeat) plus bootstrap
markdown (IDENTITY/AGENTS/USER/MEMORY) read by `load_bootstrap`.

Backward compatibility is the rule: `main` is ALWAYS present. If no `agents/main/`
dir exists it is SYNTHESIZED from config, on the LEGACY session path
(`<state_dir>/sessions/`), so existing transcripts and single-agent behavior are
byte-for-byte unchanged. Every OTHER agent partitions to
`<state_dir>/agents/<id>/sessions/`. One bad agent dir never breaks the rest.
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
        c = self._config
        return AgentSpec(
            id="main",
            name=getattr(c, "agent_name", "") or "the assistant",
            workspace=Path(c.workspace),
            state_dir=Path(c.state_dir),          # legacy flat path (back-compat)
            instructions="",
            model=None,
            tools_allow=None, tools_deny=(), skills_allow=None,
        )

    def _state_dir_for(self, agent_id: str) -> Path:
        # main keeps the legacy session path; every other agent partitions.
        base = Path(self._config.state_dir)
        return base if agent_id == "main" else base / "agents" / agent_id

    def _load_dir(self, agent_id: str, d: Path) -> AgentSpec:
        data: dict = {}
        toml_path = d / "agent.toml"
        if toml_path.is_file():
            with toml_path.open("rb") as f:
                data = tomllib.load(f)

        c = self._config
        ws = data.get("workspace")
        if ws:
            workspace = Path(ws).expanduser()
        elif (d / "workspace").is_dir():
            workspace = d / "workspace"
        else:
            workspace = Path(c.workspace)

        tools = data.get("tools") or {}
        allow = tools.get("allow")
        deny = tools.get("deny") or []
        skills_allow = data.get("skills")
        model = data.get("model")
        heartbeat = data.get("heartbeat")

        return AgentSpec(
            id=agent_id,
            name=str(data.get("name") or agent_id),
            workspace=workspace,
            state_dir=self._state_dir_for(agent_id),
            instructions=load_bootstrap(d),
            model=str(model) if model else None,
            tools_allow=tuple(allow) if allow is not None else None,
            tools_deny=tuple(deny),
            skills_allow=tuple(skills_allow) if skills_allow is not None else None,
            heartbeat=str(heartbeat) if heartbeat else None,
            heartbeat_instructions=load_heartbeat(d),
        )

    # ---- AgentRegistry ------------------------------------------------------

    def resolve(self, session_key: str) -> AgentSpec:
        return self._specs.get(agent_id_from_session_key(session_key)) or self._specs["main"]

    def get(self, agent_id: str) -> AgentSpec:
        return self._specs[agent_id]

    def list_ids(self) -> list[str]:
        return sorted(self._specs)
