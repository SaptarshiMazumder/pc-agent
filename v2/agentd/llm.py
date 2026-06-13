"""LiteLLM-backed stream function.

Implements the loop's StreamFn contract: an async generator of
  {"type":"text_delta","delta":...}
  {"type":"thinking_delta","delta":...}
  {"type":"toolcall_end","toolCall":{...}}
  {"type":"done","message": AssistantMessage}

Contract: NEVER raises. Provider errors and aborts surface as a final
AssistantMessage with stop_reason "error"/"aborted".

Tool-call deltas are assembled keyed by `index` (id/name appear only on the
first fragment; `arguments` arrives as string shards, parsed once at the end).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from .types import (
    AssistantMessage,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "toolUse",
    "function_call": "toolUse",
}


def messages_to_litellm(system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Convert internal history to OpenAI-style chat messages."""
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if isinstance(m, UserMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AssistantMessage):
            entry: dict[str, Any] = {"role": "assistant"}
            text = m.text  # thinking blocks are dropped outbound
            entry["content"] = text or None
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
            if tool_calls:
                entry["tool_calls"] = tool_calls
            if entry["content"] is None and not tool_calls:
                continue  # skip empty assistant messages (e.g. errored turns)
            out.append(entry)
        elif isinstance(m, ToolResultMessage):
            text = m.text or "(no output)"
            if m.is_error:
                text = f"ERROR: {text}"
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": text})
    return out


def tools_to_litellm(tools) -> list[dict[str, Any]] | None:
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,
            },
        }
        for t in tools
    ]


class _ToolCallAccumulator:
    """Assembles streamed tool-call fragments keyed by index."""

    def __init__(self):
        self.calls: dict[int, dict[str, Any]] = {}

    def feed(self, delta_tool_calls) -> None:
        for frag in delta_tool_calls or []:
            index = getattr(frag, "index", None)
            index = 0 if index is None else index
            slot = self.calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
            frag_id = getattr(frag, "id", None)
            if frag_id:
                slot["id"] = frag_id
            fn = getattr(frag, "function", None)
            if fn is not None:
                name = getattr(fn, "name", None)
                if name:
                    slot["name"] = name
                args = getattr(fn, "arguments", None)
                if args:
                    slot["arguments"] += args

    def finish(self) -> list[ToolCallContent]:
        out = []
        for index in sorted(self.calls):
            slot = self.calls[index]
            raw = slot["arguments"].strip()
            try:
                arguments = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                arguments = {"_raw": raw}
            if not isinstance(arguments, dict):
                arguments = {"_raw": arguments}
            out.append(
                ToolCallContent(
                    id=slot["id"] or f"call_{uuid.uuid4().hex[:8]}",
                    name=slot["name"] or "unknown",
                    arguments=arguments,
                )
            )
        return out


async def litellm_stream(
    *,
    model: str,
    system_prompt: str,
    messages: list[Message],
    tools,
    abort: asyncio.Event,
    temperature: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    import litellm

    litellm.suppress_debug_info = True

    text_parts: list[str] = []
    thinking_parts: list[str] = []
    acc = _ToolCallAccumulator()
    usage: dict[str, int] = {}
    finish_reason: str | None = None
    error_message: str | None = None
    aborted = False

    try:
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages_to_litellm(system_prompt, messages),
            stream=True,
            stream_options={"include_usage": True},
        )
        lite_tools = tools_to_litellm(tools)
        if lite_tools:
            kwargs["tools"] = lite_tools
        if temperature is not None:
            kwargs["temperature"] = temperature

        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            if abort.is_set():
                aborted = True
                break

            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "input": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    "output": getattr(chunk_usage, "completion_tokens", 0) or 0,
                }

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue  # usage-only final chunk
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                thinking_parts.append(reasoning)
                yield {"type": "thinking_delta", "delta": reasoning}

            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield {"type": "text_delta", "delta": content}

            tool_call_frags = getattr(delta, "tool_calls", None)
            if tool_call_frags:
                acc.feed(tool_call_frags)
    except asyncio.CancelledError:
        raise
    except Exception as e:  # never propagate provider failures
        error_message = f"{type(e).__name__}: {e}"

    tool_calls = acc.finish()
    content: list = []
    if thinking_parts:
        content.append(ThinkingContent(thinking="".join(thinking_parts)))
    if text_parts:
        content.append(TextContent(text="".join(text_parts)))
    content.extend(tool_calls)

    for tc in tool_calls:
        yield {
            "type": "toolcall_end",
            "toolCall": {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
        }

    if error_message is not None:
        stop_reason = "error"
    elif aborted:
        stop_reason = "aborted"
    elif tool_calls:
        stop_reason = "toolUse"
    else:
        stop_reason = FINISH_REASON_MAP.get(finish_reason or "stop", "stop")

    yield {
        "type": "done",
        "message": AssistantMessage(
            content=content,
            stop_reason=stop_reason,
            usage=usage,
            model=model,
            error_message=error_message,
        ),
    }
