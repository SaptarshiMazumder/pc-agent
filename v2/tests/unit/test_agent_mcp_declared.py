"""`[[mcp]]` — the MCP servers an agent brings with it.

The failure this whole feature exists to prevent: an author wires up a server with `mcp.add`,
watches their agent work, publishes it — and every installer gets an agent whose `aws__*` tools
do not exist. `mcp.add` writes the machine's config, and the machine's config is not packaged.

So the declaration lives in `agent.toml` and travels, and this file pins the three properties
that make it safe to connect on somebody else's machine:

  * two agents may declare the same server name and mean two different accounts
  * a server whose credential is not filled in must REFUSE, never fall through to the daemon's
  * launching a process asks first; reaching a URL does not
"""

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest
from agent_runtime.application.services.agent_mcp_connector import AgentMcpConnector
from agent_runtime.domain.agent import McpServerDecl, setting_env_name
from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry, _mcp_servers

DECLARING = """
name = "Trader"

[[settings]]
key = "AWS_ACCESS_KEY_ID"
kind = "secret"
required = true

[[mcp]]
name = "aws"
command = ["uvx", "awslabs.aws-api-mcp-server@latest"]
env = { AWS_ACCESS_KEY_ID = "${AWS_ACCESS_KEY_ID}" }
"""


class _Tool:
    def __init__(self, name):
        self.name = name


def _connector(env: dict, connect=None) -> AgentMcpConnector:
    """A connector whose environment and transport are both fakes — the policy is the subject."""
    calls: list = []

    async def _default_connect(agent_id, decl, resolved_env, resolved_headers):
        calls.append((agent_id, decl.name, resolved_env, resolved_headers))
        return [_Tool(f"{decl.name}__do_thing")]

    c = AgentMcpConnector(
        connect=connect or _default_connect,
        read_env=lambda name: env.get(name, ""),
        setting_env=setting_env_name,
    )
    c.calls = calls  # type: ignore[attr-defined]
    return c


def _agent(agent_id: str, *decls: McpServerDecl):
    return SimpleNamespace(id=agent_id, mcp=decls)


URL_SERVER = McpServerDecl(
    name="health", url="https://api.health.app/mcp", headers={"Authorization": "Bearer ${TOKEN}"}
)
STDIO_SERVER = McpServerDecl(
    name="aws",
    command=("uvx", "awslabs.aws-api-mcp-server@latest"),
    env={"AWS_ACCESS_KEY_ID": "${AWS_ACCESS_KEY_ID}"},
)


# ── parsing ─────────────────────────────────────────────────────────────────
def test_a_declaration_survives_a_round_trip_through_agent_toml(tmp_path):
    root = tmp_path / "agents" / "trader"
    root.mkdir(parents=True)
    (root / "agent.toml").write_text(DECLARING, encoding="utf-8")
    registry = FileAgentRegistry(SimpleNamespace(agents_dir=tmp_path / "agents", state_dir=tmp_path))
    (decl,) = registry.get("trader").mcp
    assert decl.name == "aws" and decl.transport == "stdio"
    assert decl.command == ("uvx", "awslabs.aws-api-mcp-server@latest")
    assert decl.placeholders == ("AWS_ACCESS_KEY_ID",)


def test_a_server_with_neither_command_nor_url_is_dropped(caplog):
    """Nothing to connect to. Dropped with a warning rather than accepted as a server that can
    never come up."""
    assert _mcp_servers([{"name": "aws"}], "demo") == ()
    assert "exactly one of" in caplog.text


def test_a_server_with_both_command_and_url_is_dropped(caplog):
    """Two different servers described as one — connecting either would be a guess."""
    assert _mcp_servers([{"name": "aws", "command": ["x"], "url": "https://y"}], "demo") == ()
    assert "exactly one of" in caplog.text


def test_placeholders_are_found_wherever_they_hide():
    decl = McpServerDecl(
        name="x",
        command=("run", "--profile", "${PROFILE}"),
        env={"A": "${ONE}"},
        headers={"B": "Bearer ${TWO}"},
    )
    assert set(decl.placeholders) == {"PROFILE", "ONE", "TWO"}


# ── the credential rule ─────────────────────────────────────────────────────
def test_a_missing_credential_refuses_the_connection():
    """THE POINT. The child would inherit the daemon's environment, so connecting without the
    user's key means acting on whatever account the daemon holds — success that is wrong."""
    c = _connector({})
    asyncio.run(c.ensure(_agent("trader", STDIO_SERVER)))
    assert c.tools_for("trader") == []
    assert "AWS_ACCESS_KEY_ID" in c.problems_for("trader")["aws"]
    assert c.calls == []  # never even attempted


def test_the_daemons_own_value_does_not_satisfy_an_agents_declaration():
    """Unprefixed AWS_ACCESS_KEY_ID is the DAEMON's. The agent's is trader__AWS_ACCESS_KEY_ID."""
    c = _connector({"AWS_ACCESS_KEY_ID": "the-daemons"})
    asyncio.run(c.ensure(_agent("trader", STDIO_SERVER)))
    assert c.tools_for("trader") == [] and c.calls == []


