"""The authoring toolset as an MCP SERVER — so somebody else's coding agent can build our agents.

A SECOND PRESENTATION ADAPTER over the same services. `agent_authoring/presentation/*_tool.py`
exposes them to OUR model as agentd Tools; this exposes them to ANY MCP client — Claude Code,
Codex, Cursor, Zed — over stdio. `domain/` and `application/` are untouched and unaware.

WHY THIS IS WORTH HAVING. The intelligence and the rules are separable. Agent Builder supplies
both, and the ceiling is whichever is weaker — which so far has been the model. A developer
already has a far stronger coding agent open; what it lacks is our format rules and our
validator. Hand it those two and it builds better agents than we do, while still building OUR
agents.

WHAT ENFORCES THE RULES IS THE VALIDATOR, NOT THE MODEL. That is the whole design. A capable
model is not a well-behaved one; it is one that needs fewer rounds to pass a gate. So the gate
travels: `validate_agent` is the same rule set, on the same findings, whoever is driving.

NO DAEMON REQUIRED. Authoring is a filesystem job — read the dir, write the files, check them.
The daemon matters only to RUN an agent, and that is already a separate thing the user has.
So this server never opens a socket to agentd; it points at an agents directory and works there.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace

from mcp.server.fastmcp import FastMCP


def _agents_dir() -> Path:
    """Which agents directory this server authors into.

    AGENTD_AGENTS_DIR wins; otherwise the repo checkout's `v2/agents/` relative to this file.
    Explicit rather than clever: an authoring tool that guesses wrong writes an agent into a
    tree nobody is running.
    """
    override = (os.environ.get("AGENTD_AGENTS_DIR") or "").strip()
    if override:
        return Path(override).expanduser()
    return Path(__file__).resolve().parents[5]


def _services():
    """Build the same object graph `agent_authoring_plugin` builds, minus the agentd runtime.

    The registry is constructed against a bare config stand-in: `FileAgentRegistry` needs only
    `agents_dir` and `state_dir`, and nothing in the authoring path touches state.
    """
    from agent_runtime.domain.events import APP_FACING_EVENTS, MESSAGE_UPDATE_KINDS
    from agent_runtime.infrastructure.agents import FileAgentRegistry
    from agent_runtime.presentation.gateway import APP_SCOPED_METHODS, PROVIDER_ENV_KEYS

    from agent_authoring.application.validate_agent_service import ValidateAgentService
    from agent_authoring.domain.agent_layout_rules import AgentLayoutRules
    from agent_authoring.domain.declaration_rules import DeclarationRules
    from agent_authoring.domain.freshness_rules import FreshnessRules
    from agent_authoring.domain.packageability_rules import PackageabilityRules
    from agent_authoring.domain.sandbox_rules import SandboxRules
    from agent_authoring.domain.ui_component import UiComponents
    from agent_authoring.domain.ui_rules import UiRules
    from agent_authoring.infrastructure.agent_dir_reader import AgentDirReader

    agents_dir = _agents_dir()
    registry = FileAgentRegistry(
        SimpleNamespace(agents_dir=agents_dir, state_dir=agents_dir.parent / ".agentd")
    )
    reader = AgentDirReader(registry)
    components = UiComponents()
    validator = ValidateAgentService(
        reader,
        AgentLayoutRules(),
        PackageabilityRules(),
        SandboxRules(),
        UiRules(
            events=APP_FACING_EVENTS,
            kinds=MESSAGE_UPDATE_KINDS,
            methods=frozenset(APP_SCOPED_METHODS),
            sdk_methods=frozenset(),
            components=components.all(),
        ),
        declaration_rules=DeclarationRules(provider_keys=PROVIDER_ENV_KEYS),
        freshness_rules=FreshnessRules(),
    )
    return agents_dir, registry, validator


AGENTS_DIR, REGISTRY, VALIDATOR = _services()
SKILLS = Path(__file__).resolve().parents[4] / "skills"

mcp = FastMCP("agentd-authoring")


@mcp.tool()
def agent_rules(topic: str = "build-agent") -> str:
    """THE FORMAT RULES for authoring an agentd agent. READ THIS FIRST, before writing any file.

    An agent is a directory of files in a specific shape; this is the authoritative reference for
    that shape — agent.toml grammar, where each file goes, how to declare settings/MCP/OAuth, and
    how to design the agent so it is a mechanism rather than a chat box.

    topic: "build-agent" (the reference) | "connect-mcp" (connecting third-party services).
    """
    path = SKILLS / topic / "SKILL.md"
    if not path.is_file():
        available = ", ".join(sorted(p.name for p in SKILLS.iterdir() if p.is_dir()))
        return f"no such topic '{topic}'. available: {available}"
    return path.read_text(encoding="utf-8")


@mcp.tool()
def list_agents() -> str:
    """Every agent in this workspace, and the directory they live in."""
    ids = sorted(REGISTRY.list_ids())
    return f"agents_dir: {AGENTS_DIR}\n" + "\n".join(f"- {i}" for i in ids)


@mcp.tool()
def validate_agent(agent_id: str) -> str:
    """CHECK AN AGENT. Run this after every change, and never call an agent finished until it is
    clean.

    Reports what the daemon will not: keys silently ignored because of TOML table order, a
    credential pasted into the file that ships, a [[mcp]] server referencing a setting nobody
    declared, a UI listening for events that do not exist, tools that will not survive the
    sandbox. Fix every [x] and run it again.
    """
    REGISTRY.refresh()  # pick up files written since the last call
    return VALIDATOR.validate(agent_id).as_text()


# `scaffold_ui` and `list_ui_templates` ARE DELIBERATELY NOT EXPOSED HERE.
#
# They copied a complete vanilla app — plain JS into `ui/`, no build step. A window is a React
# project now: source in `app/`, compiled into `ui/` by `build_app`, with the toolchain shipped
# inside the product so a user has nothing to install. One way to give an agent a window means
# there is no wrong one to pick, and a tool surface that still OFFERS the old way is a decision
# driver pointing at it however the documentation reads.
#
# The service and the templates are still on disk: a dozen agents in this product have
# hand-written `ui/` folders, served straight off disk. Nothing maintains
# them. What ended is offering vanilla as a choice for something new.


if __name__ == "__main__":
    print(f"agentd-authoring: agents_dir={AGENTS_DIR}", file=sys.stderr)
    mcp.run()
