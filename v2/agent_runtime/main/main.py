"""Entrypoint: load config, build the app via the composition root, serve."""

from __future__ import annotations

import asyncio
import logging
import sys
import time

if sys.platform == "win32":
    # Must be set before any event loop is created (Playwright + asyncio subprocesses
    # need the Proactor loop on Windows). Set at import time so it's in place by main().
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from agent_runtime.config import load_config
from agent_runtime.main.container import build_gateway


def main() -> None:
    # Structured JSON logs when the telemetry package is available, plain text otherwise (an
    # older install, or a wheel built before it existed). JSON is what makes every line carry
    # the run's tracking number automatically, and what puts each line through the redaction
    # allowlist — so a stray log call cannot put user content into CloudWatch.
    from agent_runtime.infrastructure import telemetry

    if telemetry.AVAILABLE:
        telemetry.setup_logging("daemon")
    else:
        logging.basicConfig(
            level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
        )
    # Self-onboard BEFORE loading config. The desktop shell (and any `python -m agent_runtime`)
    # spawns THIS entry point directly, bypassing the CLI's `agentd serve` — so onboarding
    # must live here too, or a fresh packaged install boots with no config and crashes in
    # build_gateway (no brain model). Idempotent: a no-op once a config exists, so the CLI
    # commands' own ensure_onboarded() calls stay harmless.
    from agent_runtime.cli.first_run import ensure_onboarded

    ensure_onboarded()
    config = load_config()
    # DIAGNOSTICS (plan 5.1). Inert unless the user opted in AND the build has somewhere to send
    # them. Applied before build_gateway so a crash during startup is still reported.
    telemetry.apply_diagnostics(config)
    _started = time.perf_counter()
    gateway = build_gateway(config)
    # "The daemon started" is the signal v0.1.0 did not have: a broken embedded runtime produced
    # no traffic anywhere, so the failure was indistinguishable from nobody using the product.
    # Emitted after the composition root, because a daemon that cannot build its gateway has not
    # started in any sense a user would recognise.
    telemetry.timing("daemon_start_ms", (time.perf_counter() - _started) * 1000, outcome="ok")
    telemetry.count("daemon_start_total", outcome="ok")
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        print("\nagentd stopped")
    except OSError:
        # Lost the port race: another `python -m agent_runtime` bound the gateway port between
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

        from agent_runtime import lifecycle, runtime_paths

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

        # Nobody claimed the port through the rendezvous, but the bind still failed. Two real
        # causes, and a raw OSError traceback names neither:
        #
        #   * an agentd is serving without a discoverable rendezvous (its state dir was cleared
        #     while it ran) — the loop that makes every subsequent launch fail identically
        #   * a FOREIGN process holds the port, which is the version a real user hits
        #
        # The traceback that used to land here is unreadable to a user of a downloaded app and
        # tells a developer nothing about which case it is. Say what happened and what to do,
        # then exit non-zero — the supervisor surfaces the message, and its circuit breaker stops
        # respawning into a port that is never going to free itself.
        host = getattr(config, "host", "127.0.0.1")
        port = getattr(config, "port", "?")
        occupied = lifecycle.port_open(host, int(port)) if str(port).isdigit() else True
        log = logging.getLogger("agentd")
        if occupied:
            log.error(
                "cannot start: %s:%s is already in use, and whatever holds it did not publish a "
                "rendezvous at %s. Either another agentd is running from a state directory that "
                "has since been cleared, or another program owns the port. Close it (Windows: "
                "`netstat -ano | findstr :%s` then `taskkill /PID <pid> /F`), or set a different "
                "port with AGENTD_PORT.",
                host,
                port,
                runtime_paths.gateway_file(),
                port,
            )
            raise SystemExit(1)
        raise
