"""Org tenancy at the daemon (plan E3) — identity widening, layout, fences, layers.

THE ONE RULE UNDER TEST, four ways: an org exists for a caller exactly when their VERIFIED
token says so, and for nobody else. The identity set (ownership.callers) widens only from that
claim; the filesystem mapper never mints an org under accounts/; the tenant scope grants org
definitions to members and nothing to strangers; the registry's org layer resolves for members
and does not exist — not "is refused", does not exist — for anyone else.

The refusal tests matter most, as everywhere in tenancy: the failure mode of every one of these
is silent cross-enterprise visibility, which is the bug class the whole plan is built against.
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain import ownership
from agent_runtime.infrastructure import user_state
from agent_runtime.infrastructure.agents.file_registry import FileAgentRegistry

ORG = "org_kajima"
OTHER_ORG = "org_rival"


# ── ownership.callers: the ONE identity-set producer ───────────────────────────────────


def test_a_hosted_member_acts_as_their_account_and_their_orgs():
    assert ownership.callers("acct_a", True, (ORG,)) == frozenset({"acct_a", ORG})


def test_orgs_ride_the_account_never_the_anonymous_caller():
    """No account, no orgs — an org id without a person behind it is nobody. The machine-token
    operator keeps exactly the platform identity it always had."""
    assert ownership.callers(None, True, (ORG,)) == frozenset({ownership.PLATFORM_OWNER})
    assert ownership.callers("", True, (ORG,)) == frozenset({ownership.PLATFORM_OWNER})


def test_desktop_keeps_local_alongside_account_and_orgs():
    got = ownership.callers("acct_a", False, (ORG,))
    assert got == frozenset({"acct_a", "local", ORG})
    # signing in never subtracts: the no-account desktop caller is unchanged
    assert ownership.callers(None, False, (ORG,)) == frozenset({"local"})


def test_only_org_prefixed_ids_can_widen_the_set():
    """A poisoned claim naming another ACCOUNT must not become an identity. The prefix is the
    namespace, so anything that is not org_* is dropped, not trusted."""
    got = ownership.callers("acct_a", True, ("acct_victim", "platform", "local", ORG))
    assert got == frozenset({"acct_a", ORG})


def test_may_observe_is_unchanged_by_org_identities():
    """Sessions stay ACCOUNT-owned (the Code Interpreter granularity lesson): two members of
    one org still cannot observe each other's chats, because org ids never appear as session
    owners — only as agent owners."""
    member_x = ownership.callers("acct_x", True, (ORG,))
    assert not ownership.may_observe("acct_y", member_x)
    assert ownership.may_observe("acct_x", member_x)


# ── user_state: the layout authority ───────────────────────────────────────────────────


def test_identity_root_maps_each_namespace_to_its_own_tree(tmp_path):
    assert user_state.identity_root(tmp_path, "acct_a") == tmp_path / "accounts" / "acct_a"
    assert user_state.identity_root(tmp_path, ORG) == tmp_path / "orgs" / ORG
    # THE conflation bug this mapper exists to prevent: an org id must never resolve under
    # accounts/ (where _file_roots_for would have silently minted it).
    assert "accounts" not in user_state.identity_root(tmp_path, ORG).parts


def _hosted_config(tmp_path) -> SimpleNamespace:
    return SimpleNamespace(
        hosted=True,
        state_dir=tmp_path / "state",
        agents_dir=str(tmp_path / "agents"),
        plugins_dir=str(tmp_path / "plugins"),
        builtin_plugins_dir="",
        hosted_read_roots=[],
    )


def _org_agent(tmp_path, org_id=ORG, agent_id="kajima-helper") -> Path:
    d = user_state.org_agents_dir(tmp_path / "state", org_id) / agent_id
    (d / "skills").mkdir(parents=True)
    (d / "workspace").mkdir()  # should never exist in practice; the fence must still hold
    (d / "agent.toml").write_text('name = "Kajima Helper"', encoding="utf-8")
    return d


def test_members_read_their_orgs_definitions_and_only_definitions(tmp_path):
    cfg = _hosted_config(tmp_path)
    org_agent = _org_agent(tmp_path)
    reads, clamp = user_state.tenant_scope(cfg, "acct_a", None, "", org_ids=(ORG,))
    assert str(org_agent / "agent.toml") in reads
    assert str(org_agent / "skills") in reads
    # the definition VIEW, not the folder: user-data subtrees are not granted even if present
    assert str(org_agent / "workspace") not in reads
    assert str(org_agent) not in reads
    # READ-only: the write clamp never widens to the org tree
    assert all(not c.startswith(str(tmp_path / "state" / "orgs")) for c in clamp)


def test_a_non_member_resolves_nothing_under_any_org_root(tmp_path):
    """The fence test the plan names: a connection without org A in its scope resolves nothing
    under org A's root — absent from the grant, not refused by a check."""
    cfg = _hosted_config(tmp_path)
    _org_agent(tmp_path)
    reads, _ = user_state.tenant_scope(cfg, "acct_b", None, "", org_ids=())
    orgs_root = str(tmp_path / "state" / "orgs")
    assert all(not r.startswith(orgs_root) for r in reads)


