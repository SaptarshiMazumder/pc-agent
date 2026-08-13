"""The model broker — the HOST side of a sandboxed tool's model call.

A sandboxed tool has no network and no credentials, which is the whole point of the sandbox and
also the reason it could not call a model at all. Rather than weaken the grant, the call is
INVERTED: the tool asks, the host performs. This module is the answering half.

    child   {"t":"model_request","id":"m1","kind":"text","model":"…","prompt":"…"}
    host    -> check the grant -> clamp -> spend the caller's budget -> call -> meter
    child   {"t":"model_response","id":"m1","ok":true,"text":"…"}

WHAT THIS BUYS BEYOND "IT WORKS":

  * The credential never crosses the boundary, so the tool cannot keep it, reuse it, or leak it.
  * `net_allowlist` stays empty — the strong version of the sandbox's guarantee survives intact.
  * It works the same in hosted, BYOK and local-model deployments, because the side that knows HOW
    to reach a model is the side that does it. A key handed into the sandbox would only ever have
    worked in hosted mode.
  * Spend is metered against the account that is running the agent, attributed to the plugin, and
    capped — see below.

COST IS THE REALISTIC FAILURE MODE, not compromise. A compromised plugin can waste money; a merely
BUGGY one in a retry loop does exactly the same thing and is far more likely. Both are bounded by
the same three checks: the model must be on the grant, the call count per run is capped, and the
output-token ceiling is clamped. All three are config-driven, none is hardcoded.

EVERY REFUSAL RETURNS A MESSAGE, never a hang and never a bare failure. A plugin author who gets
"not granted" can act on it; one whose call silently returns nothing cannot.
"""

from __future__ import annotations

import asyncio
import logging
import time

from agent_runtime.domain.sandbox import CapabilityGrant
from agent_runtime.infrastructure import telemetry

log = logging.getLogger("agentd")

#: Ceilings for ONE tool run, overridable via `config.sandbox_model_limits`. Generous but finite:
#: a limit that trips on legitimate work gets switched off wholesale, which protects nothing.
DEFAULT_MODEL_LIMITS = {
    "max_calls": 8,  # model calls per tool invocation
    "max_output_tokens": 4096,  # clamp on what a single call may generate
    "timeout_s": 120.0,  # per-call wall clock
}

#: The request kinds the broker serves. Adding one is adding a key here plus a handler — the child
#: cannot invent a kind, because an unknown kind is refused.
TEXT = "text"
VISION = "vision"
IMAGE = "image"  # image GENERATION: prompt (+ granted reference paths) -> a file at a granted path


