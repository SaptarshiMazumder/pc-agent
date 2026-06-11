"""
Layer 1: planning. Turns a free-form task into an ordered list of small,
individually verifiable steps. Uses a cheap text model (no vision needed) and
forces structured JSON so we get back clean {step, done_when} objects.

Reuses the Gemini client already built in gemini_agent.
"""
import json
import os

from google.genai import types

from gemini_agent import client

PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-2.5-flash")

_SCHEMA = types.Schema(
    type=types.Type.ARRAY,
    items=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "step": types.Schema(type=types.Type.STRING),
            "done_when": types.Schema(type=types.Type.STRING),
        },
        required=["step", "done_when"],
    ),
)

_PROMPT = (
    "You are planning a COMPUTER-USE task that an agent will carry out by "
    "clicking and typing in a desktop web browser. Break the task into 2-6 "
    "ordered steps that each map to a concrete UI action. For each step give:\n"
    "  step      - the concrete action to take\n"
    "  done_when - an OBSERVABLE on-screen condition that proves it succeeded\n"
    "Keep steps coarse (one navigation or one read), not individual clicks.\n"
    "Task: {task}"
)


def make_plan(task: str) -> list[dict]:
    resp = client.models.generate_content(
        model=PLANNER_MODEL,
        contents=_PROMPT.format(task=task),
        config=types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    return json.loads(resp.text)
