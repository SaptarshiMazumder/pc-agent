"""Tenant isolation at the daemon's two egress doors — live events and GET /file.

THE TWO LEAKS THIS PINS CLOSED (found live on the hosted web deployment, 2026-08):

  #1  _send_all fanned every chat.event to every socket, filtered by AGENT scope only —
      user X, connected to the same web agent as user Y, watched Y's run live: messages,
      deltas, tool results, and the absolute paths of Y's artifacts. An UNSCOPED
      authenticated socket received every account's events with no filter at all.
  #2  GET /file took an absolute path and served anything under the global roots — and
      state_dir is a global root, which contains EVERY account's subtree. Y's workspace
      and transcripts were fetchable by URL.

THE FIX IS ONE RULE, NOT TWO MODES. Every connection authenticates to a set of IDENTITIES
(ownership.callers — resolved once at _handle_conn); everything leaving the daemon has an
OWNER (the account acting in the emission context, else the deployment's own identity via
stamp_owner); egress is ownership.may_observe(owner, identities) at exactly two choke
points. Deployment shape changes the VALUES flowing through the test, never the code path:
on a desktop both sides are "local" and the test is always true (today's behaviour,
byte-for-byte); on a hosted daemon strangers only ever match themselves.
"""

import asyncio
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from agent_runtime.domain import ownership
from agent_runtime.domain.events import AgentEvent
from agent_runtime.infrastructure import accounts
from agent_runtime.presentation.gateway import Gateway
from agent_runtime.presentation.protocol import Event, dump_frame


class _Sock:
    def __init__(self):
        self.sent = []

    async def send(self, frame):
        self.sent.append(frame)


def _cfg(**kw):
    class _C:
        accounts = {}
        distribution = None
        state_dir = "."
        model_proxy = {}
        model_gateway = {}

    c = _C()
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def _gateway(**kw):
    return Gateway(config=_cfg(**kw), service=None)


def _wire(gw, identities, scope=None):
    """Register a socket the way _handle_conn does: clients + identities (+ scope)."""
    ws = _Sock()
    gw.clients.add(ws)
    gw.client_identities[ws] = identities
    if scope:
        gw.client_scopes[ws] = scope
    return ws


def _frame(agent_id="bedtime-kids"):
    return dump_frame(Event(event="chat.event", payload={"agentId": agent_id, "event": {}}))


def _send(gw, frame, account=None):
    """Emit one frame the way real emitters do: inside the actor's account context."""

    async def go():
        token = accounts.set_account(account)
        try:
            await gw._send_all(frame)
        finally:
            accounts.reset_account(token)

    asyncio.run(go())


# ── the rule itself ─────────────────────────────────────────────────────────
def test_the_machines_human_observes_everything_on_their_machine():
    """The "local" clause is what keeps desktop behaviour byte-for-byte without a mode
    branch: callers() never grants "local" to a hosted stranger, so the clause is inert
    exactly where it must be."""
    assert ownership.may_observe("acct_x", frozenset({"local"}))
    assert ownership.may_observe("platform", frozenset({"local"}))
    assert ownership.may_observe("acct_x", frozenset({"platform"}))  # hosted operator


def test_a_hosted_stranger_matches_only_themselves():
    assert ownership.may_observe("acct_x", frozenset({"acct_x"}))
    assert not ownership.may_observe("acct_y", frozenset({"acct_x"}))
    assert not ownership.may_observe("platform", frozenset({"acct_x"}))


def test_an_anonymous_caller_observes_nothing_owned():
    assert not ownership.may_observe("acct_x", frozenset())
    assert not ownership.may_observe("local", frozenset())
    assert not ownership.may_observe("", frozenset({"acct_x"}))  # ownerless: fail closed


# ── leak #1: live event fan-out ─────────────────────────────────────────────
def test_leak1_scope_alone_no_longer_admits_a_stranger():
    """THE observed leak: X and Y on the same web agent, both scoped to it — the agent
    filter passed each other's frames. The tenant test now drops them first."""
    gw = _gateway(hosted=True)
    x = _wire(gw, frozenset({"acct_x"}), scope="bedtime-kids")
    y = _wire(gw, frozenset({"acct_y"}), scope="bedtime-kids")
    _send(gw, _frame("bedtime-kids"), account={"account_id": "acct_x"})
    assert len(x.sent) == 1
    assert y.sent == []


def test_leak1_an_unscoped_host_connection_is_not_omniscient():
    """The worse half of the leak: an authenticated socket with NO agent scope used to
    receive every account's events on the whole daemon."""
    gw = _gateway(hosted=True)
    y_host = _wire(gw, frozenset({"acct_y"}))
    anon = _wire(gw, frozenset(), scope="bedtime-kids")  # public visitor, right agent
    _send(gw, _frame("bedtime-kids"), account={"account_id": "acct_x"})
    assert y_host.sent == []
    assert anon.sent == []


def test_deployment_owned_frames_reach_only_the_deployment_owner():
    """A hosted emission with NO account (boot seed, system cron) belongs to the platform:
    tenants never see the platform's own runs — and vice versa is already covered above."""
    gw = _gateway(hosted=True)
    x = _wire(gw, frozenset({"acct_x"}))
    operator = _wire(gw, frozenset({"platform"}))
    _send(gw, _frame(), account=None)
    assert x.sent == []
    assert len(operator.sent) == 1


def test_a_socket_the_identity_map_does_not_know_receives_nothing_owned():
    """Fail closed: an unregistered socket (a bug, a test artifact) is not a back door."""
    gw = _gateway(hosted=True)
    ghost = _Sock()
    gw.clients.add(ghost)
    _send(gw, _frame(), account={"account_id": "acct_x"})
    assert ghost.sent == []


