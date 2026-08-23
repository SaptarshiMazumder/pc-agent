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
import logging
import uuid
from collections.abc import AsyncIterator
from typing import Any

# Domain types (the conversation vocabulary). Imported from the canonical domain
# location now that this module lives in the infrastructure layer.
from agent_runtime.domain.messages import (
    AssistantMessage,
    ImageContent,
    Message,
    TextContent,
    ThinkingContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
)
from agent_runtime.infrastructure.engine.incomplete_turn import INCOMPLETE_TURN_FALLBACK_TEXT
from agent_runtime.infrastructure.files import image_data_url

log = logging.getLogger("agentd")

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
    emitted_call_ids: set[str] = set()  # tool-call ids actually sent, for pairing
    for m in messages:
        if isinstance(m, UserMessage):
            # A plain text turn stays a plain string. When the user ATTACHED files, build a
            # multimodal parts array: the text, each IMAGE inlined as a data URL so a vision
            # model can SEE it (read from disk at send time — the transcript only holds the
            # path), PLUS a one-line path note for EVERY attachment.
            #
            # Why the path note also covers images: a vision model can see an image's pixels,
            # but any tool that must operate on the FILE itself (vectorize, OCR, edit, "convert
            # this image to SVG") needs the on-disk PATH. Inlining pixels alone left the agent
            # with the picture but no path, so it hunted the filesystem with ls/find — and could
            # grab a coincidental name-match elsewhere instead of the file the user attached.
            if getattr(m, "attachments", None):
                parts: list[dict[str, Any]] = []
                if m.content:
                    parts.append({"type": "text", "text": m.content})
                notes: list[str] = []
                for att in m.attachments:
                    url = image_data_url(att.path)
                    if url is not None:
                        parts.append({"type": "image_url", "image_url": {"url": url}})
                    # every attachment (image or not) contributes its path — see note above
                    notes.append(f"- {att.name}: {att.path}")
                if notes:
                    # State WHERE each attachment lives on disk — plain fact, no behavioural
                    # instruction. Same shape as a tool result reporting the path it wrote; the
                    # model feeds it to a path-taking tool because it's the obvious input.
                    parts.append(
                        {
                            "type": "text",
                            "text": "Attached file(s), saved on disk at:\n" + "\n".join(notes),
                        }
                    )
                out.append({"role": "user", "content": parts or m.content})
            else:
                out.append({"role": "user", "content": m.content})
        elif isinstance(m, AssistantMessage):
            text = m.text  # visible text only; thinking blocks are dropped outbound
            tool_calls = [
                {
                    "id": tc.id,
                    "type": "function",
                    # the provider wants arguments as a JSON *string*, so re-encode them
                    "function": {"name": tc.name, "arguments": json.dumps(tc.arguments)},
                }
                for tc in m.tool_calls
            ]
            # Drop the UI-only "couldn't generate" placeholder: it has no tool call and, if it
            # lands between a tool call and its result, strict providers (Gemini) reject the
            # whole history with "missing corresponding tool call".
            if not tool_calls and text.strip() == INCOMPLETE_TURN_FALLBACK_TEXT:
                continue
            entry: dict[str, Any] = {"role": "assistant", "content": text or None}
            if tool_calls:
                entry["tool_calls"] = tool_calls
            # a turn with neither text nor tool calls (e.g. an errored turn) is useless
            # to re-send and some providers reject it, so skip it
            if entry["content"] is None and not tool_calls:
                continue
            out.append(entry)
            emitted_call_ids.update(tc.id for tc in m.tool_calls)
        elif isinstance(m, ToolResultMessage):
            # Drop an ORPHANED tool result — one whose tool call wasn't emitted above (lost to a
            # placeholder/errored turn). A tool message with no matching call is rejected by
            # strict providers (Gemini): "missing corresponding tool call".
            if m.tool_call_id not in emitted_call_ids:
                continue
            text = m.text or "(no output)"
            if m.is_error:
                text = f"ERROR: {text}"  # surface tool failures to the model as text
            out.append({"role": "tool", "tool_call_id": m.tool_call_id, "content": text})
            # If the tool returned IMAGE(s), forward them so a vision-capable model can SEE
            # them (just like Claude Code's read). Tool-role messages don't reliably carry
            # images across providers, so we attach them as a following user message — which
            # every provider LiteLLM supports accepts. (A text-only model simply ignores them.)
            images = [b for b in m.content if isinstance(b, ImageContent)]
            if images:
                parts: list[dict[str, Any]] = [
                    {"type": "text", "text": f"(image output from the {m.tool_name} tool above)"}
                ]
                for img in images:
                    parts.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{img.mime_type};base64,{img.data}"},
                        }
                    )
                out.append({"role": "user", "content": parts})
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


