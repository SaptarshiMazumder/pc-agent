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
import hashlib
import logging
import time
import traceback
from collections.abc import AsyncIterator, Awaitable, Callable
from typing import Any, Protocol

from agent_runtime.application.interfaces.run_observer import RunObserver, ToolEvent
from agent_runtime.domain.events import AgentEvent, EventCallback
from agent_runtime.domain.messages import (
    Artifact,
    AssistantMessage,
    Message,
    TextContent,
    ToolCallContent,
    ToolResultMessage,
    UserMessage,
    message_to_dict,
)
from agent_runtime.infrastructure import telemetry
from agent_runtime.infrastructure.files import resolve_artifacts
from agent_runtime.infrastructure.llm import context_limits
from agent_runtime.infrastructure.memory.local_store import SessionStore
from agent_runtime.infrastructure.tools import Tool, ToolArgError, ToolResult, validate_args

from .incomplete_turn import (
    INCOMPLETE_TURN_FALLBACK_TEXT,
    MAX_BEFORE_AGENT_FINALIZE_REVISIONS,
    RETRY_INSTRUCTIONS,
    RETRY_LIMITS,
    PlanningContext,
    build_before_finalize_retry_prompt,
    classify_incomplete_turn,
    describe_empty_run,
    is_injected_prompt,
    resolve_max_run_loop_iterations,
)


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


# After this many liveness halts in a run without recovery, stop (safety backstop).
STUCK_CAP = 3

#: "the caller said nothing" — as opposed to `None`, which is a caller SAYING "no router".
#:
#: They are different answers and the engine must not confuse them: an agent with cost-efficiency
#: switched off resolves to None, and treating that as "unspecified" silently reinstated the
#: daemon's router. See AgentEngine.run.
_UNSET = object()




def _notify_tool(observers, ev: ToolEvent) -> list[str]:
    out = []
    for o in observers:
        try:
            r = o.on_tool(ev)
        except Exception:  # noqa: BLE001 — an observer must never break the run
            r = None
        if r:
            out.append(r)
    return out