def test_membership_in_one_org_grants_nothing_of_another(tmp_path):
    cfg = _hosted_config(tmp_path)
    _org_agent(tmp_path, ORG)
    rival = _org_agent(tmp_path, OTHER_ORG, "rival-helper")
    reads, _ = user_state.tenant_scope(cfg, "acct_a", None, "", org_ids=(ORG,))
    assert all(not r.startswith(str(rival.parent.parent)) for r in reads)


def test_desktop_stays_unrestricted_whatever_orgs_say(tmp_path):
    cfg = _hosted_config(tmp_path)
    cfg.hosted = False
    assert user_state.tenant_scope(cfg, "acct_a", None, "", org_ids=(ORG,)) == ((), ())


# ── FileAgentRegistry: the ordered layers (curated < org < personal) ───────────────────


def _write_agent(root: Path, agent_id: str, name: str) -> Path:
    d = root / agent_id
    d.mkdir(parents=True, exist_ok=True)
    (d / "agent.toml").write_text(f'name = "{name}"', encoding="utf-8")
    return d


def _registry(tmp_path, acct=None, org_layers=()):
    cfg = SimpleNamespace(
        hosted=True,
        state_dir=tmp_path / "state",
        agents_dir=str(tmp_path / "agents"),
        agent_name="assistant",
    )
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    overlay = (
        user_state.account_agents_dir(cfg.state_dir, acct) if acct else None
    )
    return FileAgentRegistry(
        cfg,
        overlay_dir=(lambda: overlay) if acct else None,
        account_id=lambda: acct,
        org_layers=lambda: tuple(org_layers),
    )


def test_a_member_sees_the_org_agent_and_a_stranger_does_not(tmp_path):
    Path(tmp_path / "agents").mkdir()
    org_dir = user_state.org_agents_dir(tmp_path / "state", ORG)
    _write_agent(org_dir, "kajima-helper", "Kajima Helper")

    member = _registry(tmp_path, "acct_a", [(ORG, "member", org_dir)])
    assert "kajima-helper" in member.list_ids()
    assert member.get("kajima-helper").owner == ORG

    stranger = _registry(tmp_path, "acct_b", [])
    assert "kajima-helper" not in stranger.list_ids()


def test_membership_in_one_org_lists_nothing_of_another(tmp_path):
    Path(tmp_path / "agents").mkdir()
    _write_agent(user_state.org_agents_dir(tmp_path / "state", ORG), "kajima-helper", "K")
    rival_dir = user_state.org_agents_dir(tmp_path / "state", OTHER_ORG)
    _write_agent(rival_dir, "rival-helper", "R")

    member = _registry(
        tmp_path, "acct_a",
        [(ORG, "member", user_state.org_agents_dir(tmp_path / "state", ORG))],
    )
    ids = member.list_ids()
    assert "kajima-helper" in ids and "rival-helper" not in ids


def test_the_personal_copy_wins_an_id_collision(tmp_path):
    """curated < org < personal: installing your own build of the company agent gets you yours
    without touching anybody else's."""
    Path(tmp_path / "agents").mkdir()
    org_dir = user_state.org_agents_dir(tmp_path / "state", ORG)
    _write_agent(org_dir, "kajima-helper", "Org Build")
    overlay = user_state.account_agents_dir(tmp_path / "state", "acct_a")
    _write_agent(overlay, "kajima-helper", "My Build")

    member = _registry(tmp_path, "acct_a", [(ORG, "member", org_dir)])
    assert member.get("kajima-helper").name == "My Build"


def test_an_org_agent_is_owned_by_admins_not_members(tmp_path):
    """Every member SEES it; only admins/owners may change it — `mine` gates share/unshare,
    and a member editing the whole company's agent is the granularity bug the plan names."""
    Path(tmp_path / "agents").mkdir()
    org_dir = user_state.org_agents_dir(tmp_path / "state", ORG)
    _write_agent(org_dir, "kajima-helper", "K")

    plain = _registry(tmp_path, "acct_a", [(ORG, "member", org_dir)])
    assert plain.owns("kajima-helper") is False
    admin = _registry(tmp_path, "acct_b", [(ORG, "admin", org_dir)])
    assert admin.owns("kajima-helper") is True


