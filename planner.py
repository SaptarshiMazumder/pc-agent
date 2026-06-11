"""
Layer 1: planning. Turns a free-form task into an ordered list of small,
individually verifiable steps. Uses a cheap text model (no vision needed) and
forces structured JSON so we get back clean {step, done_when} objects.

Reuses the Gemini client already built in gemini_agent.
"""
import json
import os

from google.genai import types

from gemini_agent import generate

PLANNER_MODEL = os.getenv("PLANNER_MODEL", "gemini-2.5-pro")

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

_PROMPTS = {
    "gui": (
        "You are planning a COMPUTER-USE task that an agent will carry out by "
        "clicking and typing in a desktop web browser. Break the task into 2-6 "
        "ordered steps that each map to a concrete UI action. For each step give:\n"
        "  step      - the concrete action to take\n"
        "  done_when - an OBSERVABLE on-screen condition that proves it succeeded\n"
        "Keep steps coarse (one navigation or one read), not individual clicks.\n"
        "Task: {task}"
    ),
    "tools": (
        "You are planning a task an autonomous agent will perform on a Windows PC "
        "using TOOLS: run_shell (PowerShell/bash), read/write files, web_search, "
        "fetch_url, gmail/calendar (read), and use_computer_visually. Choose the "
        "SIMPLEST, FASTEST approach a knowledgeable person would use — prefer "
        "direct pages/URLs/commands over heavyweight routes like bulk exports or "
        "pipelines (e.g. YouTube history -> youtube.com/feed/history, NOT "
        "Takeout). Break it into 2-6 ordered steps. For each step give:\n"
        "  step      - the concrete action (which tool, roughly what)\n"
        "  done_when - a CHECKABLE condition proving success (e.g. command output "
        "shows X, a file exists, a value was found)\n"
        "Task: {task}"
    ),
}


def make_plan(task: str, mode: str = "gui") -> list[dict]:
    resp = generate(
        PLANNER_MODEL,
        _PROMPTS.get(mode, _PROMPTS["gui"]).format(task=task),
        types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    return json.loads(resp.text)
