"""STANDALONE APPS — the agents that are surfaces of the product, not agents you picked.

Agent Builder is a FEATURE of agentd: it belongs in the navigation, not on the shelf between the
weather agent and one the user wrote. The fact is declared by the agent itself —

    [app]
    standalone = true

— and every client reads it. These tests pin the two halves that make that work: the declaration
survives the parse, and the flagged agent STAYS IN THE ROSTER. The second half is the subtle one.
The obvious implementation is to drop it from `listed()`, and that silently breaks the feature:
a client cannot render a button for an agent it was never told about, so the app would vanish
from the lists AND from the navigation — unreachable in both places.
"""

from pathlib import Path
from types import SimpleNamespace

import tomllib

from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry
from agent_runtime.presentation.gateway import Gateway

ROOT = Path(__file__).resolve().parents[2]

# Every agent that is a product surface rather than one of the user's. Discovered, not typed out:
# the point of the declaration is that adding one is an agent.toml edit, so the test that guards
# it must find them the same way a client does.
BUILDERS = ("agent-builder", "cloud-agent-builder")


def _registry(tmp_path, *, standalone: bool | None, with_ui: bool = True, surface: str = ""):
    """One agent with an [app], declaring standalone or not (None = key absent entirely)."""
    agents = tmp_path / "agents"
    d = agents / "surface"
    (d / "ui").mkdir(parents=True)
    if with_ui:
        (d / "ui" / "index.html").write_text("<!doctype html>", encoding="utf-8")
    body = 'name = "Surface"\n\n[app]\ntitle = "Surface"\nentry = "ui/index.html"\n'
    if standalone is not None:
        body += f"standalone = {'true' if standalone else 'false'}\n"
    if surface:
        body += f'surface = "{surface}"\n'
    (d / "agent.toml").write_text(body, encoding="utf-8")
    (d / "IDENTITY.md").write_text("A surface.", encoding="utf-8")
    cfg = SimpleNamespace(
        agent_name="JARVIS",
        workspace=tmp_path / "ws",
        state_dir=tmp_path / "state",
        agents_dir=agents,
        hosted=False,
        hosted_agents_deny=(),
        hosted_agents_allow=(),
    )
    return FileAgentRegistry(cfg)


# --- the declaration survives the parse --------------------------------------


def test_declared_standalone_reaches_the_spec(tmp_path):
    reg = _registry(tmp_path, standalone=True)
    assert reg.get("surface").app["standalone"] is True


def test_absent_means_an_ordinary_agent(tmp_path):
    """The default has to be "a normal agent" — every agent that predates this key is one."""
    reg = _registry(tmp_path, standalone=None)
    assert reg.get("surface").app["standalone"] is False


def test_explicit_false_is_an_ordinary_agent(tmp_path):
    reg = _registry(tmp_path, standalone=False)
    assert reg.get("surface").app["standalone"] is False


# --- it stays reachable -------------------------------------------------------


def test_a_standalone_app_is_still_listed(tmp_path):
    """NOT hidden server-side. The client needs the row to draw the button; hiding it here would
    make the app unreachable rather than merely unlisted."""
    reg = _registry(tmp_path, standalone=True)
    assert "surface" in reg.list_ids()
    assert reg.listed("surface") is True


def test_the_flag_rides_the_discovery_surface(tmp_path):
    """`_agent_app` is what every client actually reads. If the flag stops here, each client is
    back to guessing by id — the thing this exists to prevent."""
    reg = _registry(tmp_path, standalone=True)
    app = Gateway._agent_app("surface", reg.get("surface"))
    assert app is not None and app["standalone"] is True


def test_an_ordinary_agents_app_says_so(tmp_path):
    reg = _registry(tmp_path, standalone=None)
    app = Gateway._agent_app("surface", reg.get("surface"))
    assert app is not None and app["standalone"] is False


def test_a_broken_ui_advertises_nothing(tmp_path):
    """No entry file => no app at all, standalone or not. The clients treat "is a product
    surface" as "has an app AND declares standalone", so this is the case that would otherwise
    hide an agent from the lists while offering no way in."""
    reg = _registry(tmp_path, standalone=True, with_ui=False)
    assert Gateway._agent_app("surface", reg.get("surface")) is None


# --- one surface, two implementations -----------------------------------------


def test_surface_defaults_to_the_agent_id(tmp_path):
    """An app that names no surface is the only implementation of its own — one button."""
    reg = _registry(tmp_path, standalone=True)
    assert reg.get("surface").app["surface"] == "surface"


def test_a_declared_surface_wins(tmp_path):
    reg = _registry(tmp_path, standalone=True, surface="agent-builder")
    assert reg.get("surface").app["surface"] == "agent-builder"


def test_the_surface_and_host_fit_reach_the_client(tmp_path):
    """Both facts a client needs to draw ONE entry and open the right implementation."""
    reg = _registry(tmp_path, standalone=True, surface="agent-builder")
    app = Gateway._agent_app("surface", reg.get("surface"))
    assert app["surface"] == "agent-builder"
    assert app["requiresLocal"] is False


def test_the_builders_share_one_surface():
    """The whole point: ONE "Agent Builder" entry, whichever host you are on. If these two ever
    disagree the navigation grows a second button nobody asked for."""
    surfaces = {
        agent_id: tomllib.loads((ROOT / "agents" / agent_id / "agent.toml").read_text("utf-8"))
        .get("app", {})
        .get("surface")
        for agent_id in BUILDERS
    }
    assert len(set(surfaces.values())) == 1, f"builders disagree on their surface: {surfaces}"
    assert None not in surfaces.values()


def test_the_two_implementations_are_told_apart_by_requires_local():
    """The client picks between them on this fact alone, so exactly one must claim the desktop.
    Two locals (or two hosted) and the pick becomes arbitrary — first-in-roster wins."""
    local = {
        agent_id: bool(
            tomllib.loads(
                (ROOT / "agents" / agent_id / "agent.toml").read_text("utf-8")
            ).get("requires_local")
        )
        for agent_id in BUILDERS
    }
    assert sum(local.values()) == 1, f"exactly one builder may be the local one: {local}"


# --- the agents this exists for actually declare it ---------------------------


def test_the_builders_declare_standalone():
    """The rule is only worth having if the agents it exists for use it."""
    for agent_id in BUILDERS:
        data = tomllib.loads((ROOT / "agents" / agent_id / "agent.toml").read_text("utf-8"))
        assert data.get("app", {}).get("standalone") is True, (
            f"{agent_id} builds agents — it is a feature of the product, not one of your agents"
        )


def test_the_declaration_sits_inside_the_app_table():
    """TOML scopes a key into whichever [table] precedes it. Written above [app] this lands at
    top level, where nothing reads it — the same silent-failure `requires_local` has a test for."""
    for agent_id in BUILDERS:
        lines = (ROOT / "agents" / agent_id / "agent.toml").read_text("utf-8").splitlines()
        decl = next(i for i, ln in enumerate(lines) if ln.startswith("standalone"))
        app_table = next(i for i, ln in enumerate(lines) if ln.strip() == "[app]")
        later_table = next(
            (i for i, ln in enumerate(lines) if i > app_table and ln.strip().startswith("[")),
            10**6,
        )
        assert app_table < decl < later_table, f"{agent_id}: standalone escaped the [app] table"