def _notify_turn(observers, index: int) -> list[str]:
    out = []
    for o in observers:
        try:
            r = o.on_turn(index)
        except Exception:  # noqa: BLE001
            r = None
        if r:
            out.append(r)
    return out


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
    execution_contract: str = "",
    get_steering_messages: FollowUpFn | None = None,
    get_follow_up_messages: FollowUpFn | None = None,
    verify_answer: VerifyFn | None = None,
    observers: list[RunObserver] | None = None,
    context_policy=None,
    model_router=None,
    model_trace: bool = False,
) -> list[Message]:
    """Run the loop until the model produces a genuine final answer (or limits
    are hit). Mutates `messages` in place; returns only messages produced here."""
    tool_map = {t.name: t for t in tools}
    new_messages: list[Message] = []
    stop_reason = "stop"
    error_text: str | None = None  # human-readable reason when stop_reason == "error"
    max_iters = max_iterations or resolve_max_run_loop_iterations(1)

    retry_counts = {k: 0 for k in RETRY_LIMITS}
    finalize_revisions = 0
    produced_visible_text = False
    # Why a run ended empty, and who was actually answering when it did. Carried to the end
    # so the closing message can NAME the failure instead of apologising for it.
    incomplete_kind: str | None = None
    served_by: tuple[str, str] | None = None  # (from, to) when failover took over
    # The user's triggering request — used to gate the planning-only nudge (OpenClaw: only fire
    # it when the user asked the agent to ACT).
    #
    # Injected messages are SKIPPED. Retry nudges and liveness steering are persisted as
    # UserMessages (that is how the model receives them), so on the next run the newest "user
    # message" is the runtime's own last nudge — which does not read as a request to act. The
    # guard then refused to nudge, meaning the recovery layer disabled itself permanently after
    # its first use. Reading past them restores the real question: what did the HUMAN ask for?
    user_prompt = next(
        (
            mm.content
            for mm in reversed(messages)
            if isinstance(mm, UserMessage) and not is_injected_prompt(mm.content)
        ),
        "",
    )

    # --- decoupled liveness seam (default off => unchanged behavior) ---
    observers = observers or []
    for o in observers:
        o.reset()
    stuck_halts = 0

    def persist(m: Message) -> None:
        messages.append(m)
        new_messages.append(m)
        if session is not None:
            session.append(m)

    def inject(text: str) -> None:
        persist(UserMessage(content=text))

    async def handle_halts(halts: list[str]) -> bool:
        """A liveness observer flagged the run as stuck. Inject a steering message
        and continue; after STUCK_CAP unrecovered halts, signal the loop to stop."""
        nonlocal stuck_halts
        halts = [h for h in halts if h]
        if not halts:
            return False
        stuck_halts += 1
        await on_event(AgentEvent("continuation", {"reason": "stuck", "attempt": stuck_halts}))
        if stuck_halts > STUCK_CAP:
            return True  # repeated nudges ignored -> stop (safety backstop)
        inject("[liveness] " + " ".join(dict.fromkeys(halts)))
        return False

    await on_event(AgentEvent("agent_start", {}))
    try:
        # Steering messages injected before the first turn (OpenClaw outer-loop start).
        if get_steering_messages is not None:
            for m in await _maybe_await(get_steering_messages()) or []:
                persist(m)

        # THE CLOCKS (plan 3.2). "It's slow" is unactionable until you know WHICH part was slow,
        # and the split that matters is model time vs tool time — they have completely different
        # fixes (change the model / fix the tool). Both halves are emitted; the split itself is a
        # query, because summing `tool_duration_ms` by run_id already answers the tool side and
        # threading an accumulator through the tool executor would buy nothing.
        _run_t0 = time.perf_counter()
        _first_output_ms: float | None = None
        _first_text_ms: float | None = None
        _model_time_ms = 0.0

        iterations = 0
        while iterations < max_iters:
            iterations += 1
            # ONE message can be many model turns (think -> tool -> think -> answer). Numbering
            # them is what lets "turn 4 of run abc was the slow one" be a question you can ask.
            telemetry.bind(turn_id=f"{telemetry.get().get('run_id', 'run')}-{iterations}")
            await on_event(AgentEvent("turn_start", {}))
            await on_event(AgentEvent("message_start", {"role": "assistant"}))

            assistant: AssistantMessage | None = None
            # compact the model's VIEW of history if a policy is set (never mutates the
            # real transcript; default None => send everything, unchanged).
            send_messages = context_policy.prepare(messages) if context_policy else messages
            # Cost-efficiency routing (default off => active_model is just `model`): pick the brain
            # per iteration by NEED — a cheap text model normally, a vision model only when the
            # OUTGOING context actually carries an image the brain must see (see infrastructure/llm/
            # model_router.py). The chosen model is what litellm records on the turn.
            active_model = model_router(model, send_messages) if model_router else model
            _turn_t0 = time.perf_counter()
            async for ev in stream_fn(
                model=active_model,
                system_prompt=system_prompt,
                messages=send_messages,
                tools=tools,
                abort=abort,
            ):
                kind = ev.get("type")
                if kind in ("text_delta", "thinking_delta"):
                    # FIRST OUTPUT is the number a user actually judges us on — the gap between
                    # pressing send and the screen changing. Measured from the top of the loop,
                    # so it includes prompt assembly and the provider's own time to first byte.
                    # Recorded once per run; `_first_text_ms` separates real answer text from
                    # thinking, because a wall of reasoning is not the same as an answer.
                    # NOT the same as "first token of the FINAL answer" — which turn turns out to
                    # be final is only known once the loop ends, so that one needs a retroactive
                    # pass and is deliberately not built.
                    if _first_output_ms is None:
                        _first_output_ms = (time.perf_counter() - _run_t0) * 1000
                        telemetry.timing("first_output_ms", _first_output_ms, source="stream")
                    if _first_text_ms is None and kind == "text_delta":
                        _first_text_ms = (time.perf_counter() - _run_t0) * 1000
                        telemetry.timing("first_text_ms", _first_text_ms, source="stream")
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
                elif kind == "fallback":
                    # The configured model could not serve this turn and another one took
                    # over (infrastructure/llm/failover.py). Surfaced as a first-class event
                    # so every client can say so — the run is fine, but the user is no longer
                    # talking to the model they chose, and that is not a log-file fact.
                    # (failover.py already logged this with the provider's own error text —
                    # what is missing is telling the USER, which is what the event does.)
                    served_by = (ev.get("from", ""), ev.get("to", ""))
                    await on_event(
                        AgentEvent(
                            "model_fallback",
                            {
                                "from": ev.get("from", ""),
                                "to": ev.get("to", ""),
                                "reason": ev.get("reason", ""),
                            },
                        )
                    )
                elif kind == "done":
                    assistant = ev["message"]

            # Per-turn model time. The interesting comparison is against tool_duration_ms on the
            # same run: a run that spent 40 s in the model needs a different model, a run that
            # spent 40 s in tools needs a faster tool.
            _turn_model_ms = (time.perf_counter() - _turn_t0) * 1000
            _model_time_ms += _turn_model_ms
            telemetry.timing("model_stream_ms", _turn_model_ms, source="stream")

            if assistant is None:  # defensive: stream ended without a done event
                assistant = AssistantMessage(
                    stop_reason="error", error_message="stream ended without result"
                )
            persist(assistant)
            if assistant.text.strip():
                produced_visible_text = True
            await on_event(AgentEvent("message_end", {"message": message_to_dict(assistant)}))
            # HOW FULL THE CONTEXT IS, after every assistant message and always on.
            #
            # `usage["input"]` is what the provider actually BILLED for the request that produced
            # this message — not an estimate, and better than any tokeniser we could run. The
            # limit comes from the model's own table. Together they are the number that explains
            # the failure nobody can currently see: a conversation that outgrows its model returns
            # an EMPTY response, the incomplete-turn retry appends another message and re-sends,
            # and the user watches the same shrug twice.
            #
            # Silent when either half is unknown. An unknown model must render no meter rather
            # than a wrong one — a guessed denominator would show a full bar on an empty chat.
            usage_in = int((assistant.usage or {}).get("input") or 0)
            served_model = getattr(assistant, "model", "") or active_model
            limit = context_limits.max_input_tokens(served_model)
            if usage_in and limit:
                await on_event(
                    AgentEvent(
                        "context_usage",
                        {
                            "used": usage_in,
                            "limit": limit,
                            # Precomputed so every client agrees on the number, rather than three
                            # windows each rounding it their own way.
                            "pct": round(usage_in / limit, 4),
                            "model": served_model,
                            # The cached subset of `used`. Not a second meter — it is why a large
                            # context can still be cheap, and without it a user reading "180k used"
                            # has no way to tell an expensive turn from a mostly-cached one.
                            "cached": int((assistant.usage or {}).get("cached") or 0),
                        },
                    )
                )
            # observability (default off => silent): which brain ran THIS step + its token usage, so a
            # client can show the per-step model/cost trail (e.g. deepseek -> gemini -> deepseek).
            if model_trace:
                u = assistant.usage or {}
                await on_event(
                    AgentEvent(
                        "model_trace",
                        {
                            "step": iterations,
                            # WHO ANSWERED, not who we asked. These differ whenever failover
                            # fired, and reporting only the request made a dead primary look
                            # like it was serving every turn — the trace said 'openai/gpt-5'
                            # while gemini-2.5-flash was actually producing the (empty) replies.
                            "model": getattr(assistant, "model", "") or active_model,
                            # kept alongside, because the GAP between the two is the signal:
                            # requested != model means a fallback is carrying the run.
                            "requestedModel": active_model,
                            "tokensIn": int(u.get("input") or 0),
                            "tokensOut": int(u.get("output") or 0),
                            # cache-read subset of tokensIn; tokensCached/tokensIn = cache hit rate
                            "tokensCached": int(u.get("cached") or 0),
                        },
                    )
                )

            if abort.is_set() or assistant.stop_reason == "aborted":
                stop_reason = "aborted"
                await on_event(AgentEvent("turn_end", {}))
                break

            tool_calls = assistant.tool_calls
            if tool_calls and assistant.stop_reason not in ("error", "aborted"):
                results, tool_halts = await _execute_tool_calls(
                    tool_calls, tool_map, abort, on_event, observers
                )
                for r in results:  # assistant source order
                    persist(r)
                    await on_event(AgentEvent("message_end", {"message": message_to_dict(r)}))
                await on_event(AgentEvent("turn_end", {}))
                if abort.is_set():
                    stop_reason = "aborted"
                    break
                # liveness: per-tool halts + per-turn check (default off => empty)
                if await handle_halts(tool_halts + _notify_turn(observers, iterations)):
                    stop_reason = "stuck"
                    break
                continue  # back to the model with tool results

            # --- No tool calls: the turn would normally end. Decide if it's complete. ---
            await on_event(AgentEvent("turn_end", {}))

            # liveness: tick the turn (a no-tool turn isn't "grinding"; usually a no-op)
            no_tool_halts = _notify_turn(observers, iterations)
            if no_tool_halts:
                if await handle_halts(no_tool_halts):
                    stop_reason = "stuck"
                    break
                continue

            if assistant.stop_reason in ("error",):
                stop_reason = "error"
                error_text = assistant.error_message
                break

            # 1. Typed incomplete-turn retries (planning/reasoning/empty). planning_only is
            #    gated (OpenClaw): only an agentic task-runner on an actionable prompt gets it.
            kind = classify_incomplete_turn(
                assistant,
                PlanningContext(
                    user_prompt=user_prompt,
                    model=active_model,
                    execution_contract=execution_contract,
                ),
            )
            if kind is not None:
                # remember WHY, even on the attempt that exhausts the budget — this is the
                # only place the run knows what went wrong, and without it the ending is a
                # shrug ("couldn't generate a response") instead of a diagnosis.
                incomplete_kind = kind
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

            # 3. Before-finalize revision hook (legacy callable seam).
            if (
                verify_answer is not None
                and finalize_revisions < MAX_BEFORE_AGENT_FINALIZE_REVISIONS
            ):
                reason = await _maybe_await(verify_answer(messages))
                if reason:
                    finalize_revisions += 1
                    await on_event(
                        AgentEvent(
                            "continuation", {"reason": "revision", "attempt": finalize_revisions}
                        )
                    )
                    inject(build_before_finalize_retry_prompt(reason))
                    continue

            # (Answer verification is now the agent-invoked `verify_answer` TOOL — it is
            # NOT a loop hook. The loop knows nothing about it.)

            # Genuinely done.
            stop_reason = assistant.stop_reason
            break
        else:
            stop_reason = "length"  # iteration cap exhausted
    except asyncio.CancelledError:
        abort.set()
        await on_event(AgentEvent("agent_end", {"stopReason": "aborted"}))
        raise

    # Total model time for the run. Emitted here rather than in a `finally` on purpose: an
    # aborted run re-raises above, and a partial total would poison the percentile with runs that
    # were cut off. `run_duration_ms` in the gateway already covers the aborted case.
    telemetry.timing("model_time_ms", _model_time_ms, source="stream")

    # A run that produced NO user-visible answer. Say what actually happened: "couldn't
    # generate a response, please try again" is advice to repeat something that will fail the
    # same way, and it hid a dead API key behind days of retrying. The two facts that explain
    # essentially every empty run are WHICH failure mode repeated (the retry classifier
    # already knows) and WHETHER a fallback model was carrying the run.
    if not produced_visible_text and stop_reason not in ("aborted",):
        # ONLY replace the generic "stop". `error`, `length` and friends already say
        # something specific and truer — overwriting them with `no_output` would be trading a
        # precise cause for a vague symptom, which is the whole failure this change exists to
        # undo. `stop` is the only value that actively claims success it did not achieve.
        if stop_reason == "stop":
            stop_reason = "no_output"
        detail = describe_empty_run(incomplete_kind, served_by)
        persist_text = f"{INCOMPLETE_TURN_FALLBACK_TEXT}\n\n{detail}" if detail else (
            INCOMPLETE_TURN_FALLBACK_TEXT
        )
        fallback = AssistantMessage(
            content=[TextContent(text=persist_text)], stop_reason=stop_reason
        )
        persist(fallback)
        # STREAM it, then end it. Clients build the transcript from `text_delta`; `message_end`
        # only closes the streaming state (see clients/ui store.ts). A message announced solely
        # via message_end is therefore persisted, logged — and invisible, which is exactly the
        # silence this whole change exists to remove. Emitting it the way a real answer arrives
        # makes every client render it with no client-side change at all.
        await on_event(
            AgentEvent("message_update", {"kind": "text_delta", "delta": persist_text})
        )
        await on_event(AgentEvent("message_end", {"message": message_to_dict(fallback)}))
        logging.getLogger("agentd").warning(
            "run produced no visible output — %s", detail or "no diagnosis available"
        )

    end_payload = {"stopReason": stop_reason}
    if error_text:
        end_payload["error"] = error_text
    await on_event(AgentEvent("agent_end", end_payload))
    return new_messages