def test_a_broken_org_resolver_degrades_to_no_org_layer(tmp_path):
    """Same rule as the overlay resolver: a resolver that raises costs the caller their org
    agents for the call — never the whole catalogue, and never someone else's layer."""
    Path(tmp_path / "agents").mkdir()
    _write_agent(Path(tmp_path / "agents"), "shared-helper", "S")

    def boom():
        raise RuntimeError("resolver died")

    cfg = SimpleNamespace(
        hosted=True,
        state_dir=tmp_path / "state",
        agents_dir=str(tmp_path / "agents"),
        agent_name="assistant",
    )
    reg = FileAgentRegistry(cfg, account_id=lambda: "acct_a", org_layers=boom)
    assert "shared-helper" in reg.list_ids()


# ── RunContext attribution (E2's daemon half) ──────────────────────────────────────────


def test_only_an_org_owned_agent_attributes_the_turn_to_an_org():
    from agent_runtime.application.run_context import RunContext

    assert RunContext(agent_id="a", session_key="s", mode="interactive").org_id == ""
    ctx = RunContext(agent_id="a", session_key="s", mode="interactive", org_id=ORG)
    assert ctx.org_id == ORG


def test_is_org_is_the_namespace_test():
    assert ownership.is_org(ORG)
    assert not ownership.is_org("acct_a")
    assert not ownership.is_org("platform")
    assert not ownership.is_org("")


# ── agents.shareToOrg / unshareFromOrg: the Kajima move ────────────────────────────────


def _share_gateway(tmp_path, acct, orgs):
    """A minimal Gateway + registry wired the way _handle_conn wires a member's connection."""
    import asyncio

    from agent_runtime.infrastructure import accounts
    from agent_runtime.presentation.gateway import Gateway

    cfg = SimpleNamespace(
        hosted=True,
        state_dir=tmp_path / "state",
        agents_dir=str(tmp_path / "agents"),
        agent_name="assistant",
        accounts={},
        distribution=None,
        model_proxy={},
        model_gateway={},
    )
    Path(cfg.agents_dir).mkdir(parents=True, exist_ok=True)
    overlay = user_state.account_agents_dir(cfg.state_dir, acct)
    reg = FileAgentRegistry(
        cfg,
        overlay_dir=lambda: overlay,
        account_id=lambda: acct,
        org_layers=lambda: tuple(
            (o["id"], o["role"], user_state.org_agents_dir(cfg.state_dir, o["id"]))
            for o in orgs
        ),
    )
    gw = Gateway(config=cfg, service=None, registry=reg)
    gw._agents_list = lambda: {"agents": []}  # the broadcast payload is not under test

    def run(coro):
        token = accounts.set_account({"account_id": acct, "orgs": orgs})
        try:
            return asyncio.run(coro)
        finally:
            accounts.reset_account(token)

    return gw, run, cfg


def _author_agent(tmp_path, acct, agent_id="kajima-helper"):
    d = user_state.account_agents_dir(tmp_path / "state", acct) / agent_id
    (d / "skills").mkdir(parents=True)
    (d / "workspace").mkdir()
    (d / "sessions").mkdir()
    (d / "agent.toml").write_text('name = "Kajima Helper"', encoding="utf-8")
    (d / "sessions" / "secret.jsonl").write_text("{}", encoding="utf-8")
    return d


def test_share_copies_the_definition_and_never_the_data(tmp_path):
    gw, run, cfg = _share_gateway(tmp_path, "acct_a", [{"id": ORG, "role": "admin"}])
    original = _author_agent(tmp_path, "acct_a")
    out = run(gw._agents_share_to_org({"agentId": "kajima-helper", "orgId": ORG}))
    assert out["shared"] is True

    installed = user_state.org_agents_dir(cfg.state_dir, ORG) / "kajima-helper"
    assert (installed / "agent.toml").is_file()
    assert (installed / "skills").is_dir()
    # THE POINT: the author's chats/files never enter the whole company's read scope. (The
    # registry's loader mints an EMPTY workspace/ on scan, as it does for curated agents —
    # what must never cross is content, and members' runs write under their own accounts.)
    assert not (installed / "sessions").exists()
    if (installed / "workspace").exists():
        assert list((installed / "workspace").iterdir()) == []
    # provenance: the org's copy is the ORG's, an install, traced to its source
    record = json.loads((installed / ".agentd-meta.json").read_text("utf-8"))
    assert record["owner"] == ORG and record["origin"] == "installed"
    # and the author's original is untouched
    assert (original / "sessions" / "secret.jsonl").is_file()


