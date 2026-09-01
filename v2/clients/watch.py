"""Shim: `python -m clients.watch` -> agent_runtime.clients.watch (moved into the package)."""

from agent_runtime.clients.watch import *  # noqa: F401,F403
from agent_runtime.clients.watch import main

if __name__ == "__main__":
    main()
