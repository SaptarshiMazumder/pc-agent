"""Model failover — try a fallback model when the primary errors out (Phase / S11).

Wraps a stream_fn (e.g. litellm_stream). On a CLEAN failure — the model ends with
stop_reason "error" before producing ANY output — it retries the turn on the next
candidate model. If the model already streamed output, the error is passed through
(retrying would duplicate). No fallbacks => the inner stream is returned unwrapped, so
behavior is unchanged by default.
"""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def make_failover_stream(inner, fallbacks):
    fallbacks = [f for f in (fallbacks or []) if f]
    if not fallbacks:
        return inner  # nothing to fail over to -> unchanged

    async def stream(*, model, system_prompt, messages, tools, abort):
        candidates = [model] + [f for f in fallbacks if f != model]
        for i, m in enumerate(candidates):
            produced = False  # did THIS attempt stream anything yet?
            done_ev = None
            async for ev in inner(
                model=m, system_prompt=system_prompt, messages=messages, tools=tools, abort=abort
            ):
                if ev.get("type") == "done":
                    done_ev = ev
                    break
                produced = True  # any pre-done event = output started
                yield ev
            if done_ev is None:
                return
            msg = done_ev.get("message")
            stop = getattr(msg, "stop_reason", None)
            if stop == "error" and not produced and i < len(candidates) - 1:
                # MONITOR: primary model failed -> falling back. One clear, greppable line,
                # WITH the underlying provider error so you can see WHY it failed.
                reason = getattr(msg, "error_message", None) or "no detail"
                log.warning(
                    "MODEL FALLBACK: '%s' errored before output -> falling back to '%s'"
                    "  | reason: %s",
                    m,
                    candidates[i + 1],
                    reason,
                )
                continue  # clean error + a fallback left -> try next, suppress this
            yield done_ev
            return

    return stream
