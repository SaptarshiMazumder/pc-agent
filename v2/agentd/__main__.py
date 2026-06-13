"""Entry point: python -m agentd"""

import asyncio
import logging
import sys

if sys.platform == "win32":
    # Required by both Playwright and asyncio subprocesses on Windows.
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

from .config import load_config
from .gateway import Gateway


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    config = load_config()
    gateway = Gateway(config)
    try:
        asyncio.run(gateway.serve())
    except KeyboardInterrupt:
        print("\nagentd stopped")


if __name__ == "__main__":
    main()
