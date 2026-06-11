"""
The GEMINI agent loop — the counterpart to agent.py, using Google's computer-use
tool instead of Anthropic's.

Same callback contract as agent.py so local.py can drive either backend:
  on_text(str)            -> the model said something; show it
  approve_bash(desc)->bool -> confirm a sensitive action (reused as a generic gate)

A "session" here is a list of google.genai Content objects (not Anthropic dicts),
built by new_session() / add_user_text() below.
"""
import os
import socket
import time

from google import genai
from google.genai import types
from google.genai.types import (
    Content, Part, FunctionResponse, FunctionResponsePart, FunctionResponseBlob,
)

import control
import gemini_computer

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-computer-use-preview-10-2025")
MAX_ITERATIONS = int(os.getenv("MAX_ITERATIONS", "40"))

_API_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
client = genai.Client(api_key=_API_KEY)

_ENVS = {
    "browser": types.Environment.ENVIRONMENT_BROWSER,
    "desktop": types.Environment.ENVIRONMENT_DESKTOP,
    "mobile": types.Environment.ENVIRONMENT_MOBILE,
}
# "desktop" makes Gemini a whole-computer agent (taskbar, native apps), not just
# a browser. "browser" restricts it to web pages. Default desktop — this project
# drives the real machine.
COMPUTER_ENV = os.getenv("COMPUTER_ENV", "desktop").lower()

CONFIG = types.GenerateContentConfig(
    system_instruction=(
        "You control the user's ENTIRE computer — mouse, keyboard, and screen — "
        "not just a web browser. To open an application (e.g. Teams, Slack, a "
        "file manager), click its icon on the taskbar or desktop, or use the "
        "Start menu. Only open a web browser when the task genuinely needs a "
        "website. Take a screenshot first to see the current state, act ONE step "
        "at a time, and re-check the new screenshot before the next. Be concise. "
        "If a task is ambiguous or destructive, stop and ask before proceeding."
    ),
    tools=[types.Tool(computer_use=types.ComputerUse(
        environment=_ENVS.get(COMPUTER_ENV, types.Environment.ENVIRONMENT_DESKTOP)))],
)


_TRANSIENT = (socket.gaierror, ConnectionError, TimeoutError)


def _is_transient(e: Exception) -> bool:
    if isinstance(e, _TRANSIENT):
        return True
    s = (type(e).__name__ + " " + str(e)).lower()
    return any(k in s for k in (
        "getaddrinfo", "temporarily", "timeout", "timed out", "connection",
        "unavailable", "reset", "502", "503", "504", "429"))


def generate(model, contents, config, attempts=4):
    """client.models.generate_content with retry+backoff on transient network
    errors (DNS blips, timeouts, 5xx). Non-transient errors raise immediately."""
    delay = 1.0
    for i in range(attempts):
        control.check()
        try:
            return client.models.generate_content(
                model=model, contents=contents, config=config)
        except Exception as e:               # noqa: BLE001
            if i == attempts - 1 or not _is_transient(e):
                raise
            time.sleep(delay)
            delay = min(delay * 2, 8.0)


def new_session():
    return []


def add_user_text(session, text):
    """Seed a user turn WITH a current screenshot — Gemini needs one every turn."""
    session.append(Content(role="user", parts=[
        Part(text=text),
        Part.from_bytes(data=gemini_computer.screenshot_bytes(), mime_type="image/png"),
    ]))


def run_turn(session, on_text, approve_bash):
    for _ in range(MAX_ITERATIONS):
        control.check()                      # bail before each model call
        resp = generate(MODEL, session, CONFIG)
        cand = resp.candidates[0]
        session.append(cand.content)

        calls = []
        for part in cand.content.parts or []:
            if getattr(part, "text", None) and part.text.strip():
                on_text(part.text)
            if getattr(part, "function_call", None):
                calls.append(part.function_call)

        if not calls:
            break

        responses = []
        for fc in calls:
            control.check()                  # bail before each action
            args = dict(fc.args or {})
            ack = None

            # Gemini may flag an action as needing confirmation.
            if "safety_decision" in args:
                if not approve_bash(f"{fc.name}({args})"):
                    result = {"error": "user denied this action"}
                else:
                    ack = "true"
                    result = gemini_computer.execute(fc.name, args)
            else:
                result = gemini_computer.execute(fc.name, args)

            data = {"url": ""}          # no browser URL available on raw desktop
            data.update(result)
            if ack:
                data["safety_acknowledgement"] = "true"

            responses.append(FunctionResponse(
                name=fc.name, response=data,
                parts=[FunctionResponsePart(inline_data=FunctionResponseBlob(
                    mime_type="image/png", data=gemini_computer.screenshot_bytes()))],
            ))

        session.append(Content(
            role="user", parts=[Part(function_response=r) for r in responses]))
    else:
        on_text("⚠️ Hit the iteration cap — stopping to avoid runaway cost.")
    return session
