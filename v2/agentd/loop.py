"""The core agent loop.

Faithful port of the reference agent-loop semantics:

  emit agent_start
  while turn < max_turns:
      emit turn_start, message_start
      stream LLM -> forward deltas as message_update -> AssistantMessage
      persist; emit message_end
      if no tool calls or terminal stop reason: emit turn_end; break
      validate + execute tool calls (parallel batch / sequential in order)
      append ToolResultMessages in assistant source order; emit turn_end
  emit agent_end {stopReason}

The stream function never raises: provider errors arrive as an
AssistantMessage with stop_reason "error"/"aborted".
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, AsyncIterator, Callable, Protocol

from .events import AgentEvent, EventCallback
from .session import SessionStore
from .types import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    message_to_dict,
)
from .tools import Tool, ToolArgError, ToolResult, validate_args


class StreamFn(Protocol):
    def __call__(
        self,
        *,
        model: str,
        system_prompt: str,
        messages: list[Message],
        tools: list[Tool],
        abort: asyncio.Event,
    ) -> AsyncIterator[dict[str, Any]]: ...


TERMINAL_STOP_REASONS = ("stop", "length", "error", "aborted")


async def run_agent_loop(
    *,
    messages: list[Message],
    system_prompt: str,
    tools: list[Tool],
    stream_fn: StreamFn,
    model: str,
    on_event: EventCallback,
    abort: asyncio.Event,
    session: SessionStore | None = None,
    max_turns: int = 50,
) -> list[Message]:
    """Run the loop until the model stops calling tools. Mutates `messages`
    in place and returns only the messages produced by this run."""
    tool_map = {t.name: t for t in tools}
    new_messages: list[Message] = []
    stop_reason = "stop"

    def persist(m: Message) -> None:
        messages.append(m)
        new_messages.append(m)
        if session is not None:
            session.append(m)

    await on_event(AgentEvent("agent_start", {}))
    try:
        for _turn in range(max_turns):
            await on_event(AgentEvent("turn_start", {}))
            await on_event(AgentEvent("message_start", {"role": "assistant"}))

            assistant: AssistantMessage | None = None
            async for ev in stream_fn(
                model=model,
                system_prompt=system_prompt,
                messages=messages,
                tools=tools,
                abort=abort,
            ):
                kind = ev.get("type")
                if kind in ("text_delta", "thinking_delta"):
                    await on_event(
                        AgentEvent("message_update", {"kind": kind, "delta": ev.get("delta", "")})
                    )
                elif kind == "toolcall_end":
                    await on_event(
                        AgentEvent(
                            "message_update",
                            {"kind": "toolcall", "toolCall": ev.get("toolCall")},
                        )
                    )
                elif kind == "done":
                    assistant = ev["message"]

            if assistant is None:  # defensive: stream ended without a done event
                assistant = AssistantMessage(
                    stop_reason="error", error_message="stream ended without result"
                )
            persist(assistant)
            await on_event(
                AgentEvent("message_end", {"message": message_to_dict(assistant)})
            )

            tool_calls = assistant.tool_calls
            if not tool_calls or assistant.stop_reason in TERMINAL_STOP_REASONS:
                stop_reason = assistant.stop_reason
                await on_event(AgentEvent("turn_end", {}))
                break

            results = await _execute_tool_calls(tool_calls, tool_map, abort, on_event)
            for r in results:  # assistant source order
                persist(r)
                await on_event(AgentEvent("message_end", {"message": message_to_dict(r)}))
            await on_event(AgentEvent("turn_end", {}))

            if abort.is_set():
                stop_reason = "aborted"
                break
        else:
            stop_reason = "stop"  # max_turns exhausted
    except asyncio.CancelledError:
        abort.set()
        await on_event(AgentEvent("agent_end", {"stopReason": "aborted"}))
        raise
    await on_event(AgentEvent("agent_end", {"stopReason": stop_reason}))
    return new_messages


async def _execute_tool_calls(
    tool_calls: list[ToolCallContent],
    tool_map: dict[str, Tool],
    abort: asyncio.Event,
    on_event: EventCallback,
) -> list[ToolResultMessage]:
    """Execute one assistant turn's tool calls.

    Parallel-capable tools run concurrently; sequential tools run in source
    order after the parallel batch. Results are returned in source order.
    """
    results: dict[int, ToolResultMessage] = {}

    async def run_one(index: int, call: ToolCallContent) -> None:
        tool = tool_map.get(call.name)
        await on_event(
            AgentEvent(
                "tool_execution_start",
                {"toolCallId": call.id, "toolName": call.name, "args": call.arguments},
            )
        )
        if tool is None:
            result = ToolResult.text(f"Unknown tool: {call.name}", is_error=True)
        else:
            try:
                args = validate_args(tool, call.arguments)
                result = await tool.execute(call.id, args, abort)
            except ToolArgError as e:
                result = ToolResult.text(str(e), is_error=True)
            except asyncio.CancelledError:
                raise
            except Exception:
                result = ToolResult.text(
                    f"Tool '{call.name}' failed:\n{traceback.format_exc(limit=4)}",
                    is_error=True,
                )
        msg = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=result.content,
            is_error=result.is_error,
        )
        results[index] = msg
        await on_event(
            AgentEvent(
                "tool_execution_end",
                {
                    "toolCallId": call.id,
                    "toolName": call.name,
                    "isError": result.is_error,
                    "result": message_to_dict(msg),
                },
            )
        )

    parallel: list[tuple[int, ToolCallContent]] = []
    sequential: list[tuple[int, ToolCallContent]] = []
    for i, call in enumerate(tool_calls):
        tool = tool_map.get(call.name)
        if tool is not None and tool.concurrency == "sequential":
            sequential.append((i, call))
        else:
            parallel.append((i, call))

    if parallel:
        await asyncio.gather(*(run_one(i, c) for i, c in parallel))
    for i, call in sequential:
        await run_one(i, call)

    return [results[i] for i in sorted(results)]
