"""Shim: `python -m clients.terminal` -> agent_runtime.clients.terminal (moved into the package)."""

from agent_runtime.clients.terminal.__main__ import main

if __name__ == "__main__":
    main()