def _is_local_provider(model: str) -> bool:
    """True for local/self-hosted models (Ollama / LM Studio / vLLM / loopback),
    which legitimately stay silent for long stretches while loading weights — so
    the network-silence idle watchdog should not apply to them."""
    m = (model or "").lower()
    return (
        m.startswith(("ollama/", "lm_studio/", "hosted_vllm/", "openai/local"))
        or "localhost" in m
        or "127.0.0.1" in m
    )


def _cache_read_tokens(chunk_usage) -> int:
    """Prompt-cache HIT tokens — the cached subset of the input the provider billed at the
    (much cheaper) cache-read rate instead of full price. This is what tells you whether prompt
    caching is actually firing; ``cached / input`` is the hit rate.

    Providers report it in different shapes and LiteLLM only partly normalizes them, so try each
    known shape and take the first that carries a value:
      * OpenAI / Gemini (and LiteLLM's normalized form): ``prompt_tokens_details.cached_tokens``
      * Anthropic:                                       ``cache_read_input_tokens``
      * DeepSeek:                                        ``prompt_cache_hit_tokens``
    Returns 0 on a full miss / a provider that doesn't cache."""
    details = getattr(chunk_usage, "prompt_tokens_details", None)
    cached = None
    if details is not None:
        cached = getattr(details, "cached_tokens", None)
        if cached is None and isinstance(details, dict):
            cached = details.get("cached_tokens")
    if not cached:
        cached = getattr(chunk_usage, "cache_read_input_tokens", None)
    if not cached:
        cached = getattr(chunk_usage, "prompt_cache_hit_tokens", None)
    return int(cached or 0)


def _describe_chunk(chunk) -> str:
    """One line for one streamed chunk, for the log: what it finished with, and what it carried.

    `content=None, tool_calls=None` on a chunk that ALSO says `finish_reason='stop'` is the whole
    story of a completion that ended before it began — and that is invisible inside a repr.
    """
    choices = getattr(chunk, "choices", None) or []
    if not choices:
        usage = getattr(chunk, "usage", None)
        return f"usage-only ({usage})" if usage else "empty (no choices, no usage)"
    choice = choices[0]
    carried = [
        name
        for name in ("content", "reasoning_content", "tool_calls", "role")
        if getattr(getattr(choice, "delta", None), name, None)
    ]
    finish = getattr(choice, "finish_reason", None)
    return f"finish_reason={finish!r}, carried {'+'.join(carried) if carried else 'NOTHING'}"


