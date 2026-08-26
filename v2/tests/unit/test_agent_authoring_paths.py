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


# ──────────────── the registry: per-REQUEST account-layer lookup ────────────────
# The per-connection overlay rides a contextvar; HTTP (app serving) has none, so the
# gateway names the layer(s) explicitly. Same data, second door.


def test_find_in_account_layers_named_account(world):
    world.current["acct"] = "acct_a"
    world.registry.create_from("mkt", _skeleton)
    world.current["acct"] = None  # the HTTP request carries no contextvar
    spec = world.registry.find_in_account_layers("mkt", "acct_a")
    assert spec is not None and spec.id == "mkt"
    assert world.registry.find_in_account_layers("mkt", "acct_b") is None, (
        "another account's layer must not answer for a named caller"
    )


def test_find_in_account_layers_deployment_owner_view(world):
    world.current["acct"] = "acct_a"
    world.registry.create_from("mkt", _skeleton)
    world.current["acct"] = None
    spec = world.registry.find_in_account_layers("mkt")  # no account named = every layer
    assert spec is not None and spec.id == "mkt"
    assert world.registry.find_in_account_layers("ghost") is None


# ───────────────── ONE FOLDER PER AGENT: definition + workspace + sessions ─────────────────


def test_an_agents_definition_and_its_data_are_one_folder(tmp_path):
    """THE layout rule. Two trees (`installed/agents/<id>` for the definition, `agents/<id>` for
    the data) meant every reader had to know which half it wanted, and an authored agent lived
    in a directory labelled "installed" — which read as "not yours to edit"."""
    agents = user_state.account_agents_dir(tmp_path, "acct_a")
    assert agents == tmp_path / "accounts" / "acct_a" / "agents"
    assert user_state.account_agent_dir(tmp_path, "acct_a", "mkt") == agents / "mkt"
    assert user_state.account_workspace(tmp_path, "acct_a", "mkt") == agents / "mkt" / "workspace"
    assert user_state.account_state_dir(tmp_path, "acct_a", "mkt") == agents / "mkt"
    assert "installed" not in str(user_state.account_plugins_dir(tmp_path, "acct_a"))


def test_migration_folds_the_legacy_installed_tree_in(tmp_path):
    legacy = tmp_path / "accounts" / "acct_a" / "installed" / "agents" / "mkt"
    (legacy / "ui").mkdir(parents=True)
    (legacy / "agent.toml").write_text('name = "Mkt"\n', encoding="utf-8")
    (legacy / "ui" / "index.html").write_text("<html/>", encoding="utf-8")
    (tmp_path / "accounts" / "acct_a" / "installed" / "plugins" / "img").mkdir(parents=True)

    assert user_state.migrate_legacy_account_layout(tmp_path) == ["acct_a"]
    agent = user_state.account_agent_dir(tmp_path, "acct_a", "mkt")
    assert (agent / "agent.toml").is_file() and (agent / "ui" / "index.html").is_file()
    assert (user_state.account_plugins_dir(tmp_path, "acct_a") / "img").is_dir()
    assert not (tmp_path / "accounts" / "acct_a" / "installed").exists()
    assert user_state.migrate_legacy_account_layout(tmp_path) == [], "must be idempotent"


def test_migration_never_overwrites_live_data(tmp_path):
    """The destination is where transcripts and workspace files have been accumulating; the
    legacy tree often carries an EMPTY workspace/ from scaffold time. Losing a real workspace to
    an empty one is the single unrecoverable outcome here."""
    acct = tmp_path / "accounts" / "acct_a"
    legacy = acct / "installed" / "agents" / "mkt"
    (legacy / "workspace").mkdir(parents=True)  # the empty scaffold one
    (legacy / "agent.toml").write_text('name = "Mkt"\n', encoding="utf-8")
    live = acct / "agents" / "mkt"
    (live / "workspace").mkdir(parents=True)
    (live / "workspace" / "poster.png").write_bytes(b"real work")
    (live / "sessions").mkdir()
    (live / "sessions" / "s.jsonl").write_text("{}\n", encoding="utf-8")

    user_state.migrate_legacy_account_layout(tmp_path)
    assert (live / "workspace" / "poster.png").read_bytes() == b"real work"
    assert (live / "sessions" / "s.jsonl").is_file()
    assert (live / "agent.toml").is_file(), "the definition still moved in beside the data"


def test_removing_an_account_agent_keeps_its_chats_and_files(world):
    """One folder means deleting the definition could delete the person's history with it."""
    world.current["acct"] = "acct_a"
    spec = world.registry.create_from("doomed", _skeleton)
    d = Path(spec.dir)
    (d / "sessions").mkdir(exist_ok=True)
    (d / "sessions" / "chat.jsonl").write_text("{}\n", encoding="utf-8")
    (d / "workspace" / "keep.txt").write_text("mine", encoding="utf-8")

    world.registry.remove("doomed")
    assert not (d / "agent.toml").exists(), "the definition is gone"
    assert (d / "sessions" / "chat.jsonl").is_file(), "the user's chats are NOT"
    assert (d / "workspace" / "keep.txt").is_file()
    assert "doomed" not in world.registry.list_ids(), "and it no longer lists"


def test_account_agents_layers_enumerates_every_layer(world):
    """Process-global work (private-plugin discovery's one daemon-wide tool map, the /apps
    deployment-owner view) runs where NO connection exists — boot — so it cannot ride the
    contextvar overlay. The enumeration is the layout owner's (user_state) one answer."""
    world.current["acct"] = "acct_a"
    world.registry.create_from("mkt", _skeleton)
    world.current["acct"] = "acct_b"
    world.registry.create_from("ops", _skeleton)
    world.current["acct"] = None
    layers = user_state.account_agents_layers(world.state)
    assert [acct for acct, _ in layers] == ["acct_a", "acct_b"]
    assert layers[0][1] == user_state.account_agents_dir(world.state, "acct_a")
    assert user_state.account_agents_layers(world.state / "absent") == []


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
    create_agent lands in THEIR layer, stamped to THEM, resolvable immediately.

    (Briefly reversed to shared placement in 2026-08 and reversed back the same day — the
    overlay IS the intended home for a signed-in author's agents.)"""
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
