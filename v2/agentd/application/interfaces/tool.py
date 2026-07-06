"""The Tool contract — the framework surface every tool (built-in plugin OR third-party)
implements, and that plugins import.

This is the clean-architecture home for the tool abstraction: an INTERFACE in the application
layer, depending only on domain types. Tool *implementations* live OUTSIDE the core (in
``plugins/``); they import ``Tool`` / ``ToolResult`` from here. The engine, the guard middleware,
and the loaders depend on this interface, never on a concrete tool.

(Historically these types lived in ``agentd.infrastructure.tools``; that module now RE-EXPORTS
them from here for backward compatibility, so existing ``from agentd.infrastructure.tools import
Tool`` imports keep working.)
"""

from __future__ import annotations

import asyncio
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Callable

import jsonschema

from agentd.domain.messages import ContentBlock, TextContent


@dataclass
class ToolResult:
    """What a tool hands back: a list of content blocks plus an error flag.

    ``details`` is optional structured data (kept out of the model's view, e.g. raw
    search results); ``is_error`` tells the loop/model the tool failed.

    ``artifacts`` is the DELIVERABLE channel: absolute paths of files THIS tool produced
    and wants presented to the user (a figure, a deck, a video, …). This is the ONLY way a
    file gets rendered inline — it is never inferred from the tool's text. A tool that just
    reads/searches/lists files (find/ls/read) leaves this empty, so it can never surface a
    random file. The loop resolves each path to a typed artifact (mime/kind) for the client.
    """

    content: list[ContentBlock] = field(default_factory=list)
    details: Any = None
    is_error: bool = False
    artifacts: list[str] = field(default_factory=list)

    @staticmethod
    def text(text: str, details: Any = None, is_error: bool = False,
             artifacts: list[str] | None = None) -> "ToolResult":
        """Convenience constructor for the common case: a single text result (optionally
        declaring the deliverable file(s) the tool produced)."""
        return ToolResult(content=[TextContent(text=text)], details=details,
                          is_error=is_error, artifacts=list(artifacts or []))


# A tool may call this to report incremental progress.
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

    # --- model-bearing metadata (only meaningful when needs_model=True) ---------------
    # A tool that calls an LLM/VLM/diffusion model declares its home ``plugin`` (the config
    # grouping), sets ``needs_model = True``, and gives a ``default_model`` — the last-resort
    # fallback used when nothing is configured (see resolve_tool_model's PLUGIN->TOOL->model
    # precedence). Pure/deterministic tools (file, shell, browser, ...) leave these defaults:
    # needs_model=False means the model resolver skips the tool entirely.
    plugin: str = ""
    needs_model: bool = False
    default_model: str = ""  # "" => a text helper may inherit the brain model; VLM tools set a real id

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