def test_desktop_every_window_of_the_machines_human_still_sees_everything():
    """Local mode, byte-for-byte: a signed-out window (terminal), and a signed-in window
    ({local, account} — sign-in ADDS an identity) both see every run on their machine,
    whether the run was started signed-out (owner "local") or signed-in (owner account)."""
    gw = _gateway()  # hosted absent -> the one-tenant case
    signed_out = _wire(gw, frozenset({"local"}))
    signed_in = _wire(gw, frozenset({"local", "acct_a"}))
    _send(gw, _frame(), account=None)
    _send(gw, _frame(), account={"account_id": "acct_a"})
    assert len(signed_out.sent) == 2
    assert len(signed_in.sent) == 2


def test_the_agent_scope_filter_still_applies_within_a_tenant():
    """Isolation composes with — never replaces — the existing scoped-app filter."""
    gw = _gateway(hosted=True)
    app = _wire(gw, frozenset({"acct_x"}), scope="other-agent")
    _send(gw, _frame("bedtime-kids"), account={"account_id": "acct_x"})
    assert app.sent == []


def test_chat_events_and_broadcast_still_carry_the_agent_id():
    """_broadcast is untouched wiring: same frame shape, delivery decided in _send_all."""
    import json

    gw = _gateway()
    ws = _wire(gw, frozenset({"local"}))

    async def go():
        token = accounts.set_account(None)
        try:
            await gw._broadcast("agent:demo:app", "r1", AgentEvent("turn_start", {}), "demo")
        finally:
            accounts.reset_account(token)

    asyncio.run(go())
    assert json.loads(ws.sent[0])["payload"]["agentId"] == "demo"


# ── leak #2: GET /file ──────────────────────────────────────────────────────
def _serve(gw, path, query=""):
    split = urlsplit(f"/file?path={quote(str(path))}" + (f"&{query}" if query else ""))
    return asyncio.run(gw._serve_file(split, {}))


@pytest.fixture
def hosted_world(tmp_path, monkeypatch):
    """A hosted daemon with two tenants' files on one filesystem, sign-in enforced."""
    state = tmp_path / "state"
    mine = state / "accounts" / "acct_x" / "agents" / "bedtime-kids" / "workspace" / "mine.txt"
    theirs = state / "accounts" / "acct_y" / "agents" / "bedtime-kids" / "workspace" / "theirs.txt"
    for f in (mine, theirs):
        f.parent.mkdir(parents=True)
        f.write_text("data")

    async def fake_resolve(token):
        return {"account_id": "acct_x"} if token == "sess_x" else None

    monkeypatch.setattr(accounts, "resolve", fake_resolve)
    monkeypatch.setattr(accounts, "enabled", lambda: True)
    gw = _gateway(
        hosted=True,
        state_dir=str(state),
        workspace=str(tmp_path / "shared_ws"),
        agents_dir=str(tmp_path / "agents"),
    )
    return gw, mine, theirs


def test_leak2_no_credential_no_file(hosted_world):
    gw, mine, _ = hosted_world
    assert _serve(gw, mine).status_code == 401


def test_leak2_the_machine_token_does_not_bypass_signin(hosted_world):
    """Same rule as the WS gate: where sign-in is enforced, only a session identifies."""
    gw, mine, _ = hosted_world
    gw.auth_token = "machine-secret"
    assert _serve(gw, mine, "token=machine-secret").status_code == 401


def test_leak2_an_account_reads_its_own_files(hosted_world):
    gw, mine, _ = hosted_world
    resp = _serve(gw, mine, "token=sess_x")
    assert resp.status_code == 200
    assert resp.body == b"data"


def test_leak2_an_account_cannot_read_another_tenants_files(hosted_world):
    """THE hole: this exact request served Y's file to X before the fix (it is under
    state_dir, which was a global root). Now Y's subtree is outside X's roots entirely."""
    gw, _, theirs = hosted_world
    assert _serve(gw, theirs, "token=sess_x").status_code == 404


def test_leak2_an_account_cannot_read_the_shared_workspace(hosted_world):
    """The deployment's own scratch belongs to the deployment, not to any tenant."""
    gw, _, _ = hosted_world
    shared = Path(gw.config.workspace) / "ops.txt"
    shared.parent.mkdir(parents=True, exist_ok=True)
    shared.write_text("operator data")
    assert _serve(gw, shared, "token=sess_x").status_code == 404


def test_desktop_file_serving_is_unchanged(tmp_path):
    """Local mode: no accounts, machine auth off — the whole single-user world serves,
    exactly as before the fix (the deployment-owner identity keeps the legacy roots)."""
    state = tmp_path / "state"
    f = state / "sessions" / "chat.jsonl"
    f.parent.mkdir(parents=True)
    f.write_text("{}")
    gw = _gateway(state_dir=str(state))
    resp = _serve(gw, f)
    assert resp.status_code == 200


def test_the_deployment_owner_keeps_the_legacy_roots(tmp_path):
    gw = _gateway(state_dir=str(tmp_path))
    assert gw._file_roots_for(frozenset({"local"})) == gw._allowed_file_roots()
    assert gw._file_roots_for(frozenset({"platform"})) == gw._allowed_file_roots()


def test_an_accounts_roots_are_its_subtree(tmp_path):
    gw = _gateway(hosted=True, state_dir=str(tmp_path))
    roots = gw._file_roots_for(frozenset({"acct_x"}))
    assert (Path(tmp_path) / "accounts" / "acct_x").resolve() in roots
    assert all("acct_y" not in str(r) for r in roots)
