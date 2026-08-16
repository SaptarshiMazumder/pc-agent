"""Restart the daemon — from OUTSIDE it, because nothing can respawn itself.

    python -m agent_runtime.respawn

Kill the running daemon by the pid in the rendezvous file, wait for it to actually go, clear the
file, start a fresh one. Exactly what the dev pre-start script does in Node, in the language that
already owns both halves (``lifecycle.stop_daemon`` / ``lifecycle.spawn_daemon``).

WHY IT IS A SEPARATE PROCESS. A daemon asked to restart itself can stop, and can do nothing after
that — the thing that starts the replacement has to outlive the kill. So ``POST /restart`` spawns
this DETACHED and answers immediately; this then kills its own parent and brings up the successor.

It is also why the kill is `taskkill /T` / SIGTERM by pid rather than a graceful in-process
shutdown: a daemon wedged in a way that stops it answering its own event loop still dies to a
signal from outside.
"""

from __future__ import annotations

import sys
import time

from agent_runtime import lifecycle

#: The caller (the daemon serving `POST /restart`) needs its reply to reach the client before its
#: process disappears underneath the socket.
REPLY_GRACE_S = 0.4


#: How long to wait for the port to actually free up after the daemon dies. A socket in
#: TIME_WAIT, or a lingering child still holding it, makes the replacement fail to bind — and
#: that failure reads as "the restart broke agentd" rather than "wait a second longer".
PORT_FREE_TIMEOUT_S = 15.0


def main() -> int:
    time.sleep(REPLY_GRACE_S)
    before = lifecycle.read_gateway_file()  # host/port, so we can confirm the port is free

    # NOT kill_tree. This process is a CHILD of the daemon it is about to kill, so killing the
    # tree kills the restarter — leaving a dead daemon, a closed port, and nobody to start the
    # replacement. Learned the direct way: the endpoint answered 200 and the machine lost its
    # daemon.
    if not lifecycle.stop_daemon(kill_tree=False):
        print("respawn: the old daemon did not stop — not starting a second one", file=sys.stderr)
        return 1

    if before is not None:
        deadline = time.monotonic() + PORT_FREE_TIMEOUT_S
        while time.monotonic() < deadline and lifecycle.port_open(before.host, before.port):
            time.sleep(0.25)
        if lifecycle.port_open(before.host, before.port):
            print(
                f"respawn: {before.host}:{before.port} is still held after the daemon died — "
                f"something else has it; not starting a second daemon",
                file=sys.stderr,
            )
            return 1

    # stop_daemon clears the rendezvous for the pid it killed; clear unconditionally in case the
    # file described someone else by then. A stale file makes the next daemon refuse to bind
    # ("agentd is already running"), which turns a restart into an outage.
    lifecycle.clear_gateway_file()
    try:
        info = lifecycle.spawn_daemon()
    except RuntimeError as e:
        print(f"respawn: {e}", file=sys.stderr)
        return 1
    print(f"respawn: agentd up again on {info.host}:{info.port} (pid {info.pid})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
