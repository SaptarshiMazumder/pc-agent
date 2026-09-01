"""Per-account installed agents: the shared catalogue + one account's own overlay.

Sessions, workspace and semantic memory were already partitioned per account (user_state +
accounts.memory_partition). The agent CATALOGUE was not — and that was fine while the only agents
were the ones we shipped. The marketplace broke it: an install unpacked into the daemon-global
agents_dir, so on a hosted daemon one visitor's install showed up in everybody's list and their
uninstall deleted it for everybody.

The tests below are mostly about what one account CANNOT see or do to another, because those are
the failures that look like success from the inside.
"""

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agent_runtime.infrastructure import user_state
from agent_runtime.infrastructure.agents import FileAgentRegistry


def _agent_dir(root: Path, agent_id: str, name: str = "") -> Path:
    d = root / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.toml").write_text(f'name = "{name or agent_id}"\n', encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path):
    """A shared catalogue plus two accounts, and a registry whose overlay follows `current`."""
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
        # This file models the HOSTED world (many accounts, one shared catalogue). Ownership
        # semantics differ by deployment — desktop cases get their own fixtures below.
        hosted=True,
    )
    registry = FileAgentRegistry(
        config, overlay_dir=overlay, account_id=lambda: current["acct"]
    )
    return SimpleNamespace(
        registry=registry,
        state=state,
        shared=shared,
        current=current,
        install=lambda acct, aid, name="": _agent_dir(
            user_state.account_agents_dir(state, acct), aid, name
        ),
    )


# ─────────────────────────── the catalogue is still shared ───────────────────────────


def test_no_account_sees_only_the_shared_catalogue(world):
    assert world.registry.list_ids() == ["curated", "main"]


def test_an_account_still_sees_the_shared_catalogue(world):
    world.current["acct"] = "acct_a"
    assert set(world.registry.list_ids()) == {"curated", "main"}


# ─────────────────────────── the overlay ───────────────────────────


def test_an_install_is_visible_to_its_own_account(world):
    world.install("acct_a", "alpha")
    world.current["acct"] = "acct_a"
    world.registry.refresh()
    assert set(world.registry.list_ids()) == {"curated", "main", "alpha"}
    assert world.registry.get("alpha").id == "alpha"


def test_an_install_is_invisible_to_everyone_else(world):
    """THE regression. One visitor installing an agent must not put it in anybody else's list."""
    world.install("acct_a", "alpha")
    world.registry.refresh()

    world.current["acct"] = "acct_b"
    assert "alpha" not in world.registry.list_ids()
    with pytest.raises(KeyError):
        world.registry.get("alpha")

    world.current["acct"] = None  # desktop / no account
    assert "alpha" not in world.registry.list_ids()


def test_two_accounts_keep_separate_installs(world):
    world.install("acct_a", "alpha")
    world.install("acct_b", "beta")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert set(world.registry.list_ids()) == {"curated", "main", "alpha"}
    world.current["acct"] = "acct_b"
    assert set(world.registry.list_ids()) == {"curated", "main", "beta"}


def test_an_overlay_agent_shadows_a_curated_one_for_that_account_only(world):
    """Installing your own build of a curated agent replaces it FOR YOU, not for everyone."""
    world.install("acct_a", "curated", "My Fork")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert world.registry.get("curated").name == "My Fork"
    world.current["acct"] = "acct_b"
    assert world.registry.get("curated").name == "Curated Agent"


def test_overlay_never_synthesizes_main(world):
    """`main` is the shared layer's job. An overlay inventing one would give the account an agent
    it never installed, rooted in a directory that does not exist."""
    world.install("acct_a", "alpha")
    world.registry.refresh()
    world.current["acct"] = "acct_a"
    assert world.registry.get("main").dir == world.shared / "main"


def test_resolve_falls_back_to_main_per_account(world):
    world.current["acct"] = "acct_a"
    assert world.registry.resolve("nosuchagent:1").id == "main"


# ─────────────────────────── writes go to the right layer ───────────────────────────


