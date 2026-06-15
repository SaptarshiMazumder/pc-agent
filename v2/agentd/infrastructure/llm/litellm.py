"""LiteLLM-backed LLM service — the concrete "talk to the model" implementation.

This is the INFRASTRUCTURE implementation of the LLM interface. It is the ONLY
place that knows about a real model provider. The agent engine calls it once per
loop iteration with the full conversation, and it streams the model's reply back.

Why LiteLLM: it is a universal LLM-call layer. You pass one uniform request and
just change the ``model`` string to switch providers (Gemini / Claude / GPT /
local Ollama / your own VM). LiteLLM translates the request into that provider's
real API and translates the reply back into one uniform shape. This is what makes
the agent provider-agnostic (the whole "any LLM / 3 tiers" design).

It yields a small stream of dict events (the contract the engine consumes):
  {"type":"text_delta",     "delta": ...}       # visible text, piece by piece
  {"type":"thinking_delta", "delta": ...}       # reasoning, piece by piece
  {"type":"toolcall_end",   "toolCall": {...}}  # a fully-assembled tool call
  {"type":"done",           "message": AssistantMessage}   # the final assembled turn

IMPORTANT CONTRACT: this function NEVER raises. A provider error or an abort is
turned into a final ``AssistantMessage`` with stop_reason "error"/"aborted", so the
loop above it can always continue gracefully instead of crashing.

Streaming tool calls are tricky: providers send a tool call in FRAGMENTS across
many chunks — the id/name appear only on the first fragment, and the JSON
``arguments`` arrive as string shards keyed by ``index``. We accumulate them and
parse the JSON once at the very end (see ``_ToolCallAccumulator``).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

# Domain types (the conversation vocabulary). Imported from the canonical domain
# location now that this module lives in the infrastructure layer.
from agentd.domain.messages import (
    AssistantMessage,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)

# Map a provider's "why did you stop" reason -> our internal stop_reason vocabulary.
FINISH_REASON_MAP = {
    "stop": "stop",
    "length": "length",
    "tool_calls": "toolUse",
    "function_call": "toolUse",
}


def messages_to_litellm(system_prompt: str, messages: list[Message]) -> list[dict[str, Any]]:
    """Convert our internal ``Message`` history into OpenAI-style chat dicts.

    LiteLLM speaks the OpenAI message format, so we translate each of our message
    types into it: user -> {"role":"user"}, assistant (with any tool calls) ->
    {"role":"assistant", "tool_calls":[...]}, tool result -> {"role":"tool"}.
    Thinking blocks are intentionally dropped on the way OUT (the model doesn't need
    its own past reasoning re-fed).
    """
    out: list[dict[str, Any]] = [{"role": "system", "content": system_prompt}]
    for m in messages:
        if isinstance(m, UserMessage):
            out.append({"role": "user", "content": m.content})
        elif isinstance(m, AssistantMessage):
            entry: dict[str, Any] = {"role": "assistant"}
            text = m.text  # visible text only; thinking blocks are dropped outbound
            entry["content"] = text or None
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    # the provider wants arguments as a JSON *string*, so re-encode them
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
            if tool_calls:
                entry["tool_calls"] = tool_calls
            # a turn with neither text nor tool calls (e.g. an errored turn) is useless
            # to re-send and some providers reject it, so skip it
            if entry["content"] is None and not tool_calls:
                continue
            out.append(entry)
        elif isinstance(m, ToolResultMessage):
            text = m.text or "(no output)"
            if m.is_error:
                text = f"ERROR: {text}"  # surface tool failures to the model as text
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": text})
    return out


def tools_to_litellm(tools) -> list[dict[str, Any]] | None:
    """Convert our tools into the OpenAI "function tool" schema the model expects.
    Returns None when there are no tools (so we don't send an empty tools field)."""
    if not tools:
        return None
    return [
        {
            "type": "function",
            "function": {
                "name": t.name,
                "description": t.description,
                "parameters": t.parameters,  # the tool's JSON-Schema for its args
            },
        }
        for t in tools
    ]


class _ToolCallAccumulator:
    """Reassembles streamed tool-call FRAGMENTS into whole tool calls.

    A provider streams a tool call across many chunks. The fragments are keyed by
    ``index`` (NOT by id — the id may be absent on later fragments). The first
    fragment for an index carries the id + function name; subsequent fragments
    append more characters of the ``arguments`` JSON string. We buffer per index
    and only ``json.loads`` the arguments at the very end, when they're complete.
    """

    def __init__(self):
        # index -> {"id": str|None, "name": str, "arguments": str (growing JSON)}
        self.calls: dict[int, dict[str, Any]] = {}

    def feed(self, delta_tool_calls) -> None:
        """Absorb the tool-call fragments from one streamed chunk."""
        for frag in delta_tool_calls or []:
            index = getattr(frag, "index", None)
            index = 0 if index is None else index  # some providers omit index on a single call
            slot = self.calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
            frag_id = getattr(frag, "id", None)
            if frag_id:  # id appears once (usually the first fragment)
                slot["id"] = frag_id
            fn = getattr(frag, "function", None)
            if fn is not None:
                name = getattr(fn, "name", None)
                if name:  # name also appears once
                    slot["name"] = name
                args = getattr(fn, "arguments", None)
                if args:  # arguments arrive as string shards -> concatenate
                    slot["arguments"] += args

    def finish(self) -> list[ToolCallContent]:
        """Finalize: parse each buffered JSON-args string into a ToolCallContent."""
        out = []
        for index in sorted(self.calls):  # preserve the order the provider used
            slot = self.calls[index]
            raw = slot["arguments"].strip()
            try:
                arguments = json.loads(raw) if raw else {}
            except json.JSONDecodeError:
                # if the model emitted malformed JSON, keep the raw text rather than crash
                arguments = {"_raw": raw}
            if not isinstance(arguments, dict):
                arguments = {"_raw": arguments}
            out.append(
                ToolCallContent(
                    id=slot["id"] or f"call_{uuid.uuid4().hex[:8]}",  # synthesize an id if missing
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
    reasoning_effort: str = "off",
) -> AsyncIterator[dict[str, Any]]:
    """Stream one model reply. Builds the request, streams chunks, assembles them
    into a final AssistantMessage, and yields progress events along the way.
    Never raises — errors/aborts become a "done" event with the right stop_reason."""
    import litellm  # imported lazily so importing this module is cheap/side-effect-free

    litellm.suppress_debug_info = True

    # Accumulators for the pieces we collect as the stream arrives:
    text_parts: list[str] = []       # visible text shards
    thinking_parts: list[str] = []   # reasoning shards
    acc = _ToolCallAccumulator()     # tool-call fragments
    usage: dict[str, int] = {}       # token counts (from the final usage-only chunk)
    finish_reason: str | None = None
    error_message: str | None = None
    aborted = False

    try:
        # --- Build the provider request ---
        kwargs: dict[str, Any] = dict(
            model=model,
            messages=messages_to_litellm(system_prompt, messages),  # full history each call
            stream=True,
            stream_options={"include_usage": True},  # ask for token usage in a final chunk
        )
        lite_tools = tools_to_litellm(tools)
        if lite_tools:
            kwargs["tools"] = lite_tools
        if temperature is not None:
            kwargs["temperature"] = temperature
        if reasoning_effort and reasoning_effort != "off":
            # LiteLLM maps reasoning_effort to each provider's own thinking control
            # (Gemini thinking budget, Anthropic thinking, OpenAI reasoning).
            kwargs["reasoning_effort"] = reasoning_effort
            kwargs["allowed_openai_params"] = ["reasoning_effort"]

        # --- Consume the stream chunk by chunk ---
        response = await litellm.acompletion(**kwargs)
        async for chunk in response:
            if abort.is_set():  # user cancelled mid-stream -> stop reading
                aborted = True
                break

            # token usage usually arrives in a final chunk (because include_usage=True)
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "input": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    "output": getattr(chunk_usage, "completion_tokens", 0) or 0,
                }

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue  # the usage-only final chunk has no choices
            choice = choices[0]
            if getattr(choice, "finish_reason", None):
                finish_reason = choice.finish_reason

            delta = getattr(choice, "delta", None)
            if delta is None:
                continue

            # reasoning text (only some models/levels emit this) -> stream it out
            reasoning = getattr(delta, "reasoning_content", None)
            if reasoning:
                thinking_parts.append(reasoning)
                yield {"type": "thinking_delta", "delta": reasoning}

            # visible text -> collect + stream it out so the UI shows it live
            content = getattr(delta, "content", None)
            if content:
                text_parts.append(content)
                yield {"type": "text_delta", "delta": content}

            # tool-call fragments -> buffer them (assembled after the stream ends)
            tool_call_frags = getattr(delta, "tool_calls", None)
            if tool_call_frags:
                acc.feed(tool_call_frags)
    except asyncio.CancelledError:
        raise  # a real task cancellation must propagate (don't swallow it)
    except Exception as e:
        # NEVER propagate provider failures — turn them into an error result instead
        error_message = f"{type(e).__name__}: {e}"

    # --- Assemble the final AssistantMessage from everything we collected ---
    tool_calls = acc.finish()
    content: list = []
    if thinking_parts:
        content.append(ThinkingContent(thinking="".join(thinking_parts)))
    if text_parts:
        content.append(TextContent(text="".join(text_parts)))
    content.extend(tool_calls)

    # announce each completed tool call (the loop watches for these)
    for tc in tool_calls:
        yield {
            "type": "toolcall_end",
            "toolCall": {"id": tc.id, "name": tc.name, "arguments": tc.arguments},
        }

    # decide why this turn ended (priority: error > aborted > tool use > provider reason)
    if error_message is not None:
        stop_reason = "error"
    elif aborted:
        stop_reason = "aborted"
    elif tool_calls:
        stop_reason = "toolUse"
    else:
        stop_reason = FINISH_REASON_MAP.get(finish_reason or "stop", "stop")

    # the final event: the fully-assembled assistant turn for the loop to use
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
