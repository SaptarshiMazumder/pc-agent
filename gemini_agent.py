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

CONFIG = types.GenerateContentConfig(
    system_instruction=(
        "You control the user's real computer through the computer-use tool. "
        "Look at each screenshot, then take ONE action at a time, re-checking the "
        "new screenshot before the next. Be concise. If a task is ambiguous or "
        "destructive, stop and ask before proceeding."
    ),
    tools=[types.Tool(computer_use=types.ComputerUse(
        environment=types.Environment.ENVIRONMENT_BROWSER))],
)


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
        resp = client.models.generate_content(
            model=MODEL, contents=session, config=CONFIG)
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