def test_a_plain_member_may_not_share_or_unshare(tmp_path):
    gw, run, _cfg = _share_gateway(tmp_path, "acct_a", [{"id": ORG, "role": "member"}])
    _author_agent(tmp_path, "acct_a")
    out = run(gw._agents_share_to_org({"agentId": "kajima-helper", "orgId": ORG}))
    assert out["shared"] is False and "admin" in out["error"]
    out = run(gw._agents_unshare_from_org({"agentId": "kajima-helper", "orgId": ORG}))
    assert out["removed"] is False and "admin" in out["error"]


def test_an_admin_of_another_org_cannot_share_into_this_one(tmp_path):
    """The org id comes from a frame; the ROLE comes from the token. Admin of Rival, member of
    nothing else — Kajima must refuse."""
    gw, run, _cfg = _share_gateway(tmp_path, "acct_a", [{"id": OTHER_ORG, "role": "admin"}])
    _author_agent(tmp_path, "acct_a")
    out = run(gw._agents_share_to_org({"agentId": "kajima-helper", "orgId": ORG}))
    assert out["shared"] is False


def test_unshare_removes_the_org_copy_and_leaves_the_original(tmp_path):
    gw, run, cfg = _share_gateway(tmp_path, "acct_a", [{"id": ORG, "role": "owner"}])
    original = _author_agent(tmp_path, "acct_a")
    assert run(gw._agents_share_to_org({"agentId": "kajima-helper", "orgId": ORG}))["shared"]
    out = run(gw._agents_unshare_from_org({"agentId": "kajima-helper", "orgId": ORG}))
    assert out["removed"] is True
    assert not (user_state.org_agents_dir(cfg.state_dir, ORG) / "kajima-helper").exists()
    assert (original / "agent.toml").is_file()
    assert (original / "sessions" / "secret.jsonl").is_file()


def test_nobody_shares_an_agent_that_is_not_theirs(tmp_path):
    """Org admin or not, the SOURCE agent must be the caller's own (`mine`) — sharing the
    platform's curated agent would copy code the caller does not own into their org."""
    gw, run, cfg = _share_gateway(tmp_path, "acct_a", [{"id": ORG, "role": "admin"}])
    curated = Path(cfg.agents_dir) / "platform-helper"
    curated.mkdir(parents=True)
    (curated / "agent.toml").write_text('name = "Platform Helper"', encoding="utf-8")
    gw.registry.refresh()
    out = run(gw._agents_share_to_org({"agentId": "platform-helper", "orgId": ORG}))
    assert out["shared"] is False and "not yours" in out["error"]


# ── the identity ledger's step 2 (the portable migration) ──────────────────────────────


def _identity_db():
    import sqlite3

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("CREATE TABLE accounts (id TEXT PRIMARY KEY)")  # the FK target
    return conn


def test_a_fresh_database_lands_on_version_2_with_the_org_tables():
    from identity.infrastructure.sqlite_schema import SCHEMA_VERSION, create_schema

    conn = _identity_db()
    assert create_schema(conn) == SCHEMA_VERSION == 2
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"orgs", "org_members", "org_domains", "org_invites"} <= tables


def test_a_version_1_database_upgrades_in_place():
    """The versioned-steps ledger applying forward — the whole reason these tables live in
    identity's file and not behind a PRAGMA probe: this exact motion is what ports to
    Postgres mechanically."""
    from identity.infrastructure import sqlite_schema

    conn = _identity_db()
    # Build a REAL v1 database: apply only step 1 and stamp version 1, the way any deployed
    # database looked the day before this change shipped.
    conn.execute(
        "CREATE TABLE identity_schema_version (id INTEGER PRIMARY KEY CHECK (id = 1), "
        "version INTEGER NOT NULL)"
    )
    conn.executescript(sqlite_schema._STEPS[1])
    conn.execute("INSERT INTO identity_schema_version (id, version) VALUES (1, 1)")

    assert sqlite_schema.create_schema(conn) == 2
    tables = {
        r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert {"orgs", "org_members", "identities", "refresh_tokens"} <= tables
    # idempotent: a second boot is a no-op, never a crash
    assert sqlite_schema.create_schema(conn) == 2
