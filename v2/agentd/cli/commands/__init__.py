"""Command registry: every module here exposes register(subparsers). Adding a
command = adding a module + one entry in ALL (order = help order)."""

from agentd.cli.commands import (
    agents,
    app,
    bundle,
    chat,
    daemon,
    doctor,
    license_,
    marketplace,
    plugins,
    projects,
    serve,
    sessions,
)

ALL = [
    chat,
    serve,
    daemon,
    doctor,
    agents,
    app,
    sessions,
    projects,
    plugins,
    marketplace,
    bundle,
    license_,
]
