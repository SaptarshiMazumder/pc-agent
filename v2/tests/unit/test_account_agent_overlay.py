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
    )
    registry = FileAgentRegistry(config, overlay_dir=overlay)
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
