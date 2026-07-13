"""Daemon lifecycle — one running gateway per user, discoverable by every client.

The rendezvous is a single file, ``~/.agentd/gateway.json`` (both runtime modes):
the daemon writes ``{host, port, pid, token, version, started_at}`` when it binds
and removes it on clean exit; clients read it to find the URL AND the bearer token
(M2 auth). Everything here is stdlib-only and synchronous — it runs before any
event loop exists (CLI startup, desktop supervisor).

Spawning: ``ensure_running()`` starts ``<python> -m agentd`` DETACHED (survives the
parent terminal), logs to ``~/.agentd/logs/daemon.log``, and waits for a FRESH
rendezvous file + an accepting socket. Stale files (dead pid / closed port) are
cleaned up on read, so a crashed daemon never wedges the next start.
"""

from __future__ import annotations

import contextlib
import json
import os
import secrets
import socket
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from agentd import runtime_paths

_STILL_ACTIVE = 259  # Windows GetExitCodeProcess: process has not terminated


@dataclass(frozen=True)
class GatewayInfo:
    """What a client needs to reach the one running daemon."""

    host: str
    port: int
    pid: int
    token: str = ""  # "" => auth disabled on that daemon
    version: str = ""
    started_at: str = ""

    @property
    def ws_url(self) -> str:
        return f"ws://{self.host}:{self.port}"

    def connect_url(self) -> str:
        """The URL a client should actually dial (token carried as a query param —
        the one place browser-based clients can put it on a WebSocket)."""
        return f"{self.ws_url}/?token={self.token}" if self.token else self.ws_url


def mint_token() -> str:
    return secrets.token_urlsafe(32)


# ---- rendezvous file ---------------------------------------------------------------


def write_gateway_file(info: GatewayInfo) -> Path:
    path = runtime_paths.gateway_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(info), indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)
    with contextlib.suppress(OSError):  # best-effort 0600 (owner-only on POSIX)
        os.chmod(path, 0o600)
    return path


def read_gateway_file() -> GatewayInfo | None:
    path = runtime_paths.gateway_file()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return GatewayInfo(
            host=str(data.get("host") or "127.0.0.1"),
            port=int(data["port"]),
            pid=int(data.get("pid") or 0),
            token=str(data.get("token") or ""),
            version=str(data.get("version") or ""),
            started_at=str(data.get("started_at") or ""),
        )
    except (OSError, ValueError, KeyError):
        return None


def clear_gateway_file(only_pid: int | None = None) -> None:
    """Remove the rendezvous file. With ``only_pid``, remove it only if it still
    belongs to that pid — so a daemon shutting down late never deletes its successor's."""
    path = runtime_paths.gateway_file()
    if only_pid is not None:
        current = read_gateway_file()
        if current is not None and current.pid != only_pid:
            return
    with contextlib.suppress(OSError):
        path.unlink()


# ---- liveness ------------------------------------------------------------------------


def pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == _STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True


def port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def find_running() -> GatewayInfo | None:
    """The live daemon per the rendezvous file, or None. A stale file (dead pid or
    closed port) is removed so the next start is clean."""
    info = read_gateway_file()
    if info is None:
        return None
    if pid_alive(info.pid) and port_open(info.host, info.port):
        return info
    clear_gateway_file()
    return None


# ---- spawn / stop ----------------------------------------------------------------------


def daemon_command() -> list[str]:
    """How to launch the daemon. Override with AGENTD_DAEMON_CMD (the desktop shell
    sets this to its bundled runtime); default: this interpreter, `-m agentd`."""
    override = os.environ.get("AGENTD_DAEMON_CMD", "").strip()
    if override:
        import shlex

        return shlex.split(override, posix=(sys.platform != "win32"))
    return [sys.executable, "-m", "agentd"]


def spawn_daemon(wait_sec: float = 300.0) -> GatewayInfo:
    """Start the daemon DETACHED and wait until it is accepting connections.

    Raises RuntimeError with the log tail when it fails to come up in time."""
    if find_running() is not None:
        raise RuntimeError("a daemon is already running")
    clear_gateway_file()
    log_path = runtime_paths.logs_dir() / "daemon.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = open(log_path, "a", encoding="utf-8")  # noqa: SIM115 — handed to the child
    kwargs: dict = {
        "stdout": log_file,
        "stderr": subprocess.STDOUT,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if sys.platform == "win32":
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | getattr(subprocess, "DETACHED_PROCESS", 0x00000008)
            | getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        )
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen(daemon_command(), **kwargs)  # noqa: S603 — our own daemon
    log_file.close()  # the child holds its own handle now
    print("starting agentd daemon (first start can take a minute: plugins + MCP servers)...")

    deadline = time.monotonic() + wait_sec
    while time.monotonic() < deadline:
        # A HEALTHY rendezvous means a daemon is serving — ours, OR a concurrent
        # starter (a second terminal, the desktop supervisor) that won the port. We
        # cleared the file before spawning, so any file that appears is fresh; we do
        # NOT insist on our own child's pid (the loser of a single-instance race would
        # otherwise wait out the full timeout for a pid that never binds).
        info = read_gateway_file()
        if info is not None and port_open(info.host, info.port):
            return info
        if proc.poll() is not None:
            # our child exited without a live rendezvous — either it lost the race to a
            # winner still coming up, or it genuinely failed. Adopt a winner if present;
            # else surface the failure with the log tail.
            winner = find_running()
            if winner is not None:
                return winner
            raise RuntimeError(
                f"daemon exited immediately (code {proc.returncode}) — log tail:\n{_log_tail(log_path)}"
            )
        time.sleep(0.25)
    raise RuntimeError(
        f"daemon did not come up within {wait_sec:.0f}s — log tail:\n{_log_tail(log_path)}"
    )


def ensure_running(wait_sec: float = 300.0) -> tuple[GatewayInfo, bool]:
    """The live daemon, spawning one if needed. Returns (info, spawned_now)."""
    info = find_running()
    if info is not None:
        return info, False
    return spawn_daemon(wait_sec), True


def stop_daemon(timeout: float = 10.0) -> bool:
    """Stop the running daemon (if any). Returns True when it is down."""
    info = find_running()
    if info is None:
        clear_gateway_file()
        return True
    if sys.platform == "win32":
        subprocess.run(
            ["taskkill", "/PID", str(info.pid), "/T", "/F"], capture_output=True, check=False
        )
    else:
        import signal

        with contextlib.suppress(ProcessLookupError):
            os.kill(info.pid, signal.SIGTERM)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not pid_alive(info.pid):
            clear_gateway_file(only_pid=info.pid)
            return True
        time.sleep(0.2)
    return not pid_alive(info.pid)


def _log_tail(path: Path, lines: int = 15) -> str:
    try:
        return "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:])
    except OSError:
        return "(no log)"