def test_create_writes_into_the_callers_overlay(world):
    world.current["acct"] = "acct_a"
    world.registry.create("mine", name="Mine")

    assert (user_state.account_agents_dir(world.state, "acct_a") / "mine").is_dir()
    assert not (world.shared / "mine").exists(), "must not land in the shared catalogue"

    world.current["acct"] = "acct_b"
    assert "mine" not in world.registry.list_ids()


def test_create_refuses_to_shadow_something_the_caller_can_already_see(world):
    world.current["acct"] = "acct_a"
    with pytest.raises(ValueError, match="already exists"):
        world.registry.create("curated")


def test_agents_dir_points_at_the_write_target(world):
    assert world.registry.agents_dir == world.shared
    world.current["acct"] = "acct_a"
    assert world.registry.agents_dir == user_state.account_agents_dir(world.state, "acct_a")


def test_the_shared_root_never_follows_the_caller(world):
    """THE PUBLISH_AGENT VANISHING ACT. Private-plugin discovery rebuilds ONE process-global
    tool map; when it read `agents_dir` (the write target) from inside an account-scoped
    hot-reload, it re-scanned only that account's overlay and replaced the whole map —
    creating one agent while signed in removed agent-builder's own tools daemon-wide.
    Process-global work names its roots: the shared catalogue is caller-independent, and the
    overlay is asked for explicitly."""
    world.current["acct"] = "acct_a"
    assert world.registry.shared_agents_dir == world.shared, "signed in must not move it"
    assert world.registry.overlay_path() == user_state.account_agents_dir(world.state, "acct_a")
    world.current["acct"] = None
    assert world.registry.shared_agents_dir == world.shared
    assert world.registry.overlay_path() is None


def test_add_prefers_the_overlay_copy(world):
    """A marketplace install lands in the overlay; loading the shared copy of the same id instead
    would run the curated agent while the user's own install sat on disk doing nothing."""
    world.install("acct_a", "curated", "My Fork")
    world.current["acct"] = "acct_a"
    assert world.registry.add("curated").name == "My Fork"


# ─────────────────────────── the destructive one ───────────────────────────


def test_an_account_cannot_remove_a_shared_agent(world):
    """Without this, an ordinary uninstall rmtree's the shared catalogue — one user removing an
    agent from every other user's account, permanently, with the UI reporting success."""
    world.current["acct"] = "acct_a"
    with pytest.raises(ValueError, match="shared catalogue"):
        world.registry.remove("curated")
    assert (world.shared / "curated").is_dir()

    world.current["acct"] = None
    assert "curated" in world.registry.list_ids()


def test_an_account_removes_only_its_own_copy(world):
    world.install("acct_a", "alpha")
    world.install("acct_b", "alpha")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    world.registry.remove("alpha")
    assert "alpha" not in world.registry.list_ids()

    world.current["acct"] = "acct_b"
    assert "alpha" in world.registry.list_ids(), "the other account's copy must survive"
    assert (user_state.account_agents_dir(world.state, "acct_b") / "alpha").is_dir()


def test_removing_a_shadow_reveals_the_curated_agent_again(world):
    world.install("acct_a", "curated", "My Fork")
    world.registry.refresh()
    world.current["acct"] = "acct_a"
    assert world.registry.get("curated").name == "My Fork"

    world.registry.remove("curated")
    assert world.registry.get("curated").name == "Curated Agent"


def test_removing_main_is_still_refused(world):
    world.current["acct"] = "acct_a"
    with pytest.raises(ValueError, match="main"):
        world.registry.remove("main")


def test_unknown_agent_still_raises_keyerror(world):
    world.current["acct"] = "acct_a"
    with pytest.raises(KeyError):
        world.registry.remove("nosuch")


# ─────────────────────────── robustness ───────────────────────────


