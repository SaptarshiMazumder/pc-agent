"""Tool interface, validation, and registry.

Mirrors the reference AgentTool contract:
  {name, description, parameters(JSON Schema), label,
   execute(tool_call_id, params, abort, on_update) -> ToolResult}
Tools declare concurrency: "parallel" (default) or "sequential".
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

from ..types import ContentBlock, TextContent


@dataclass
class ToolResult:
    content: list[ContentBlock] = field(default_factory=list)
    details: Any = None
    is_error: bool = False

    @staticmethod
    def text(text: str, details: Any = None, is_error: bool = False) -> "ToolResult":
        return ToolResult(content=[TextContent(text=text)], details=details, is_error=is_error)


OnUpdate = Callable[[ToolResult], None]


class Tool(ABC):
    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}
    label: str = ""
    concurrency: str = "parallel"  # or "sequential"

    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],
        abort: asyncio.Event,
        on_update: OnUpdate | None = None,
    ) -> ToolResult: ...


class ToolArgError(Exception):
    pass


def validate_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Validate args against the tool's JSON Schema; raise ToolArgError on mismatch."""
    try:
        jsonschema.validate(instance=args, schema=tool.parameters)
    except jsonschema.ValidationError as e:
        raise ToolArgError(f"Invalid arguments for tool '{tool.name}': {e.message}") from e
    return args


def build_tools(config, browser_manager=None) -> list[Tool]:
    """Assemble the active tool list. Imported lazily so optional deps
    (playwright, ddgs, trafilatura) don't break unrelated tools."""
    from .exec_tool import ExecTool, ProcessTool
    from .fs_tools import EditTool, FindTool, LsTool, ReadTool, WriteTool
    from .web_fetch import WebFetchTool
    from .web_search import WebSearchTool

    tools: list[Tool] = [
        ReadTool(config),
        WriteTool(config),
        EditTool(config),
        LsTool(config),
        FindTool(config),
        ExecTool(config),
        ProcessTool(config),
        WebSearchTool(config),
        WebFetchTool(config),
    ]
    if browser_manager is not None:
        from .browser import BrowserTool

        tools.append(BrowserTool(config, browser_manager))
    return tools
