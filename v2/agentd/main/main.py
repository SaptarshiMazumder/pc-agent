"""Entrypoint: load config, build the app via the composition root, serve."""

from __future__ import annotations

import asyncio
import logging
import sys

if sys.platform == "win32":
    # Must be set before any event loop is created (Playwright + asyncio subprocesses
    # need the Proactor loop on Windows). Set at import time so it's in place by main().
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from agentd.config import load_config
from agentd.main.container import build_gateway


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # Self-onboard BEFORE loading config. The desktop shell (and any `python -m agentd`)
    # spawns THIS entry point directly, bypassing the CLI's `agentd serve` — so onboarding
    # must live here too, or a fresh packaged install boots with no config and crashes in
    # build_gateway (no brain model). Idempotent: a no-op once a config exists, so the CLI
    # commands' own ensure_onboarded() calls stay harmless.
    from agentd.cli.first_run import ensure_onboarded

    ensure_onboarded()
    config = load_config()
    gateway = build_gateway(config)
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        print("\nagentd stopped")
    except OSError:
        # Lost the port race: another `python -m agentd` bound the gateway port between
        # serve()'s find_running() guard and its actual bind (a CONCURRENT start — the
        # desktop supervisor racing a second starter, or a stale-rendezvous respawn). The
        # loser used to die here with a raw OSError 10048 traceback, which the supervisor
        # read as a crash and retried — N racing daemons, each loading plugins + spawning
        # children, is the process storm that can wedge the machine. Exit CLEANLY instead:
        # the winner is serving and every client finds it via ~/.agentd/gateway.json. Poll
        # briefly (the winner writes its rendezvous just after binding); re-raise only if
        # no agentd owns the port (a genuine bind failure, e.g. a foreign process on it).
        import os
        import time as _time

        from agentd import lifecycle

        me = os.getpid()
        for _ in range(15):
            winner = lifecycle.find_running()
            if winner is not None and winner.pid != me:
                logging.getLogger("agentd").info(
                    "another agentd (pid %s) already owns %s:%s — this instance exits cleanly",
                    winner.pid,
                    getattr(config, "host", "?"),
                    getattr(config, "port", "?"),
                )
                return
            _time.sleep(0.2)
        raise
