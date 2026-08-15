"""`GET /restart` — one endpoint, any caller, kill by pid and bring a fresh one up.

GET, because this is the WebSocket server's port and `websockets` rejects every other method
while parsing the request line — before the daemon's own handler is reached. A POST here does not
404, it drops the connection, which surfaces as a bare "Failed to fetch" with nothing to debug.
The token is what makes it safe, not the verb.

There used to be a mechanism per caller: the desktop shell had its supervisor, and an app window
(which is given no host bridge on purpose) would have needed a socket RPC of its own. Two
implementations of one sentence. This is the sentence.

The part that is easy to get wrong, and the reason for most of these tests: **nothing can restart
itself**. The process that starts the replacement has to already be running when the kill lands,
so the endpoint's whole job is to spawn that process and answer — never to do the work.
"""

from __future__ import annotations

import json

import pytest

from agent_runtime.presentation.gateway import Gateway


class FakeSplit:
    def __init__(self, query: str = ""):
        self.path = "/restart"
        self.query = query


@pytest.fixture
def gateway(monkeypatch):
    g = Gateway.__new__(Gateway)
    g.auth_token = ""
    monkeypatch.setattr("agent_runtime.presentation.gateway.accounts.enabled", lambda: False)
    return g


def _body(response) -> dict:
    return json.loads(response.body.decode("utf-8"))


def _identity(monkeypatch, allowed: bool):
    """Patched on the CLASS, so it takes `self` like the real method."""

    async def identities(_self, _q, _h):
        return frozenset({"someone"}) if allowed else None

    monkeypatch.setattr(Gateway, "_http_identities", identities, raising=False)


@pytest.mark.asyncio
async def test_it_spawns_the_respawner_and_does_not_kill_anything_itself(gateway, monkeypatch):
    """THE WHOLE DESIGN. If this handler killed the daemon, there would be nobody left to start
    the next one — the endpoint's job is to hand the work to a process that outlives it."""
    _identity(monkeypatch, True)
    spawned: list[bool] = []
    monkeypatch.setattr(
        "agent_runtime.presentation.gateway.lifecycle.spawn_respawner",
        lambda: spawned.append(True) or 4321,
    )

    response = await gateway._serve_restart(FakeSplit(), {})

    assert response.status_code == 200
    assert _body(response)["ok"] is True
    assert spawned == [True]


@pytest.mark.asyncio
async def test_a_signed_in_window_is_authorised_by_its_session(gateway, monkeypatch):
    """THE BUG THIS EXISTS FOR. A window that has signed in sends `?session=`, not the machine
    token — the SDK sends one or the other. Checking only for the token 401s exactly the window
    the button is on, and signing in is what causes it."""
    _identity(monkeypatch, True)
    monkeypatch.setattr(
        "agent_runtime.presentation.gateway.lifecycle.spawn_respawner", lambda: 1
    )

    response = await gateway._serve_restart(FakeSplit(query="session=sess_abc"), {})

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_an_unauthenticated_caller_is_refused(gateway, monkeypatch):
    """Same rule as /file and the socket — one door, three transports."""
    _identity(monkeypatch, False)
    monkeypatch.setattr(
        "agent_runtime.presentation.gateway.lifecycle.spawn_respawner",
        lambda: pytest.fail("restarted without a credential"),
    )

    response = await gateway._serve_restart(FakeSplit(query="token=wrong"), {})

    assert response.status_code == 401


@pytest.mark.asyncio
async def test_a_hosted_daemon_refuses(gateway, monkeypatch):
    """There it serves other people's sessions, and no window's convenience is worth ending
    them."""
    _identity(monkeypatch, True)
    monkeypatch.setattr("agent_runtime.presentation.gateway.accounts.enabled", lambda: True)
    monkeypatch.setattr(
        "agent_runtime.presentation.gateway.lifecycle.spawn_respawner",
        lambda: pytest.fail("restarted a multi-account daemon"),
    )

    response = await gateway._serve_restart(FakeSplit(), {})

    assert response.status_code == 403
    assert "sessions" in _body(response)["error"]


