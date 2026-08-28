"""Agent-private plugins: tools shipped INSIDE an agent's own folder (agents/<id>/plugins/) —
discovered per agent, offered ONLY to the owner (implicitly allowed, deny wins), invisible to
other agents and the global catalog, and travelling inside the .agentpkg with the agent."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.application.services.agent_service import AgentService
from agent_runtime.domain.agent import select_private_tools
from agent_runtime.infrastructure.plugins import discover_agent_plugins


def _write_private_plugin(agents_dir: Path, agent_id: str, tool_name: str, module: str) -> None:
    pdir = agents_dir / agent_id / "plugins" / f"{tool_name}-kit"
    pdir.mkdir(parents=True)
    # Only a DECLARED agent ships private tools: an account's agents root also holds folders for
    # agents it merely used, which are the user's own writable space — code found there must not
    # be loaded as that agent's tool.
    toml = agents_dir / agent_id / "agent.toml"
    if not toml.is_file():
        toml.write_text(f'name = "{agent_id}"\n', encoding="utf-8")
    (pdir / "plugin.toml").write_text(
        f'id = "{tool_name}-kit"\nname = "Kit"\nkind = "native"\nentry = "{module}:register"\n',
        encoding="utf-8",
    )
    (pdir / f"{module}.py").write_text(
        "class T:\n"
        f"    name = '{tool_name}'\n"
        "    description = 'private test tool'\n"
        "    parameters = {'type': 'object', 'properties': {}}\n"
        "    async def execute(self, *_a, **_k):\n"
        "        raise NotImplementedError\n"
        "def register(api, ctx):\n"
        "    api.register_tool(T())\n",
        encoding="utf-8",
    )


# ---- discovery ---------------------------------------------------------------------------------
def test_discover_agent_plugins_tags_owner(tmp_path):
    agents = tmp_path / "agents"
    _write_private_plugin(agents, "spec-agent", "spec_tool", "tkit_priv_mod_a")
    (agents / "plain-agent").mkdir()  # an agent with no plugins/ contributes nothing
    out = discover_agent_plugins(agents, SimpleNamespace(plugins={}))
    assert list(out) == ["spec-agent"]
    (tool,) = out["spec-agent"]
    assert tool.name == "spec_tool"
    assert tool._agent_id == "spec-agent" and tool._plugin_id == "spec_tool-kit"


# ---- service isolation ---------------------------------------------------------------------------
class _SharedTool:
    name = "shared_tool"
    description = "in the global catalog"


class _PrivTool:
    name = "priv_tool"
    description = "ships with demo"
    _agent_id = "demo"


def _service(agent_tools=None, demo_deny=()):
    # The private-tool map is keyed by LOCATION (agent_dir_key of the agent's folder), not by
    # id — ids can collide across account layers on a hosted daemon. The specs carry the dirs.
    from agent_runtime.domain.agent import agent_dir_key

    demo = SimpleNamespace(id="demo", dir="/agents/demo", tools_allow=None, tools_deny=demo_deny)
    other = SimpleNamespace(id="other", dir="/agents/other", tools_allow=None, tools_deny=())
    specs = {"demo": demo, "other": other}

    def _get(aid):
        if aid in specs:
            return specs[aid]
        raise KeyError(aid)

    return AgentService(
        engine=SimpleNamespace(),
        tools=[_SharedTool()],
        registry=SimpleNamespace(get=_get, resolve=lambda _k: specs["demo"]),
        make_session=lambda *_a: None,
        build_prompt=lambda *_a, **_k: "",
        agent_tools=agent_tools or {agent_dir_key(demo.dir): [_PrivTool()]},
    )


def test_private_tools_only_for_owner():
    svc = _service()
    demo_names = [t.name for t in svc._select_tools("demo")]
    other_names = [t.name for t in svc._select_tools("other")]
    catalog_names = [t.name for t in svc._select_tools(None)]
    assert "priv_tool" in demo_names and "shared_tool" in demo_names
    assert "priv_tool" not in other_names  # isolation: never another agent's
    assert "priv_tool" not in catalog_names  # never the global catalog


def test_private_tool_implicitly_allowed_but_deny_wins():
    # an allowlist that doesn't name the private tool must NOT exclude it (it ships with
    # the agent), but an explicit deny must.
    spec_allow = SimpleNamespace(id="demo", tools_allow=("shared_tool",), tools_deny=())
    assert [t.name for t in select_private_tools([_PrivTool()], spec_allow)] == ["priv_tool"]
    svc2 = _service(demo_deny=("priv_tool",))
    assert "priv_tool" not in [t.name for t in svc2._select_tools("demo")]


def test_find_tool_agent_aware():
    svc = _service()
    assert svc.find_tool("priv_tool") is None  # global lookup can't see privates
    assert svc.find_tool("priv_tool", "demo").name == "priv_tool"
    assert svc.find_tool("shared_tool", "demo").name == "shared_tool"
    assert svc.agent_private_tools("demo")[0].name == "priv_tool"
    assert svc.agent_private_tools("other") == []
    svc.set_agent_tools({})  # hot-reload replaces wholesale
    assert svc.find_tool("priv_tool", "demo") is None


# ---- scoped tools.invoke: an app can drive its agent's own tools --------------------------------
class _ExecPrivTool:
    name = "priv_exec"
    description = "private executable"
    _agent_id = "demo"
    artifact_action = None  # NOT a canvas tool — only invokable because it's the agent's own

    async def execute(self, _id, params, _abort):
        return SimpleNamespace(
            content=[SimpleNamespace(text="private ran")], is_error=False, artifacts=[], details=None
        )


def test_scoped_invoke_runs_private_tool(tmp_path):
    from agent_runtime.presentation.gateway import Gateway

    spec = SimpleNamespace(
        id="demo",
        # a restrictive allowlist that does NOT name the private tool — it must still run,
        # because it ships with the agent (implicitly allowed)
        tools_allow=("ls",),
        tools_deny=(),
        workspace=str(tmp_path),
        plugins={},
    )
    cfg = SimpleNamespace(
        agent_name="j", agent_id="main", state_dir=str(tmp_path), workspace=str(tmp_path)
    )
    gw = Gateway(
        config=cfg,
        service=SimpleNamespace(
            find_tool=lambda n, a=None: (
                _ExecPrivTool() if (n == "priv_exec" and a == "demo") else None
            )
        ),
        registry=SimpleNamespace(
            get=lambda a: spec if a == "demo" else (_ for _ in ()).throw(KeyError(a))
        ),
    )
    out = asyncio.run(gw._tools_invoke({"name": "priv_exec"}, "demo"))
    assert out["text"] == "private ran"
    # denied by the agent -> refused even though it's the agent's own
    spec.tools_deny = ("priv_exec",)
    try:
        asyncio.run(gw._tools_invoke({"name": "priv_exec"}, "demo"))
        raise AssertionError("denied private tool must be refused")
    except RuntimeError as e:
        assert "not available to agent" in str(e)


# ---- shipping: the private plugin travels inside the .agentpkg ----------------------------------
def test_private_plugin_ships_inside_agentpkg(tmp_path):
    from agent_runtime.domain.bundle import BundleManifest
    from agent_runtime.infrastructure.marketplace.bundle_io import pack_bundle, unpack_bundle

    src_agents = tmp_path / "src"
    _write_private_plugin(src_agents, "kiosk", "kiosk_tool", "tkit_priv_pack_mod")
    (src_agents / "kiosk" / "agent.toml").write_text('name = "Kiosk"\n', encoding="utf-8")

    manifest = BundleManifest(id="kiosk", name="Kiosk", version="1.0.0", description="x")
    pkg = pack_bundle(src_agents / "kiosk", tmp_path / "out", manifest)
    agents_dir = tmp_path / "agents"
    unpack_bundle(pkg, manifest, agents_dir, tmp_path / "plugins")
    assert (agents_dir / "kiosk" / "plugins" / "kiosk_tool-kit" / "plugin.toml").is_file()
    # and the INSTALLED copy's private tools discover cleanly (fresh root, no repo paths)
    out = discover_agent_plugins(agents_dir, SimpleNamespace(plugins={}))
    assert "kiosk_tool" in [t.name for t in out.get("kiosk", [])]


# ---- samples/: the loader must reach every root the ROSTER reaches ------------------------------
def test_a_sample_agents_private_tools_are_discovered(tmp_path):
    """`agents/samples/<id>/` is a second definition root (FileAgentRegistry._scan_samples), so a
    sample is a registered, runnable agent. Discovery scanned only one level, which made LOADING
    stricter than the roster — the agent listed, opened its window, and every tool call came back
    `tool not available`. A registered agent with none of its own tools is the worst kind of
    broken: it looks installed and fails at first use."""
    agents_dir = tmp_path / "agents"
    _write_private_plugin(agents_dir, "ordinary", "top_tool", "tkit_priv_top_mod")
    _write_private_plugin(agents_dir / "samples", "exemplar", "sample_tool", "tkit_priv_sample_mod")

    out = discover_agent_plugins(agents_dir, SimpleNamespace(plugins={}))

    assert "top_tool" in [t.name for t in out.get("ordinary", [])]
    assert "sample_tool" in [t.name for t in out.get("exemplar", [])], (
        "a sample's private tools must load — the registry registers it, so the loader must too"
    )
    # and the container folder is not itself an agent (it has no agent.toml)
    assert "samples" not in out


def test_the_samples_folder_alone_registers_nothing(tmp_path):
    """The `agent.toml` guard still applies to the second root: a stray plugins/ dir directly
    under samples/ must not become a tool of an agent called "samples"."""
    agents_dir = tmp_path / "agents"
    stray = agents_dir / "samples" / "plugins" / "x-kit"
    stray.mkdir(parents=True)
    (stray / "plugin.toml").write_text(
        'id = "x-kit"\nname = "Kit"\nkind = "native"\nentry = "nope:register"\n', encoding="utf-8"
    )
    assert discover_agent_plugins(agents_dir, SimpleNamespace(plugins={})) == {}


def test_tools_carry_the_folder_they_were_found_in(tmp_path):
    """The private-tool map is keyed by LOCATION, and the container builds that key from
    `_plugin_agent_dir` — the folder discovery actually walked — while the service looks it up
    from the resolved spec's `dir`. If the stamp disagreed with the real folder the two keys
    would never meet, and the agent would run with none of its own tools while the load log
    reported shipping them. (The container used to rebuild the key as `root / id`, which is
    exactly that bug for anything not sitting one level down.)"""
    agents_dir = tmp_path / "agents"
    _write_private_plugin(agents_dir / "samples", "nested", "nested_tool", "tkit_priv_nested_mod")

    out = discover_agent_plugins(agents_dir, SimpleNamespace(plugins={}))
    tools = out["nested"]
    stamped = {getattr(t, "_plugin_agent_dir", "") for t in tools}

    assert stamped == {str(agents_dir / "samples" / "nested")}, (
        f"the stamp must be the real folder, not a reconstruction: {stamped}"
    )
    assert str(agents_dir / "nested") not in stamped
