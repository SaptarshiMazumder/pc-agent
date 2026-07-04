"""Shim: `python -m clients.terminal` -> agentd.clients.terminal (moved into the package)."""

from agentd.clients.terminal.__main__ import main

if __name__ == "__main__":
    main()
