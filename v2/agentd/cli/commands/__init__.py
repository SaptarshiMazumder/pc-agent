"""Command registry: every module here exposes register(subparsers). Adding a
command = adding a module + one entry in ALL (order = help order)."""

from agentd.cli.commands import agents, chat, daemon, doctor, plugins, serve

ALL = [chat, serve, daemon, doctor, agents, plugins]