def test_a_broken_overlay_resolver_degrades_to_the_shared_catalogue(tmp_path):
    """A resolver that throws must not take the daemon's agent list with it."""
    shared = tmp_path / "agents"
    _agent_dir(shared, "main")

    def boom():
        raise RuntimeError("contextvar exploded")

    registry = FileAgentRegistry(
        SimpleNamespace(
            agents_dir=shared, state_dir=tmp_path / "state", agent_name="Assistant", plugins={}
        ),
        overlay_dir=boom,
    )
    assert registry.list_ids() == ["main"]


def test_a_missing_overlay_dir_is_simply_empty(world):
    """An account that has never installed anything has no directory — not an error."""
    world.current["acct"] = "acct_never"
    assert set(world.registry.list_ids()) == {"curated", "main"}


def test_paths_of_two_accounts_do_not_overlap(tmp_path):
    a = user_state.account_agents_dir(tmp_path, "acct_a")
    b = user_state.account_agents_dir(tmp_path, "acct_b")
    assert a != b and not str(a).startswith(str(b)) and not str(b).startswith(str(a))


def test_account_ids_are_sanitised_into_the_path(tmp_path):
    """account_id arrives over the network and becomes a directory name."""
    hostile = user_state.account_agents_dir(tmp_path, "../../etc")
    assert tmp_path.resolve() in hostile.resolve().parents



# ─────────────────────────── visible is not the same as yours ───────────────────────────
#
# THE PUBLISH REGRESSION. `publish_agent` built its path from `agents_dir` — the WRITE target —
# so a signed-in user publishing an agent the sidebar plainly showed got "no agent 'bedtime-kids'"
# naming a directory they had never heard of. Two questions, two answers: `resolve_dir` says where
# an agent IS (the same union reads use), `owns` says whose it is (the caller's write layer).


def test_resolve_dir_finds_a_shared_agent_for_a_signed_in_account(world):
    """The exact failure: signed in, agent lives in the shared catalogue, lookup must still land."""
    world.current["acct"] = "acct_a"
    assert world.registry.resolve_dir("curated") == world.shared / "curated"


def test_resolve_dir_prefers_the_overlay_copy(world):
    """Same precedence as every read — anything else and publish would ship the curated agent
    while the user's own fork sat on disk doing nothing."""
    world.install("acct_a", "curated", "My Fork")
    world.registry.refresh()
    world.current["acct"] = "acct_a"
    expected = user_state.account_agents_dir(world.state, "acct_a") / "curated"
    assert world.registry.resolve_dir("curated") == expected


def test_resolve_dir_does_not_leak_another_accounts_install(world):
    world.install("acct_a", "alpha")
    world.registry.refresh()
    world.current["acct"] = "acct_b"
    assert world.registry.resolve_dir("alpha") is None
    assert world.registry.resolve_dir("nosuch") is None


def test_the_operator_owns_the_catalogue(world):
    """No account on a hosted daemon = the machine token = the platform acting on itself, and
    the shared catalogue is the platform's — including the synthesized main."""
    assert world.registry.owns("curated")
    assert world.registry.owns("main")


def test_signed_in_the_catalogue_is_not_yours(world):
    world.install("acct_a", "alpha")
    world.registry.refresh()
    world.current["acct"] = "acct_a"
    assert world.registry.owns("alpha")
    assert not world.registry.owns("curated"), "visible, openable — but not theirs to publish"
    assert not world.registry.owns("main")


# ─────────────────────────── ownership records beat layout ───────────────────────────
#
# Step 2: `owns` reads the `.agentd-meta.json` record when there is one; a record-less dir
# falls back to exactly the Step-1 layer rule (`presumed_owner`). Desktop and hosted differ in
# ONE way: on a desktop the human is the operator, so "local" stays among their identities even
# while signed in — signing in adds an identity, it never subtracts one.


def _record(agent_dir, owner, origin="authored"):
    from agent_runtime.domain import ownership
    from agent_runtime.infrastructure.agents import ownership_store

    ownership_store.write(agent_dir, ownership.OwnershipRecord(owner=owner, origin=origin))