async def _execute_tool_calls(
    tool_calls: list[ToolCallContent],
    tool_map: dict[str, Tool],
    abort: asyncio.Event,
    on_event: EventCallback,
    observers: list[RunObserver] | None = None,
) -> tuple[list[ToolResultMessage], list[str]]:
    """Execute one assistant turn's tool calls.

    Parallel-capable tools run concurrently; sequential tools run in source
    order after the parallel batch. Results are returned in source order. Also
    notifies liveness observers before/after each call and returns any halt
    reasons they raised (empty when no observers are configured).
    """
    observers = observers or []
    results: dict[int, ToolResultMessage] = {}
    halts: list[str] = []

    async def run_one(index: int, call: ToolCallContent) -> None:
        tool = tool_map.get(call.name)
        halts.extend(_notify_tool(observers, ToolEvent(call.name, call.arguments, "before")))
        await on_event(
            AgentEvent(
                "tool_execution_start",
                {"toolCallId": call.id, "toolName": call.name, "args": call.arguments},
            )
        )

        # Forward a tool's incremental progress (GuardedTool retries/timeouts, the
        # computer tool's per-step updates) as tool_progress events. Sync callback
        # (the contract type); the async emit is scheduled fire-and-forget.
        def _on_update(update) -> None:
            if isinstance(update, str):
                text = update
            else:  # a ToolResult — join its text blocks
                text = "".join(getattr(b, "text", "") for b in getattr(update, "content", []))
            asyncio.create_task(
                on_event(
                    AgentEvent(
                        "tool_progress",
                        {"toolCallId": call.id, "toolName": call.name, "text": text},
                    )
                )
            )

        # THE single choke point for every tool in the system — built-in, plugin, MCP,
        # sandboxed, agent-private, third-party marketplace. Timing it here means every tool
        # ever published is instrumented without its author doing anything, and it is what
        # separates "the model is slow" (their problem) from "our tools are slow" (ours).
        # NOTE the tool NAME is a property, never a dimension: with a public marketplace it is
        # unbounded, and unbounded dimensions are billed per distinct value.
        _tool_started = time.perf_counter()
        _tool_outcome = "ok"
        if tool is None:
            result = ToolResult.text(f"Unknown tool: {call.name}", is_error=True)
            _tool_outcome = "unknown"
        else:
            try:
                args = validate_args(tool, call.arguments)
                result = await tool.execute(call.id, args, abort, _on_update)
                if getattr(result, "is_error", False):
                    _tool_outcome = "error"
            except ToolArgError as e:
                result = ToolResult.text(str(e), is_error=True)
                _tool_outcome = "bad_args"
            except asyncio.CancelledError:
                telemetry.timing(
                    "tool_duration_ms", (time.perf_counter() - _tool_started) * 1000,
                    outcome="aborted", _props={"tool": call.name},
                )
                raise
            except Exception:
                result = ToolResult.text(
                    f"Tool '{call.name}' failed:\n{traceback.format_exc(limit=4)}",
                    is_error=True,
                )
                _tool_outcome = "exception"
        telemetry.timing(
            "tool_duration_ms", (time.perf_counter() - _tool_started) * 1000,
            outcome=_tool_outcome, _props={"tool": call.name},
        )
        telemetry.count("tool_call_total", outcome=_tool_outcome, _props={"tool": call.name})
        # DELIVERABLES: a producing tool declares the file(s) it made via result.artifacts;
        # resolve each to a typed artifact (skips non-existent/dupes). Nothing is inferred from text.
        declared = [
            Artifact(**info) for info in resolve_artifacts(getattr(result, "artifacts", None))
        ]
        msg = ToolResultMessage(
            tool_call_id=call.id,
            tool_name=call.name,
            content=result.content,
            is_error=result.is_error,
            artifacts=declared,
        )
        results[index] = msg
        rtext = "".join(getattr(b, "text", "") for b in result.content)
        digest = hashlib.sha1(rtext.encode("utf-8", "ignore")).hexdigest()[:12] if rtext else None
        halts.extend(
            _notify_tool(
                observers,
                ToolEvent(
                    call.name,
                    call.arguments,
                    "after",
                    is_error=result.is_error,
                    result_digest=digest,
                ),
            )
        )
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

    return [results[i] for i in sorted(results)], halts


