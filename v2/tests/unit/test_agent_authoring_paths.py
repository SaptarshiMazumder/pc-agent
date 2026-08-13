"""Agent authoring follows the CALLER — the one-authority fix, pinned.

The deadlock these tests keep dead: create_agent wrote where the REGISTRY said the caller's
agents live (the account overlay — correct), while check_write judged it against a scope
expanded from WHERE THE AGENT-BUILDER'S OWN FILES SIT (the shared catalogue — stale). Signed
in, the two disagreed: the compliant write was refused and the refusal steered the model into
authoring an unstamped agent in the shared layer instead.

The fix, in three parts, each pinned below:

  * the registry answers "every root where THIS caller's agents may live" (agent_roots), and
    AgentService expands the `<agents_dir>` scope token by ASKING it — never by deriving from
    file layout (diagrams/agent-authoring-current-and-fix.puml);
  * creation has ONE path (FileAgentRegistry.create_from): placement, layer-aware collision,
    ownership stamp at birth, live registration — content is a closure the caller supplies;
  * the create_agent tool delegates its world half to that path, so it CANNOT place, collide
    or stamp differently.
"""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(
    0, str(Path(__file__).resolve().parents[2] / "agents" / "agent-builder" / "plugins" / "agent-authoring")
)

from agent_runtime.application.services.agent_service import AgentService
from agent_runtime.domain import ownership
from agent_runtime.infrastructure import user_state
from agent_runtime.infrastructure.agents import FileAgentRegistry, ownership_store


def _agent_dir(root: Path, agent_id: str, name: str = "") -> Path:
    d = root / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.toml").write_text(f'name = "{name or agent_id}"\n', encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path):
    """A hosted-shaped world: shared catalogue + accounts, registry overlay follows `current`.
    Same shape as test_account_agent_overlay's fixture — the deployment the deadlock hit."""
    shared = tmp_path / "agents"
    _agent_dir(shared, "main")
    _agent_dir(shared, "curated", "Curated Agent")

    state = tmp_path / "state"
    current = {"acct": None}

    def overlay():
        acct = current["acct"]
        return user_state.account_agents_dir(state, acct) if acct else None

    config = SimpleNamespace(
        agents_dir=shared,
        state_dir=state,
        agent_name="Assistant",
        plugins={},
        skills_dir=shared / "main" / "skills",
        hosted=True,
    )
    registry = FileAgentRegistry(config, overlay_dir=overlay, account_id=lambda: current["acct"])
    return SimpleNamespace(registry=registry, state=state, shared=shared, current=current)


def _skeleton(d: Path) -> None:
    (d / "agent.toml").write_text('name = "New Agent"\nversion = "1.0.0"\n', encoding="utf-8")
    (d / "IDENTITY.md").write_text("I am new.\n", encoding="utf-8")


# ───────────────────────── the registry: ONE creation path ─────────────────────────


def test_signed_in_create_lands_in_the_account_overlay(world):
    world.current["acct"] = "acct_a"
    spec = world.registry.create_from("newbie", _skeleton)
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert Path(spec.dir) == overlay_root / "newbie"
    assert (overlay_root / "newbie" / "agent.toml").is_file()
    assert not (world.shared / "newbie").exists(), "a signed-in create must NOT touch shared"
    assert "newbie" in world.registry.list_ids(), "registered live, no restart"


def test_created_agent_is_stamped_at_birth(world):
    world.current["acct"] = "acct_a"
    spec = world.registry.create_from("stamped", _skeleton)
    record = ownership_store.read(spec.dir)
    assert record is not None, "birth without a record is the legacy hole this fix closes"
    assert record.owner == "acct_a"
    assert record.origin == ownership.AUTHORED


def test_signed_out_create_lands_shared_and_is_the_deployments_own(world):
    spec = world.registry.create_from("op-agent", _skeleton)
    assert Path(spec.dir) == world.shared / "op-agent"
    record = ownership_store.read(spec.dir)
    assert record is not None
    assert record.owner == ownership.stamp_owner(None, hosted=True)


def test_overlay_create_cannot_shadow_a_curated_id(world):
    """The layer-aware collision: `curated` exists only in SHARED, so a write-layer-only
    check (the tool's old d.is_dir()) would have scaffolded a shadowing skeleton."""
    world.current["acct"] = "acct_a"
    with pytest.raises(ValueError, match="already exists"):
        world.registry.create_from("curated", _skeleton)
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert not (overlay_root / "curated").exists()


def test_registry_create_still_stamps_via_the_one_path(world):
    """create() is now a content closure over create_from — the refactor must not have
    dropped anything: workspace, agent.toml content, the stamp."""
    world.current["acct"] = "acct_b"
    spec = world.registry.create("via-create", name="Via Create", identity="I exist.")
    d = Path(spec.dir)
    assert (d / "workspace").is_dir()
    assert 'name = "Via Create"' in (d / "agent.toml").read_text(encoding="utf-8")
    assert (d / "IDENTITY.md").is_file()
    assert ownership_store.read(d).owner == "acct_b"


# ───────────────────────── the registry: caller-shaped roots ─────────────────────────