def _desktop_world(tmp_path, acct):
    """A DESKTOP registry (hosted=False): same layers, different caller identities."""
    shared = tmp_path / "agents"
    _agent_dir(shared, "main")
    _agent_dir(shared, "game-master", "Game Master")
    current = {"acct": acct}
    config = SimpleNamespace(
        agents_dir=shared, state_dir=tmp_path / "state", agent_name="Assistant", plugins={},
        hosted=False,
    )
    registry = FileAgentRegistry(
        config,
        overlay_dir=lambda: (
            user_state.account_agents_dir(tmp_path / "state", current["acct"])
            if current["acct"]
            else None
        ),
        account_id=lambda: current["acct"],
    )
    return registry, shared


def test_desktop_signing_in_never_subtracts_ownership(tmp_path):
    """THE game-master case: a record-less checkout agent stays the operator's after sign-in.
    Step 1 got this wrong (layer rule said 'not your write layer, not yours')."""
    registry, _ = _desktop_world(tmp_path, acct="acct_a")
    assert registry.owns("game-master")


def test_a_curated_record_is_honored_even_on_desktop(tmp_path):
    """A REAL desktop install: first-run seeds agent-builder with owner=platform, and no amount
    of being the machine's owner makes our agent yours to publish."""
    registry, shared = _desktop_world(tmp_path, acct=None)
    _record(shared / "game-master", owner="platform", origin="curated")
    registry.refresh()
    assert not registry.owns("game-master")
    assert registry.origin_of("game-master") == "curated"


def test_an_authored_record_travels_with_its_account(world):
    """acct_a's record in acct_a's overlay: theirs; the same id means nothing to acct_b."""
    d = world.install("acct_a", "alpha")
    _record(d, owner="acct_a")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert world.registry.owns("alpha")
    world.current["acct"] = "acct_b"
    assert not world.registry.owns("alpha"), "b cannot even resolve it"


def test_an_installed_record_is_owned_but_not_authored(world):
    d = world.install("acct_a", "weather")
    _record(d, owner="acct_a", origin="installed")
    world.registry.refresh()
    world.current["acct"] = "acct_a"
    assert world.registry.owns("weather"), "theirs to run and uninstall"
    assert world.registry.origin_of("weather") == "installed", "but not theirs to republish"


def test_create_stamps_the_record_at_birth(world):
    from agent_runtime.infrastructure.agents import ownership_store

    world.current["acct"] = "acct_a"
    world.registry.create("newborn", name="Newborn")
    record = ownership_store.read(
        user_state.account_agents_dir(world.state, "acct_a") / "newborn"
    )
    assert (record.owner, record.origin) == ("acct_a", "authored")


# ─────────────────────────── hosted is default-private (Step 3) ───────────────────────────
#
# The shared layer stops being "whatever sits in the directory, shown to everyone". An account
# sees a shared agent only when it is the PLATFORM's (curated — which record-less hosted dirs
# are presumed to be, so nothing legitimate disappears) or their own. Anything else — a migrated
# desktop dir, another account's stray copy — is INVISIBLE, not refused: no roster entry, no
# session key, no app served. The operator (no account) still sees everything.


def test_a_migrated_desktop_dir_is_invisible_to_accounts(world):
    """A dir stamped owner=local lands on hosted EFS (backup restore, migration): no stranger
    may even know it exists — but the operator sees it, because losing sight of files on your
    own disk is how they never get cleaned up."""
    _record(world.shared / "curated", owner="local")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert "curated" not in world.registry.list_ids()
    assert world.registry.resolve_dir("curated") is None
    with pytest.raises(KeyError):
        world.registry.get("curated")

    world.current["acct"] = None  # the operator
    assert "curated" in world.registry.list_ids()


def test_a_shared_dir_owned_by_an_account_is_visible_only_to_it(world):
    _record(world.shared / "curated", owner="acct_a")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert "curated" in world.registry.list_ids()
    world.current["acct"] = "acct_b"
    assert "curated" not in world.registry.list_ids()


def test_recordless_shared_agents_stay_visible_to_everyone(world):
    """The back-compat half: today's hosted EFS has no records, and hiding it all would turn
    the deployment's real catalogue into an empty store. Presumed platform = still curated."""
    world.current["acct"] = "acct_a"
    assert set(world.registry.list_ids()) == {"curated", "main"}