class NativeEngine:
    """Our hand-rolled reason->act loop, wrapped as a swappable AgentEngine.

    Holds the LLM stream function + model id; ``run`` just delegates to
    ``run_agent_loop`` (the function above). Alternative engines (Claude Agent SDK,
    LangGraph) would be sibling classes implementing this same ``run`` shape — the
    application's AgentService calls whichever one it was given, none the wiser.
    """

    def __init__(
        self,
        stream_fn,
        model: str,
        max_iterations: int | None = None,
        observers=None,
        context_policy=None,
        execution_contract: str = "",
        model_router=None,
        model_trace: bool = False,
    ):
        self._stream_fn = stream_fn  # the LLMService (e.g. litellm_stream)
        self._model = model  # which model id to pass each call
        self._max_iterations = max_iterations
        self._observers = observers or []  # decoupled liveness seam (default off)
        self._context_policy = context_policy  # compaction policy (S7); None = send all
        self._execution_contract = execution_contract  # gates the planning-only nudge (OpenClaw)
        self._model_router = model_router  # cost-efficiency brain routing (default off => None)
        self._model_trace = model_trace  # emit per-step model_trace events (default off)

    async def run(
        self,
        *,
        messages,
        system_prompt,
        tools,
        on_event,
        abort,
        session=None,
        model=None,
        model_router=_UNSET,
    ):
        """``model_router`` is the per-agent counterpart of ``model``, and it exists because
        without it ``model`` did not actually work.

        The router OVERWRITES the model on every turn (see run_agent_loop). While it was built
        once at boot from the daemon's config and held here, an agent that named its own model
        had that choice silently discarded the moment cost-efficiency was on anywhere. Passing
        the agent's own router alongside its own model is what makes the pair coherent.

        `_UNSET`, NOT `None`, AND THAT DISTINCTION IS THE WHOLE POINT. `router_for()` answers
        None for an agent that has cost-efficiency switched OFF — a decision, not an absence. This
        used to read `model_router or self._model_router`, which cannot tell the two apart: an
        agent that had explicitly turned routing off fell through to the DAEMON's router, which
        was on. So the off switch silently re-enabled the very thing it was switched off, the
        agent's chosen model was overwritten every turn by the daemon's cheap one, and when that
        model had no credit every run failed with an error naming a model the user never picked.

        A caller that passes nothing at all — a sub-agent run, a tool driving the engine directly
        — still gets the engine default, which is what `_UNSET` preserves."""
        return await run_agent_loop(
            messages=messages,
            system_prompt=system_prompt,
            tools=tools,
            stream_fn=self._stream_fn,
            model=model or self._model,  # per-agent override, else the engine default
            on_event=on_event,
            abort=abort,
            session=session,
            max_iterations=self._max_iterations,
            observers=self._observers,
            context_policy=self._context_policy,
            execution_contract=self._execution_contract,
            model_router=self._model_router if model_router is _UNSET else model_router,
            model_trace=self._model_trace,
        )
