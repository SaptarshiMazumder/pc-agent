"""computer INFRASTRUCTURE: the ComputerProvider adapters (OS screen/keyboard/mouse driver) +
factory. The container builds + INJECTS this; the computer TOOL (the dispatcher the model calls)
migrated out to the built-in 'computer' plugin (plugins/computer/), which receives the provider via
``ctx.computer``."""

from agent_runtime.infrastructure.tools.computer.factory import build_computer_provider

__all__ = ["build_computer_provider"]