def test_desktop_never_filters(tmp_path):
    """One person, one machine: even a record another identity stamped stays VISIBLE on a
    desktop — ownership there gates publishing, never sight."""
    registry, shared = _desktop_world(tmp_path, acct="acct_a")
    _record(shared / "game-master", owner="acct_b")
    registry.refresh()
    assert "game-master" in registry.list_ids()
    assert not registry.owns("game-master"), "visible, but acct_b's record still holds"


def test_a_web_app_copy_is_resolvable_but_listed_for_nobody(world):
    """The platform HOSTING a copy (so /apps/<id> works) is not the platform ENDORSING it.
    Every resolution path still finds it — the app must serve — but no account's sidebar
    carries it uninvited. The operator still lists it: it is the platform's own copy."""
    _record(world.shared / "curated", owner="platform", origin="web-app")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert world.registry.get("curated").id == "curated", "still resolvable — the app serves"
    assert world.registry.resolve_dir("curated") is not None
    assert not world.registry.listed("curated"), "but in nobody's sidebar"

    world.current["acct"] = None  # the operator owns the platform's copies
    assert world.registry.listed("curated")


def test_installing_a_web_app_from_the_store_lists_it_for_that_account(world):
    """The overlay copy is an ordinary install — owner = the account — and overlay shadows
    shared, so the same id flips from unlisted to listed for exactly one person."""
    _record(world.shared / "curated", owner="platform", origin="web-app")
    d = world.install("acct_a", "curated", "My Copy")
    _record(d, owner="acct_a", origin="installed")
    world.registry.refresh()

    world.current["acct"] = "acct_a"
    assert world.registry.listed("curated")
    world.current["acct"] = "acct_b"
    assert not world.registry.listed("curated")


def test_agents_list_omits_unlisted_web_apps(world):
    from agent_runtime.presentation.gateway import Gateway

    _record(world.shared / "curated", owner="platform", origin="web-app")
    world.registry.refresh()
    world.current["acct"] = "acct_a"

    cfg = SimpleNamespace(agent_id="main", agent_name="Assistant", state_dir=world.state)
    rows = Gateway(config=cfg, service=None, registry=world.registry)._agents_list()["agents"]
    assert "curated" not in {a["id"] for a in rows}


def test_agents_list_carries_mine_so_clients_stop_guessing(world):
    """The UI renders ownership from data. Before this field every client derived its own answer,
    and the derivations disagreed — the sidebar said an agent existed while publish said not."""
    from agent_runtime.presentation.gateway import Gateway

    world.install("acct_a", "alpha")
    world.registry.refresh()
    world.current["acct"] = "acct_a"

    cfg = SimpleNamespace(agent_id="main", agent_name="Assistant", state_dir=world.state)
    rows = {a["id"]: a for a in Gateway(config=cfg, service=None, registry=world.registry)._agents_list()["agents"]}
    assert rows["alpha"]["mine"] is True
    assert rows["curated"]["mine"] is False

    world.current["acct"] = None  # signed out: one layer, everything is the caller's
    rows = {a["id"]: a for a in Gateway(config=cfg, service=None, registry=world.registry)._agents_list()["agents"]}
    assert rows["curated"]["mine"] is True


def test_purging_an_uninstall_hits_the_accounts_own_data(tmp_path):
    """`uninstall --purge` deletes ``<installer state_dir>/agents/<id>``.

    The gateway composes an account's marketplace with ``state_dir = account_root``, so that
    expression has to land on exactly the directory holding THAT account's transcripts and
    workspace. If the two ever drift, purge silently deletes nothing and the user's data survives
    a deletion they asked for — which is why this is asserted as a path identity rather than
    trusted to stay true.
    """
    account_root = user_state.account_root(tmp_path, "acct_a")
    purge_target = account_root / "agents" / "alpha"
    assert purge_target == user_state.account_agent_dir(tmp_path, "acct_a", "alpha")
