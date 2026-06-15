"""The core agent loop.

Faithful port of OpenClaw's agent-loop + embedded-agent-runner continuation
protocol. Beyond the basic ReAct cycle (LLM -> tools -> repeat), it adds:

  - steering messages injected before the first turn (get_steering_messages)
  - typed incomplete-turn retries after a no-tool-call turn:
      planning-only / reasoning-only / empty-response  (incomplete_turn.py)
  - follow-up message injection after a turn would end (get_follow_up_messages)
  - a before-finalize revision hook (verify_answer) — up to 3 revisions
  - OpenClaw's iteration cap (min(160, max(32, 24 + 8*profiles)))

Injected instructions are appended as synthetic user messages and the loop
continues, exactly like OpenClaw re-prompting the model to keep going.
"""

from __future__ import annotations

import asyncio
import traceback
from typing import Any, AsyncIterator, Awaitable, Callable, Protocol

from agentd.domain.events import AgentEvent, EventCallback
from .incomplete_turn import (
    INCOMPLETE_TURN_FALLBACK_TEXT,
    MAX_BEFORE_AGENT_FINALIZE_REVISIONS,
    RETRY_INSTRUCTIONS,
    RETRY_LIMITS,
    build_before_finalize_retry_prompt,
    classify_incomplete_turn,
    resolve_max_run_loop_iterations,
)
from agentd.infrastructure.memory.local_store import SessionStore
from agentd.domain.messages import (
    AssistantMessage,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
    message_to_dict,
)
from agentd.infrastructure.tools import Tool, ToolArgError, ToolResult, validate_args


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


# Stop reasons that end a turn without tool execution.
TERMINAL_STOP_REASONS = ("stop", "length", "error", "aborted")

FollowUpFn = Callable[[], Awaitable[list[Message]] | list[Message]]
VerifyFn = Callable[[list[Message]], Awaitable[str | None] | str | None]


async def _maybe_await(value):
    if asyncio.iscoroutine(value):
        return await value
    return value


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
    max_iterations: int | None = None,
    get_steering_messages: FollowUpFn | None = None,
    get_follow_up_messages: FollowUpFn | None = None,
    verify_answer: VerifyFn | None = None,
) -> list[Message]:
    """Run the loop until the model produces a genuine final answer (or limits
    are hit). Mutates `messages` in place; returns only messages produced here."""
    tool_map = {t.name: t for t in tools}
    new_messages: list[Message] = []
    stop_reason = "stop"
    max_iters = max_iterations or resolve_max_run_loop_iterations(1)

    retry_counts = {k: 0 for k in RETRY_LIMITS}
    finalize_revisions = 0
    produced_visible_text = False

    def persist(m: Message) -> None:
        messages.append(m)
        new_messages.append(m)
        if session is not None:
            session.append(m)

    def inject(text: str) -> None:
        persist(UserMessage(content=text))

    await on_event(AgentEvent("agent_start", {}))
    try:
        # Steering messages injected before the first turn (OpenClaw outer-loop start).
        if get_steering_messages is not None:
            for m in await _maybe_await(get_steering_messages()) or []:
                persist(m)

        iterations = 0
        while iterations < max_iters:
            iterations += 1
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
            if assistant.text.strip():
                produced_visible_text = True
            await on_event(AgentEvent("message_end", {"message": message_to_dict(assistant)}))

            if abort.is_set() or assistant.stop_reason == "aborted":
                stop_reason = "aborted"
                await on_event(AgentEvent("turn_end", {}))
                break

            tool_calls = assistant.tool_calls
            if tool_calls and assistant.stop_reason not in ("error", "aborted"):
                results = await _execute_tool_calls(tool_calls, tool_map, abort, on_event)
                for r in results:  # assistant source order
                    persist(r)
                    await on_event(AgentEvent("message_end", {"message": message_to_dict(r)}))
                await on_event(AgentEvent("turn_end", {}))
                if abort.is_set():
                    stop_reason = "aborted"
                    break
                continue  # back to the model with tool results

            # --- No tool calls: the turn would normally end. Decide if it's complete. ---
            await on_event(AgentEvent("turn_end", {}))

            if assistant.stop_reason in ("error",):
                stop_reason = "error"
                break

            # 1. Typed incomplete-turn retries (planning/reasoning/empty).
            kind = classify_incomplete_turn(assistant)
            if kind is not None and retry_counts[kind] < RETRY_LIMITS[kind]:
                retry_counts[kind] += 1
                await on_event(
                    AgentEvent("continuation", {"reason": kind, "attempt": retry_counts[kind]})
                )
                inject(RETRY_INSTRUCTIONS[kind])
                continue

            # 2. Follow-up message injection (harness-driven continuation).
            if get_follow_up_messages is not None:
                follow_ups = await _maybe_await(get_follow_up_messages()) or []
                if follow_ups:
                    for m in follow_ups:
                        persist(m)
                    continue

            # 3. Before-finalize revision hook.
            if verify_answer is not None and finalize_revisions < MAX_BEFORE_AGENT_FINALIZE_REVISIONS:
                reason = await _maybe_await(verify_answer(messages))
                if reason:
                    finalize_revisions += 1
                    await on_event(
                        AgentEvent("continuation", {"reason": "revision", "attempt": finalize_revisions})
                    )
                    inject(build_before_finalize_retry_prompt(reason))
                    continue

            # Genuinely done.
            stop_reason = assistant.stop_reason
            break
        else:
            stop_reason = "length"  # iteration cap exhausted
    except asyncio.CancelledError:
        abort.set()
        await on_event(AgentEvent("agent_end", {"stopReason": "aborted"}))
        raise

    # Fallback text when a run produced no user-visible answer at all.
    if not produced_visible_text and stop_reason not in ("aborted",):
        fallback = AssistantMessage(
            content=[TextContent(text=INCOMPLETE_TURN_FALLBACK_TEXT)], stop_reason=stop_reason
        )
        persist(fallback)
        await on_event(AgentEvent("message_end", {"message": message_to_dict(fallback)}))

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


class NativeEngine:
    """Our hand-rolled reason->act loop, wrapped as a swappable AgentEngine.

    Holds the LLM stream function + model id; ``run`` just delegates to
    ``run_agent_loop`` (the function above). Alternative engines (Claude Agent SDK,
    LangGraph) would be sibling classes implementing this same ``run`` shape — the
    application's AgentService calls whichever one it was given, none the wiser.
    """

    def __init__(self, stream_fn, model: str, max_iterations: int | None = None):
        self._stream_fn = stream_fn          # the LLMService (e.g. litellm_stream)
        self._model = model                  # which model id to pass each call
        self._max_iterations = max_iterations

    async def run(self, *, messages, system_prompt, tools, on_event, abort, session=None):
        return await run_agent_loop(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            stream_fn=self._stream_fn,
            model=self._model,
            on_event=on_event,
            abort=abort,
            session=session,
            max_iterations=self._max_iterations,
        )
