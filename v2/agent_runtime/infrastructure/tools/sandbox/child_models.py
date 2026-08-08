"""The CHILD half of the model broker: `text_complete` that asks the host instead of dialling out.

A plugin author writes the same call they always wrote:

    from agent_runtime.infrastructure.llm.oneshot import text_complete
    narration = await asyncio.to_thread(text_complete, model=m, prompt=p, max_tokens=220)

Inside the sandbox that name resolves to the shim below, which sends a `model_request` frame to the
parent and blocks until the answer comes back. Identical signature, identical return type. A plugin
that works unsandboxed works sandboxed, which is what makes it safe to make the sandbox the default
— an isolation mode that requires plugin authors to write different code is one they will avoid.

THE STUB IS INSTALLED IN ``sys.modules``, NOT MONKEYPATCHED ONTO THE REAL MODULE. Two reasons, and
the second is the bigger one:

  * ``from X import f`` binds at import time. Patching attributes on an already-imported module
    would still be correct here, but pre-seeding ``sys.modules`` means the real module is never
    imported at all — so it also cannot be reached around by importing it again.
  * The real ``oneshot`` imports litellm, which is heavy. Never importing it keeps child startup
    to the interpreter plus the plugin, which matters when the cost is paid per tool call.

THREADS, NOT ASYNCIO. Responses arrive on stdin, read by a daemon thread, and the shim blocks on a
``threading.Event``. That works whether the plugin calls it from the event loop or from a worker
thread via ``asyncio.to_thread`` — the usual case, since these functions are synchronous by
contract. Wrapping stdin in an asyncio stream would have been the other option and is not portable
to Windows for a non-socket handle.
"""

from __future__ import annotations

import json
import sys
import threading
import types

#: Hard ceiling on how long the child waits for the host. The host applies its OWN, shorter
#: per-call timeout; this exists only so a lost response cannot hang the child forever — which
#: would present as a tool that never returns, the least debuggable failure there is.
RESPONSE_TIMEOUT_S = 900.0

_MODULE = "agent_runtime.infrastructure.llm.oneshot"


class ModelBridge:
    """Sends model requests to the host and matches the answers back to their callers."""

    def __init__(self, send) -> None:
        self._send = send  # callable(dict) -> None, writes one frame on the protocol stream
        self._pending: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._next_id = 0

    def request(self, kind: str, payload: dict) -> str:
        """Send one request and BLOCK until the host answers. -> the completion text."""
        with self._lock:
            self._next_id += 1
            request_id = f"m{self._next_id}"
            slot = {"event": threading.Event(), "response": None}
            self._pending[request_id] = slot
        self._send({"t": "model_request", "id": request_id, "kind": kind, **payload})
        if not slot["event"].wait(RESPONSE_TIMEOUT_S):
            with self._lock:
                self._pending.pop(request_id, None)
            raise RuntimeError("the sandbox host did not answer the model request in time")
        response = slot["response"] or {}
        if not response.get("ok"):
            # Surfaced as an ordinary exception, because that is what a failed model call looks
            # like to a plugin already — its existing error handling keeps working.
            raise RuntimeError(str(response.get("error") or "the model call was refused"))
        return str(response.get("text") or "")

    def deliver(self, payload: dict) -> None:
        """Called by the stdin reader thread when a `model_response` arrives."""
        with self._lock:
            slot = self._pending.pop(str(payload.get("id") or ""), None)
        if slot is not None:
            slot["response"] = payload
            slot["event"].set()

    def fail_all(self, reason: str) -> None:
        """Release every waiter — the host is gone and no answer is coming."""
        with self._lock:
            slots, self._pending = list(self._pending.values()), {}
        for slot in slots:
            slot["response"] = {"ok": False, "error": reason}
            slot["event"].set()


def install(bridge: ModelBridge) -> None:
    """Pre-seed `sys.modules` with a broker-backed `oneshot`. Call BEFORE the plugin is loaded."""
    stub = types.ModuleType(_MODULE)
    stub.__doc__ = "Sandboxed stand-in for oneshot: model calls are served by the host process."

    def text_complete(*, model: str, prompt: str, max_tokens=None, api_key=None, timeout=None) -> str:
        # `api_key` is accepted and IGNORED, deliberately. The signature has to match or a plugin
        # passing it would TypeError; honouring it would mean a sandboxed plugin could supply its
        # own credential and make calls nobody is metering.
        return bridge.request(
            "text", {"model": model, "prompt": prompt, "max_tokens": max_tokens}
        )

    def vision_complete(
        *, model: str, prompt: str, image_paths, want_json: bool = False, api_key=None, timeout=None
    ) -> str:
        paths = list(image_paths) if isinstance(image_paths, (list, tuple)) else [image_paths]
        return bridge.request(
            "vision",
            {"model": model, "prompt": prompt, "image_paths": [str(p) for p in paths],
             "want_json": bool(want_json)},
        )

    def normalize_model(model: str) -> str:
        """Kept as identity: the HOST normalizes, because the host is what will make the call."""
        return model

    stub.text_complete = text_complete
    stub.vision_complete = vision_complete
    stub.normalize_model = normalize_model
    sys.modules[_MODULE] = stub


def start_reader(stream, bridge: ModelBridge, on_unknown=None) -> threading.Thread:
    """Read `model_response` frames off the child's stdin, forever, on a daemon thread.

    A daemon thread so a plugin that leaves work behind cannot keep the child alive: the process
    exits when the tool call is done, answered or not.
    """

    def run() -> None:
        try:
            while True:
                line = stream.readline()
                if not line:
                    break
                try:
                    payload = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(payload, dict):
                    continue
                if payload.get("t") == "model_response":
                    bridge.deliver(payload)
                elif on_unknown is not None:
                    on_unknown(payload)
        except (OSError, ValueError):
            pass
        finally:
            # stdin closed => the parent is gone or finished. Anyone still waiting must be told,
            # or they block until RESPONSE_TIMEOUT_S for no reason.
            bridge.fail_all("the sandbox host closed the channel")

    thread = threading.Thread(target=run, name="agentd-sandbox-host-reader", daemon=True)
    thread.start()
    return thread
