"""computer tool: dispatcher (ComputerTool) + ComputerProvider adapters + factory."""

from agentd.infrastructure.tools.computer.factory import build_computer_provider
from agentd.infrastructure.tools.computer.tool import ComputerTool

__all__ = ["ComputerTool", "build_computer_provider"]