def test_the_agents_own_value_is_resolved_to_a_literal():
    """Handed down already-resolved: the layer that launches the child expands ${…} from
    os.environ and has no idea which agent is asking."""
    c = _connector({"trader__AWS_ACCESS_KEY_ID": "AKIA-traders"})
    asyncio.run(c.ensure(_agent("trader", STDIO_SERVER)))
    assert c.calls[0][2] == {"AWS_ACCESS_KEY_ID": "AKIA-traders"}
    assert [t.name for t in c.tools_for("trader")] == ["aws__do_thing"]


def test_two_agents_hold_two_accounts_for_the_same_server_name():
    """The multi-account case, which one flat config.mcp_servers list cannot express at all."""
    c = _connector(
        {
            "cost__AWS_ACCESS_KEY_ID": "AKIA-readonly",
            "provision__AWS_ACCESS_KEY_ID": "AKIA-poweruser",
        }
    )
    asyncio.run(c.ensure(_agent("cost", STDIO_SERVER)))
    asyncio.run(c.ensure(_agent("provision", STDIO_SERVER)))
    envs = {agent: env["AWS_ACCESS_KEY_ID"] for agent, _, env, _ in c.calls}
    assert envs == {"cost": "AKIA-readonly", "provision": "AKIA-poweruser"}
    # and neither agent can see the other's tools
    assert len(c.tools_for("cost")) == 1 and len(c.tools_for("provision")) == 1


# ── launching ───────────────────────────────────────────────────
# A stdio server used to wait for the user to approve its exact argv before it could launch, on
# the reasoning that installing an agent should not silently run third-party code. It was removed:
# the prompt reached the person who had just ASKED for the integration, gave them no way to see
# why their agent had no tools, and left an authoring agent with no way to tell "blocked on you"
# from "broken". A declared server now comes up on the agent's first run, whatever its transport.
def test_a_url_server_connects():
    c = _connector({"health-agent__TOKEN": "t"})
    asyncio.run(c.ensure(_agent("health-agent", URL_SERVER)))
    assert [t.name for t in c.tools_for("health-agent")] == ["health__do_thing"]


def test_a_stdio_server_connects_without_being_approved():
    c = _connector({"trader__AWS_ACCESS_KEY_ID": "AKIA"})
    asyncio.run(c.ensure(_agent("trader", STDIO_SERVER)))
    assert [t.name for t in c.tools_for("trader")] == ["aws__do_thing"]
    assert c.problems_for("trader") == {}



# ── failure handling ────────────────────────────────────────────────────────
def test_a_server_that_advertises_nothing_is_reported_not_silently_accepted():
    """An MCP server that connects and exposes no tools looks identical to one that worked."""

    async def _empty(agent_id, decl, env, headers):
        return []

    c = _connector({"health-agent__TOKEN": "t"}, connect=_empty)
    asyncio.run(c.ensure(_agent("health-agent", URL_SERVER)))
    assert "advertises no tools" in c.problems_for("health-agent")["health"]


def test_a_connection_error_never_breaks_the_turn():
    async def _boom(agent_id, decl, env, headers):
        raise RuntimeError("connection refused")

    c = _connector({"health-agent__TOKEN": "t"}, connect=_boom)
    asyncio.run(c.ensure(_agent("health-agent", URL_SERVER)))  # must not raise
    assert "connection refused" in c.problems_for("health-agent")["health"]


def test_a_failure_is_not_retried_every_single_turn():
    """Otherwise a server that is down adds its timeout to every message the user sends."""
    attempts: list = []

    async def _boom(agent_id, decl, env, headers):
        attempts.append(decl.name)
        raise RuntimeError("down")

    c = _connector({"health-agent__TOKEN": "t"}, connect=_boom)
    agent = _agent("health-agent", URL_SERVER)
    for _ in range(3):
        asyncio.run(c.ensure(agent))
    assert attempts == ["health"]


def test_changing_a_setting_makes_it_try_again():
    """A running child holds the environment it launched with, so a new key does nothing until
    the process is replaced."""
    c = _connector({})
    agent = _agent("trader", STDIO_SERVER)
    asyncio.run(c.ensure(agent))
    assert c.problems_for("trader")  # refused: no credential

    c._read_env = lambda name: {"trader__AWS_ACCESS_KEY_ID": "AKIA"}.get(name, "")
    assert c.agents_using([agent], {"AWS_ACCESS_KEY_ID"}) == ["trader"]
    c.forget("trader")
    asyncio.run(c.ensure(agent))
    assert [t.name for t in c.tools_for("trader")] == ["aws__do_thing"]


def test_an_agent_declaring_nothing_costs_nothing():
    c = _connector({})
    asyncio.run(c.ensure(SimpleNamespace(id="plain", mcp=())))
    assert c.calls == [] and c.tools_for("plain") == [] and c.problems_for("plain") == {}
