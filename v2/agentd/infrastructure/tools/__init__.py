"""Tools — the agent's capabilities, all in one place.

This package is the agent's whole toolbox: every tool the agent can call lives in
THIS folder (`fs_tools.py`, `exec_tool.py`, `web_search.py`, `web_fetch.py`,
`browser.py`). Want to see all tools? Look here. Want to add one? Drop a new file
here and add it to `build_tools` below.

This file holds three things shared by every tool:
  * ``Tool``        — the CONTRACT every tool must satisfy (name/description/params/execute).
                      (In strict clean-architecture terms this contract belongs in
                      application/interfaces; kept here for now so the tool files'
                      ``from . import Tool`` keep working — to be lifted out in a later step.)
  * ``ToolResult``  — the standard shape a tool returns (content blocks + error flag).
  * ``build_tools`` — the REGISTRY: the single list of which tools are active.
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

# Domain types (a tool's output is made of content blocks). Imported from the
# canonical domain location now that tools live in the infrastructure layer.
from agentd.domain.messages import ContentBlock, TextContent


@dataclass
class ToolResult:
    """What a tool hands back: a list of content blocks plus an error flag.

    ``details`` is optional structured data (kept out of the model's view, e.g. raw
    search results); ``is_error`` tells the loop/model the tool failed.
    """

    content: list[ContentBlock] = field(default_factory=list)
    details: Any = None
    is_error: bool = False

    @staticmethod
    def text(text: str, details: Any = None, is_error: bool = False) -> "ToolResult":
        """Convenience constructor for the common case: a single text result."""
        return ToolResult(content=[TextContent(text=text)], details=details, is_error=is_error)


# A tool may call this to report incremental progress (rarely used today).
OnUpdate = Callable[[ToolResult], None]


class Tool(ABC):
    """The contract every tool implements.

    A tool is identified by ``name``, described to the model by ``description``, and
    declares its argument schema in ``parameters`` (JSON Schema). ``concurrency``
    says whether it's safe to run in parallel with sibling tool calls ("parallel")
    or must run alone in order ("sequential" — e.g. exec/edit/browser, which have
    side effects). ``execute`` does the actual work.
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}  # JSON Schema for the tool's arguments
    label: str = ""
    concurrency: str = "parallel"  # "parallel" (default) or "sequential"

    @abstractmethod
    async def execute(
        self,
        tool_call_id: str,
        params: dict[str, Any],
        abort: asyncio.Event,
        on_update: OnUpdate | None = None,
    ) -> ToolResult: ...


class ToolArgError(Exception):
    """Raised when the model's tool arguments don't match the tool's schema."""


def validate_args(tool: Tool, args: dict[str, Any]) -> dict[str, Any]:
    """Validate the model's args against the tool's JSON Schema before running it.
    Raises ToolArgError on a mismatch (the loop turns that into an error result so
    the model can correct itself, rather than executing a malformed call)."""
    try:
        jsonschema.validate(instance=args, schema=tool.parameters)
    except jsonschema.ValidationError as e:
        raise ToolArgError(f"Invalid arguments for tool '{tool.name}': {e.message}") from e
    return args


def build_tools(config, browser_manager=None, computer_provider=None,
                task_store=None) -> list[Tool]:
    """The REGISTRY: assemble the list of active tools.

    Tool classes are imported lazily (inside this function) so that an optional
    dependency (playwright, ddgs, trafilatura) failing to import never breaks the
    unrelated tools. The browser tool is only included when a browser manager is
    provided (it needs Playwright).
    """
    from .exec_tool import ExecTool, ProcessTool
    from .fetch import build_fetch_providers
    from .fs_tools import EditTool, FindTool, LsTool, ReadTool, WriteTool
    from .search import build_search_providers
    from .update_plan_tool import UpdatePlanTool
    from .web_fetch import WebFetchTool
    from .web_search import WebSearchTool

    tools: list[Tool] = [
        UpdatePlanTool(),
        ReadTool(config),
        WriteTool(config),
        EditTool(config),
        LsTool(config),
        FindTool(config),
        ExecTool(config),
        ProcessTool(config),
        WebSearchTool(config, build_search_providers(config)),
        WebFetchTool(config, build_fetch_providers(config, browser_manager)),
    ]
    # Autonomy: heartbeat_respond is built only when autonomy is enabled (it's then
    # exposed ONLY on a heartbeat tick — see domain.agent.apply_mode). Off => the tool
    # never exists, so interactive chat is exactly as before.
    if getattr(config, "autonomy_enabled", False):
        from .heartbeat_tool import HeartbeatRespondTool
        from .outcome_tool import ReportOutcomeTool

        tools.append(HeartbeatRespondTool())
        tools.append(ReportOutcomeTool())   # exposed ONLY on a cron run (apply_mode)
        # cron + goal — durable scheduling + objective tracking (need the ledger).
        if task_store is not None:
            from .cron_tool import CronTool
            from .goal_tool import GoalTool

            tools.append(CronTool(task_store))
            tools.append(GoalTool(task_store))   # SqliteTaskStore implements GoalStore too
    if browser_manager is not None:
        from .browser import BrowserTool

        tools.append(BrowserTool(config, browser_manager))
    if computer_provider is not None:
        from .computer import ComputerTool

        tools.append(ComputerTool(config, computer_provider))
    # Agent-invoked answer-review tool (AGENTD_VERIFY_TOOL=1). Off => not registered
    # at all, so it's exactly as if the feature never existed.
    if getattr(config, "verify_tool", False):
        from agentd.infrastructure.verify import LlmJudgeVerifier, build_judge_fn

        judge = build_judge_fn(config)
        if judge is not None:
            from .verify_tool import VerifyTool

            tools.append(VerifyTool(config, LlmJudgeVerifier(judge)))
    return tools
