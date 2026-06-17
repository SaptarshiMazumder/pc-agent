"""GeminiComputerUseDriver — the internal computer-use loop (ported from v1
gemini_agent.py).

Drives Gemini's dedicated computer-use model via the google-genai SDK with the
native `computer_use` tool: screenshot -> model emits a function_call (click_at,
type_text_at, ...) -> execute via the ComputerProvider -> feed the new screenshot
back -> repeat, until the model stops calling functions, the step cap is hit, or
the user aborts. Self-contained: its own model + creds, independent of the main
agent's LLM (so the main agent can be any model, even text-only).

The model call is injectable (`generate_fn`) so the loop is unit-testable without
the network or a real desktop.
"""

from __future__ import annotations

import asyncio
import logging
import os
import socket
import time
from pathlib import Path

from agentd.infrastructure.tools.computer.actions import parse_function_call

log = logging.getLogger("agentd")

_SYSTEM_INSTRUCTION = (
    "You control the user's ENTIRE computer — mouse, keyboard, and screen — not "
    "just a web browser. To open an application (Notepad, Teams, VS Code, a file "
    "manager), click its taskbar/desktop icon or use the Start menu (press the "
    "Windows key, type the app name, Enter). Only open a web browser when the task "
    "genuinely needs a website. You are given a screenshot each turn; act ONE step "
    "at a time and re-check the new screenshot before the next. When the task is "
    "fully complete (or is impossible/ambiguous), stop calling actions and reply "
    "with a short plain-text summary of what you did or why you stopped. Be concise."
)

_TRANSIENT = (socket.gaierror, ConnectionError, TimeoutError)


def _is_transient(e: Exception) -> bool:
    if isinstance(e, _TRANSIENT):
        return True
    s = (type(e).__name__ + " " + str(e)).lower()
    return any(k in s for k in (
        "getaddrinfo", "temporarily", "timeout", "timed out", "connection",
        "unavailable", "reset", "502", "503", "504", "429"))


class GeminiComputerUseDriver:
    def __init__(self, provider, model: str, config, generate_fn=None):
        self._provider = provider
        self._model = model
        self._config = config
        self._max_steps = config.computer_max_steps
        self._shots_dir = Path(config.state_dir) / "screenshots" / f"computer-{int(time.time())}"
        self._generate_fn = generate_fn or self._default_generate_fn()

    # ------------------------------------------------------------- model call

    def _default_generate_fn(self):
        """Build the real google-genai call (lazy, so importing this module is cheap
        and tests can inject a fake instead)."""
        from google import genai
        from google.genai import types

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        client = genai.Client(api_key=api_key)
        env = getattr(types.Environment, "ENVIRONMENT_DESKTOP", types.Environment.ENVIRONMENT_BROWSER)
        cfg = types.GenerateContentConfig(
            system_instruction=_SYSTEM_INSTRUCTION,
            tools=[types.Tool(computer_use=types.ComputerUse(environment=env))],
        )

        def _gen(contents):
            delay = 1.0
            for i in range(4):
                try:
                    return client.models.generate_content(
                        model=self._model, contents=contents, config=cfg)
                except Exception as e:  # noqa: BLE001
                    if i == 3 or not _is_transient(e):
                        raise
                    time.sleep(delay)
                    delay = min(delay * 2, 8.0)

        return _gen

    # ------------------------------------------------------------------- loop

    async def run(self, task: str, abort, on_update=None) -> str:
        from google.genai import types

        self._shots_dir.mkdir(parents=True, exist_ok=True)

        def _shot_part():
            png = self._provider.screenshot()
            return png, types.Part.from_bytes(data=png, mime_type="image/png")

        png, shot = _shot_part()
        self._save(png, 0)
        contents = [types.Content(role="user", parts=[types.Part(text=task), shot])]

        last_text = ""
        for step in range(1, self._max_steps + 1):
            if abort.is_set():
                return f"Aborted by user after {step - 1} step(s). {last_text}".strip()

            try:
                resp = await asyncio.to_thread(self._generate_fn, contents)
            except Exception as e:  # noqa: BLE001
                return f"Computer-use model error after {step - 1} step(s): {type(e).__name__}: {e}"

            cand = resp.candidates[0]
            contents.append(cand.content)

            calls = []
            for part in cand.content.parts or []:
                if getattr(part, "text", None) and part.text.strip():
                    last_text = part.text.strip()
                    if on_update:
                        on_update(last_text)
                if getattr(part, "function_call", None):
                    calls.append(part.function_call)

            if not calls:  # model stopped acting -> its text is the final summary
                return last_text or f"Completed after {step - 1} step(s)."

            responses = []
            for fc in calls:
                if abort.is_set():
                    return f"Aborted by user after {step} step(s). {last_text}".strip()
                name, args = parse_function_call(fc)
                ack = args.pop("safety_decision", None)  # autonomous: auto-acknowledge
                try:
                    result = await asyncio.to_thread(self._provider.act, name, **args)
                except Exception as e:  # FailSafeException etc. -> stop the run
                    return f"Stopped after {step} step(s) (failsafe/abort: {type(e).__name__}). {last_text}".strip()
                if on_update:
                    on_update(f"step {step}: {name}({args})")
                data = {"url": ""}
                data.update(result)
                if ack:
                    data["safety_acknowledgement"] = "true"
                png, shot = _shot_part()
                self._save(png, step)
                responses.append(types.FunctionResponse(
                    name=name, response=data,
                    parts=[types.FunctionResponsePart(
                        inline_data=types.FunctionResponseBlob(mime_type="image/png", data=png))],
                ))
            contents.append(types.Content(
                role="user", parts=[types.Part(function_response=r) for r in responses]))

        return (f"Reached the step cap ({self._max_steps}) without the model signalling "
                f"completion. Last note: {last_text}").strip()

    def _save(self, png: bytes, step: int) -> None:
        try:
            (self._shots_dir / f"step-{step:02d}.png").write_bytes(png)
        except OSError:
            pass
