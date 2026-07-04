"""Shim: `python -m clients.watch` -> agentd.clients.watch (moved into the package)."""

from agentd.clients.watch import *  # noqa: F401,F403
from agentd.clients.watch import main

if __name__ == "__main__":
    main()
