"""Daemon lifecycle: rendezvous round-trip + spawn_daemon's concurrent-start handling.

The core invariant under test: a client that spawns a daemon must CONVERGE on whatever
daemon actually ends up serving — its own child OR a concurrent starter (a second
terminal, the desktop supervisor) that won the single-instance race — instead of
waiting out the timeout for its own child's pid (the bug that hung `agentd`)."""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd import lifecycle
from agentd.lifecycle import GatewayInfo


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENTD_HOME", str(tmp_path))
    # runtime_paths reads AGENTD_HOME live, so the rendezvous lands under tmp_path
    return tmp_path


def test_gateway_file_roundtrip(home):
    info = GatewayInfo(host="127.0.0.1", port=8787, pid=4321, token="tok", version="0.1.0")
    lifecycle.write_gateway_file(info)
    back = lifecycle.read_gateway_file()
    assert back == info
    assert back.connect_url() == "ws://127.0.0.1:8787/?token=tok"
    lifecycle.clear_gateway_file()
    assert lifecycle.read_gateway_file() is None


def test_clear_only_pid_keeps_successors_file(home):
    lifecycle.write_gateway_file(GatewayInfo("127.0.0.1", 8787, pid=222))
    lifecycle.clear_gateway_file(only_pid=111)     # not the owner -> keep it
    assert lifecycle.read_gateway_file() is not None
    lifecycle.clear_gateway_file(only_pid=222)     # the owner -> remove it
    assert lifecycle.read_gateway_file() is None


class _FakeProc:
    def __init__(self, pid, exit_code=None):
        self.pid = pid
        self.returncode = exit_code
        self._exit = exit_code

    def poll(self):
        return self._exit


def _stub_spawn(monkeypatch, child_pid, writes_pid=None, exit_code=None):
    """Stub Popen so 'spawning' optionally writes a rendezvous (a daemon binding), and
    port_open is always true. writes_pid simulates whoever won the bind (our child, or a
    concurrent starter) — written AS A SIDE EFFECT of spawn, i.e. AFTER spawn_daemon's
    own clear_gateway_file(), which is what makes it visible to the wait loop."""
    def fake_popen(*_a, **_k):
        if writes_pid is not None:
            lifecycle.write_gateway_file(GatewayInfo("127.0.0.1", 8787, pid=writes_pid, token="t"))
        return _FakeProc(child_pid, exit_code)

    monkeypatch.setattr(lifecycle.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(lifecycle, "port_open", lambda host, port, timeout=1.0: True)
    monkeypatch.setattr(lifecycle, "find_running", lambda: None)  # guard: proceed to spawn


def test_spawn_adopts_concurrent_winner(home, monkeypatch):
    # our child (pid 999) never binds; a CONCURRENT starter (pid 12345) wrote the
    # rendezvous. spawn_daemon must ADOPT it, not wait out the timeout for pid 999.
    _stub_spawn(monkeypatch, child_pid=999, writes_pid=12345)
    info = lifecycle.spawn_daemon(wait_sec=5)
    assert info.pid == 12345, "must adopt the daemon that actually bound"


def test_spawn_returns_own_child_when_it_binds(home, monkeypatch):
    _stub_spawn(monkeypatch, child_pid=555, writes_pid=555)   # our child binds
    info = lifecycle.spawn_daemon(wait_sec=5)
    assert info.pid == 555


def test_spawn_raises_when_child_dies_and_nobody_serves(home, monkeypatch):
    # child exits immediately, no rendezvous, no winner -> clear failure (not a hang)
    _stub_spawn(monkeypatch, child_pid=999, writes_pid=None, exit_code=1)
    monkeypatch.setattr(lifecycle, "port_open", lambda *a, **k: False)
    with pytest.raises(RuntimeError, match="exited immediately"):
        lifecycle.spawn_daemon(wait_sec=5)