@pytest.mark.asyncio
async def test_a_failed_spawn_is_reported_not_swallowed(gateway, monkeypatch):
    """A 200 with no respawner is the worst outcome available: the caller waits for a daemon
    that is never coming, with nothing on screen to say why."""
    _identity(monkeypatch, True)

    def boom():
        raise OSError("no interpreter")

    monkeypatch.setattr("agent_runtime.presentation.gateway.lifecycle.spawn_respawner", boom)

    response = await gateway._serve_restart(FakeSplit(), {})

    assert response.status_code == 500
    assert "no interpreter" in _body(response)["error"]


def _fake_lifecycle(monkeypatch, respawn, *, stopped=True, port_open=False, calls=None):
    calls = calls if calls is not None else []
    monkeypatch.setattr(respawn.time, "sleep", lambda _s: None)
    monkeypatch.setattr(respawn.lifecycle, "read_gateway_file", lambda: None)
    monkeypatch.setattr(
        respawn.lifecycle,
        "stop_daemon",
        lambda **kw: calls.append(("stop", kw)) or stopped,
    )
    monkeypatch.setattr(respawn.lifecycle, "port_open", lambda *_a, **_k: port_open)
    monkeypatch.setattr(respawn.lifecycle, "clear_gateway_file", lambda: calls.append("clear"))
    monkeypatch.setattr(
        respawn.lifecycle,
        "spawn_daemon",
        lambda: calls.append("spawn")
        or type("I", (), {"host": "127.0.0.1", "port": 8765, "pid": 1})(),
    )
    return calls


def test_the_respawner_stops_before_it_starts(monkeypatch):
    """Order is the whole algorithm: kill, clear the rendezvous, then spawn. Spawning first hits
    'agentd is already running'; skipping the clear leaves a stale file that makes the NEXT
    daemon refuse to bind, turning a restart into an outage."""
    from agent_runtime import respawn

    calls = _fake_lifecycle(monkeypatch, respawn)

    assert respawn.main() == 0
    assert [c[0] if isinstance(c, tuple) else c for c in calls] == ["stop", "clear", "spawn"]


def test_the_respawner_does_not_kill_its_own_process_tree(monkeypatch):
    """THE BUG THIS EXISTS FOR. This process is a CHILD of the daemon it kills, so a tree-kill
    takes it out mid-flight: the daemon dies, the port closes, and nothing is left to start the
    replacement. It happened once — the endpoint answered 200 and the machine lost its daemon."""
    from agent_runtime import respawn

    calls = _fake_lifecycle(monkeypatch, respawn)
    respawn.main()

    stop = next(c for c in calls if isinstance(c, tuple) and c[0] == "stop")
    assert stop[1].get("kill_tree") is False


def test_the_respawner_refuses_to_start_a_second_daemon(monkeypatch):
    """If the old one would not die, starting another gives two daemons fighting over one port
    and one rendezvous file — worse than the wedged daemon we were asked to replace."""
    from agent_runtime import respawn

    _fake_lifecycle(monkeypatch, respawn, stopped=False)
    monkeypatch.setattr(
        respawn.lifecycle, "spawn_daemon", lambda: pytest.fail("spawned over a live daemon")
    )

    assert respawn.main() == 1


def test_it_will_not_spawn_onto_a_port_something_else_still_holds(monkeypatch):
    """The pid is gone but the socket is not: a lingering child, or TIME_WAIT. Spawning into
    that produces a bind error whose message blames the new daemon for the old one's socket."""
    from agent_runtime import respawn

    _fake_lifecycle(monkeypatch, respawn, port_open=True)
    monkeypatch.setattr(
        respawn.lifecycle,
        "read_gateway_file",
        lambda: type("I", (), {"host": "127.0.0.1", "port": 8765, "pid": 9})(),
    )
    monkeypatch.setattr(
        respawn.lifecycle, "spawn_daemon", lambda: pytest.fail("spawned onto a held port")
    )
    monkeypatch.setattr(respawn.time, "monotonic", _clock())

    assert respawn.main() == 1


def _clock():
    """A monotonic clock that runs fast, so the port-free wait does not really take 15 seconds."""
    ticks = iter(range(0, 10_000, 5))
    return lambda: float(next(ticks))
