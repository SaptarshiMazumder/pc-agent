"""Command registry: every module here exposes register(subparsers). Adding a
command = adding a module + one entry in ALL (order = help order)."""

from agent_runtime.cli.commands import (
    agents,
    app,
    ask,
    bundle,
    chat,
    daemon,
    doctor,
    license_,
    marketplace,
    plugins,
    product,
    projects,
    serve,
    sessions,
)

ALL = [
    chat,
    ask,
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
    product,
    license_,
]