def test_agent_roots_follow_the_caller(world):
    assert world.registry.agent_roots() == (world.shared,)
    world.current["acct"] = "acct_a"
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert world.registry.agent_roots() == (world.shared, overlay_root)


# ───────────────────── AgentService: scope expansion ASKS the registry ─────────────────────


def _service(registry, installed_agents=None):
    return AgentService(
        engine=None,
        tools=[],
        registry=registry,
        make_session=lambda *_: None,
        build_prompt=lambda *_, **__: "",
        installed_agents=installed_agents,
    )


def _builder_spec(world):
    """A stand-in for the agent-builder: an agent whose own definition sits in SHARED —
    exactly the shape whose parent-derived scope went stale."""
    return SimpleNamespace(dir=str(world.shared / "agent-builder"))


def test_scope_token_expands_to_every_caller_root(world):
    world.current["acct"] = "acct_a"
    svc = _service(world.registry)
    roots = svc._expand_paths(_builder_spec(world), ("<agents_dir>",))
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert roots == (str(world.shared), str(overlay_root)), (
        "THE deadlock: the overlay (where create_agent writes) missing from the scope "
        "(what check_write enforces)"
    )


def test_scope_token_signed_out_is_the_shared_root_alone(world):
    svc = _service(world.registry)
    assert svc._expand_paths(_builder_spec(world), ("<agents_dir>",)) == (str(world.shared),)


def test_agent_dir_token_still_expands_inside_agents_dir(world):
    """A self-deny like `<agent_dir>` must survive the multi-root expansion untouched."""
    world.current["acct"] = "acct_a"
    svc = _service(world.registry)
    spec = _builder_spec(world)
    denies = svc._expand_paths(spec, ("<agent_dir>",))
    assert denies == (str(world.shared / "agent-builder"),)


def test_registry_without_agent_roots_keeps_the_legacy_derivation(world):
    """Minimal stand-in registries (older fakes) fall back to agent_dir.parent — in a
    single-layer world the same answer as before, byte-for-byte."""
    svc = _service(SimpleNamespace())  # no agent_roots on this registry
    spec = _builder_spec(world)
    assert svc._expand_paths(spec, ("<agents_dir>",)) == (str(world.shared),)


def test_protected_installed_agents_are_guarded_in_every_root(world):
    world.current["acct"] = "acct_a"
    svc = _service(world.registry, installed_agents=lambda: frozenset({"paid-agent"}))
    protected = svc._protected_paths(_builder_spec(world))
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert str(world.shared / "paid-agent") in protected
    assert str(overlay_root / "paid-agent") in protected


def test_unreadable_ledger_protects_every_root(world):
    world.current["acct"] = "acct_a"
    svc = _service(world.registry, installed_agents=lambda: None)
    protected = svc._protected_paths(_builder_spec(world))
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert set(protected) == {str(world.shared), str(overlay_root)}


# ──────────────── the create_agent tool: world half fully delegated ────────────────


def _tool(world):
    from agent_authoring.presentation.create_agent_tool import CreateAgentTool

    return CreateAgentTool(world.registry)


def test_tool_creates_into_the_overlay_stamped_and_live(world):
    """END-TO-END of the deadlock scenario, minus the stale scope: a signed-in caller's
    create_agent lands in THEIR layer, stamped to THEM, resolvable immediately."""
    world.current["acct"] = "acct_a"
    tool = _tool(world)
    res = asyncio.run(
        tool.execute("c1", {"id": "marketing-agent", "identity": "I market."}, asyncio.Event())
    )
    assert not res.is_error, res.content[0].text
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    d = overlay_root / "marketing-agent"
    assert (d / "agent.toml").is_file() and (d / "IDENTITY.md").is_file()
    assert ownership_store.read(d).owner == "acct_a"
    assert "marketing-agent" in world.registry.list_ids()
    assert not (world.shared / "marketing-agent").exists()


def test_tool_refuses_to_shadow_a_curated_agent(world):
    """The tool's old existence check looked only at its write layer — an overlay caller
    could 'create' an id that already existed shared. Now it sees what the caller sees."""
    world.current["acct"] = "acct_a"
    tool = _tool(world)
    res = asyncio.run(
        tool.execute("c1", {"id": "curated", "identity": "shadow attempt"}, asyncio.Event())
    )
    assert res.is_error
    assert "ASK THEM" in res.content[0].text  # the decision goes back to the user
    overlay_root = user_state.account_agents_dir(world.state, "acct_a")
    assert not (overlay_root / "curated").exists()


def test_tool_update_finds_the_agent_in_whichever_layer_it_lives(world):
    """Rebuild resolves via resolve_dir (the union), not agents_dir/<id> (one layer): a
    signed-in caller can still rebuild an agent that lives in SHARED."""
    world.current["acct"] = "acct_a"
    tool = _tool(world)
    res = asyncio.run(
        tool.execute(
            "c1",
            {"action": "update", "id": "curated", "identity": "rebuilt", "confirm_overwrite": True},
            asyncio.Event(),
        )
    )
    assert not res.is_error, res.content[0].text
    assert "rebuilt" in (world.shared / "curated" / "IDENTITY.md").read_text(encoding="utf-8")