async def litellm_stream(
    *,
    model: str,
    system_prompt: str,
    messages: list[Message],
    tools,
    abort: asyncio.Event,
    temperature: float | None = None,
    reasoning_effort: str = "off",
    idle_timeout_sec: float | None = None,
    request_timeout_sec: float | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Stream one model reply. Builds the request, streams chunks, assembles them
    into a final AssistantMessage, and yields progress events along the way.
    Never raises — errors/aborts become a "done" event with the right stop_reason."""
    import litellm  # imported lazily so importing this module is cheap/side-effect-free

    litellm.suppress_debug_info = True

    # Accumulators for the pieces we collect as the stream arrives:
    text_parts: list[str] = []  # visible text shards
    thinking_parts: list[str] = []  # reasoning shards
    acc = _ToolCallAccumulator()  # tool-call fragments
    usage: dict[str, int] = {}  # token counts (from the final usage-only chunk)
    finish_reason: str | None = None
    error_message: str | None = None
    aborted = False
    # Did the provider send a single `choices` entry? A stream that carries usage and nothing
    # else answers "yes it was billed" and "no it never spoke" at the same time — and until this
    # flag existed those two were indistinguishable from a clean, deliberate empty completion.
    saw_choice = False
    # WHAT ACTUALLY CAME BACK, kept only to explain a turn that produced nothing, and written
    # only to the LOG. A summary rather than a repr: `logprobs=None, audio=None,
    # system_fingerprint=None` and forty more dead fields bury the two that matter — did the chunk
    # finish, and did its delta carry anything.
    #
    # Bounded on purpose: a normal turn streams thousands of chunks and none are worth holding.
    chunk_count = 0
    chunk_notes: list[str] = []

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
        if request_timeout_sec:
            kwargs["request_timeout"] = request_timeout_sec  # hard ceiling per call

        # Local models (Ollama/LM Studio) legitimately go silent for minutes while
        # loading/thinking, so skip the network-silence idle watchdog for them.
        effective_idle = None if _is_local_provider(model) else idle_timeout_sec

        # --- Consume the stream chunk by chunk (idle-guarded) ---
        from agent_runtime.infrastructure.llm import model_proxy

        model_proxy.apply(kwargs)  # platform-keys mode: route via our proxy (no-op if off)
        response = await litellm.acompletion(**kwargs)
        it = response.__aiter__()
        while True:
            if abort.is_set():  # user cancelled mid-stream -> stop reading
                aborted = True
                break
            try:
                chunk = (
                    await asyncio.wait_for(it.__anext__(), timeout=effective_idle)
                    if effective_idle
                    else await it.__anext__()
                )
            except StopAsyncIteration:
                break
            except TimeoutError:  # no chunk for too long -> treat as a hang
                error_message = f"LLM idle timeout after {effective_idle}s (no response)"
                break

            chunk_count += 1
            if len(chunk_notes) < 6:
                chunk_notes.append(_describe_chunk(chunk))

            # token usage usually arrives in a final chunk (because include_usage=True)
            chunk_usage = getattr(chunk, "usage", None)
            if chunk_usage:
                usage = {
                    "input": getattr(chunk_usage, "prompt_tokens", 0) or 0,
                    "output": getattr(chunk_usage, "completion_tokens", 0) or 0,
                    # cache-READ (hit) tokens: the cached subset of `input`. cached/input = hit
                    # rate — how much of the re-sent context the provider served from cache
                    # instead of re-billing at full price. 0 => caching isn't firing this turn.
                    "cached": _cache_read_tokens(chunk_usage),
                }

            choices = getattr(chunk, "choices", None) or []
            if not choices:
                continue  # the usage-only final chunk has no choices
            saw_choice = True
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

    # HOSTED metering: fold this call's tokens+cost into the turn's per-account accumulator.
    # No-op outside an account-scoped turn (desktop/local), so the hot path is unaffected.
    if usage:
        from agent_runtime.infrastructure import accounts as _accts

        _accts.add_usage(model, usage.get("input", 0), usage.get("output", 0))

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

    # NOTHING GENERATED IS NOT A SUCCESSFUL ANSWER.
    #
    # A turn that produced no text, no reasoning and no tool calls, on a request the provider
    # BILLED for, is a failed generation however the provider labelled it. Two shapes reach here:
    #
    #   * the stream carried a usage chunk and no `choices` at all — `finish_reason` is then never
    #     set, and the mapping below reads `finish_reason or "stop"`;
    #   * a `choices` entry arrived with an empty delta and `finish_reason: "stop"`, and usage
    #     reports zero completion tokens.
    #
    # BOTH used to be recorded as a clean stop, and the run then told the user "the model returned
    # an entirely empty response" — blaming the model for what is nearly always an upstream
    # refusal: an exhausted balance, a dead or unfunded key, a gateway declining this model.
    #
    # The second shape was originally left alone here, on the theory that a model may legitimately
    # choose to say nothing. It cannot: choosing to say nothing still costs completion tokens, and
    # an agent turn with no text AND no tool call has not answered by any definition. `output == 0`
    # is the honest discriminator, not the label the provider attached.
    produced_nothing = not text_parts and not thinking_parts and not tool_calls
    generated_nothing = bool(usage) and not usage.get("output")
    if error_message is None and not aborted and produced_nothing and (generated_nothing or not saw_choice):
        # TWO AUDIENCES, TWO MESSAGES — and conflating them is how a chat window ended up
        # showing `finish_reason='stop', 2 chunks` and a raw ModelResponseStream dump to somebody
        # who just typed "hi".
        #
        # The chat gets ONE plain sentence: what happened, whose fault it is, and what to do. No
        # token counts, no field names, no provider internals. Someone reading their own
        # conversation is not debugging our stream parser.
        #
        # The log gets everything, because that is where a developer looks and where the detail
        # is worth its noise.
        error_message = (
            "The model returned an empty response - nothing at all, not even an error. "
            "That is a fault on the provider's side, not something wrong with your message "
            "or your settings. Try a different model."
        )
        log.warning(
            "empty completion from %s: %d chunk(s), usage=%s, finish_reason=%r; chunks: %s",
            model,
            chunk_count,
            usage or {},
            finish_reason,
            " | ".join(chunk_notes) or "(none received)",
        )

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