class SandboxModelBroker:
    """Serves the model requests of ONE sandboxed tool run. Not shared between runs: the call
    budget is per-invocation, so the counter and the run are the same lifetime."""

    def __init__(self, config, *, plugin_id: str, tool_name: str, grant: CapabilityGrant) -> None:
        self._config = config
        self._plugin_id = plugin_id
        self._tool_name = tool_name
        self._grant = grant
        limits = dict(DEFAULT_MODEL_LIMITS)
        limits.update(dict(getattr(config, "sandbox_model_limits", None) or {}))
        self._max_calls = int(limits.get("max_calls") or 0)
        self._max_output_tokens = int(limits.get("max_output_tokens") or 0)
        self._timeout_s = float(limits.get("timeout_s") or 0) or DEFAULT_MODEL_LIMITS["timeout_s"]
        self._calls = 0

    @property
    def calls_made(self) -> int:
        return self._calls

    async def serve(self, request: dict) -> dict:
        """One `model_request` -> the `model_response` to write back. Never raises."""
        request_id = str(request.get("id") or "")
        kind = str(request.get("kind") or TEXT)
        started = time.monotonic()
        try:
            model = self._authorize(kind, request)
        except _Refused as refusal:
            return self._reply(request_id, ok=False, error=str(refusal), outcome=refusal.outcome)

        self._calls += 1
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self._call, kind, model, request), timeout=self._timeout_s
            )
        except TimeoutError:
            return self._reply(
                request_id, ok=False,
                error=f"the model call exceeded {self._timeout_s:.0f}s and was abandoned",
                outcome="timeout",
            )
        except Exception as e:  # noqa: BLE001 — a provider failure is the plugin's error, not a crash
            log.warning("sandbox model call failed (%s/%s): %s", self._plugin_id, self._tool_name, e)
            return self._reply(request_id, ok=False, error=f"{type(e).__name__}: {e}", outcome="error")

        telemetry.timing(
            "sandbox_model_ms", int((time.monotonic() - started) * 1000), source="sandbox",
            _props={"plugin_id": self._plugin_id, "tool": self._tool_name, "model": model},
        )
        return self._reply(request_id, ok=True, text=text, outcome="ok")

    # ------------------------------------------------------------------ policy

    def _authorize(self, kind: str, request: dict) -> str:
        """-> the model id to actually use. Raises _Refused with a message the plugin will see.

        Every refusal happens HERE, before the call, so each one carries its own outcome tag. A
        check buried in the worker thread would come back as a generic error and the metric would
        say nothing about why.
        """
        if kind not in (TEXT, VISION, IMAGE):
            raise _Refused(f"unsupported model request kind {kind!r}", outcome="bad-kind")
        if not self._grant.models:
            raise _Refused(
                "this tool is not granted model access. A tool that calls a model declares "
                "`needs_model = True`; without that the sandbox grants none.",
                outcome="not-granted",
            )
        if self._max_calls and self._calls >= self._max_calls:
            raise _Refused(
                f"model call limit reached for this tool run ({self._max_calls}). Raise "
                "`sandbox_model_limits.max_calls` if a tool legitimately needs more.",
                outcome="call-cap",
            )
        if kind == VISION:
            self._image_paths(request)  # validated before the call, so its refusal is tagged
        if kind == IMAGE:
            # BOTH sides of the file boundary are the tool's own granted paths: what it may show
            # the model (reference images) and where the host will write the result. Validated
            # here so each refusal carries its own tag, same as vision.
            if not str(request.get("out_path") or "").strip():
                raise _Refused("an image request names no out_path", outcome="bad-request")
            self._granted_paths([request.get("out_path")], "write the generated image to")
            if request.get("reference_images"):
                self._granted_paths(request.get("reference_images"), "read")
        wanted = str(request.get("model") or "").strip()
        if not wanted:
            # Nothing named: the tool did not resolve one, so give it the grant's first — the same
            # id the host's own resolution chain produced for this tool.
            return self._grant.models[0]
        if wanted not in self._grant.models:
            raise _Refused(
                f"model '{wanted}' is not one this tool may use "
                f"(allowed: {', '.join(self._grant.models)}). The model comes from configuration, "
                "not from the plugin — naming an arbitrary one is how a plugin would spend an "
                "account's balance on a model nobody chose.",
                outcome="model-denied",
            )
        return wanted

    # ------------------------------------------------------------------ the call

    def _call(self, kind: str, model: str, request: dict) -> str:
        """Perform the real call. Runs in a worker thread; `oneshot` is synchronous.

        Imported here rather than at module scope so the import cost (litellm) is paid only by a
        deployment that actually serves a sandboxed model call.
        """
        import json

        from agent_runtime.infrastructure.llm import oneshot

        prompt = str(request.get("prompt") or "")
        max_tokens = self._clamp_tokens(request.get("max_tokens"))
        if kind == TEXT:
            return oneshot.text_complete(
                model=model, prompt=prompt, max_tokens=max_tokens, timeout=self._timeout_s
            )
        if kind == VISION:
            return oneshot.vision_complete(
                model=model,
                prompt=prompt,
                image_paths=self._image_paths(request),
                want_json=bool(request.get("want_json")),
                timeout=self._timeout_s,
            )
        if kind == IMAGE:
            result = oneshot.generate_image(
                model=model,
                prompt=prompt,
                out_path=self._granted_paths(
                    [request.get("out_path")], "write the generated image to"
                )[0],
                reference_images=self._granted_paths(request.get("reference_images"), "read")
                if request.get("reference_images")
                else [],
                aspect_ratio=str(request.get("aspect_ratio") or "") or None,
                image_size=str(request.get("image_size") or "") or None,
                timeout=self._timeout_s,
            )
            # The reply channel carries text; the file itself is already at the granted path.
            return json.dumps(result)
        raise ValueError(f"unsupported model request kind: {kind!r}")

    def _image_paths(self, request: dict) -> list[str]:
        """Vision inputs must be paths the TOOL could already read — its own workspace.

        Otherwise the broker becomes a file-read oracle: a sandboxed tool that cannot open another
        account's file could simply ask the host to send it to a model and describe it back.
        """
        return self._granted_paths(request.get("image_paths") or [], "read")

    def _granted_paths(self, raw, verb: str) -> list[str]:
        """Resolve `raw` (one path or a list) and refuse any that leaves the grant — the one
        containment check for everything the broker reads OR writes on the tool's behalf."""
        paths = [str(p) for p in (raw if isinstance(raw, (list, tuple)) else [raw]) if p]
        allowed = self._grant.fs_paths
        if not allowed:
            raise _Refused(
                "no filesystem paths are granted, so no file can be touched", outcome="fs-denied"
            )
        import os

        out = []
        for path in paths:
            real = os.path.realpath(path)
            if not any(
                real == root or real.startswith(os.path.realpath(root) + os.sep)
                for root in allowed
            ):
                raise _Refused(
                    f"'{path}' is outside this tool's granted paths — the host will not {verb} a "
                    "file on a sandboxed tool's behalf that the tool could not touch itself.",
                    outcome="fs-denied",
                )
            out.append(real)
        return out

    def _clamp_tokens(self, requested) -> int | None:
        try:
            wanted = int(requested or 0)
        except (TypeError, ValueError):
            wanted = 0
        if not self._max_output_tokens:
            return wanted or None
        if wanted <= 0:
            return self._max_output_tokens
        return min(wanted, self._max_output_tokens)

    # ------------------------------------------------------------------ replies

    def _reply(self, request_id: str, *, ok: bool, text: str = "", error: str = "", outcome: str) -> dict:
        telemetry.count(
            "sandbox_model_request_total",
            source="sandbox",
            _props={"plugin_id": self._plugin_id, "tool": self._tool_name, "outcome": outcome},
        )
        if not ok:
            log.warning(
                "sandbox: model request refused for '%s'/'%s' (%s): %s",
                self._plugin_id, self._tool_name, outcome, error,
            )
        return {"t": "model_response", "id": request_id, "ok": ok, "text": text, "error": error}


class _Refused(Exception):
    """A request the broker will not serve. The message goes back to the plugin verbatim."""

    def __init__(self, message: str, *, outcome: str) -> None:
        super().__init__(message)
        self.outcome = outcome
