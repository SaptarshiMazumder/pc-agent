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
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s"
    )
    config = load_config()
    gateway = build_gateway(config)
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        print("\nagentd stopped")
