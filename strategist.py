"""
The strategist — the 'smart' half of the hybrid brain.

A strong model (gemini-2.5-pro) looks at the task, considers a few candidate
approaches, critiques them, and picks the SIMPLEST/FASTEST one before the cheap
flash executor spends any time. This is what stops "find my YouTube history"
from turning into a 7-minute Google Takeout detour.

Returns {approach, why, avoid}; the executor receives `approach` as guidance.
"""
import json
import os

from google.genai import types

from gemini_agent import generate

STRATEGY_MODEL = os.getenv("STRATEGY_MODEL", "gemini-2.5-pro")
PRIORITY = os.getenv("PRIORITY", "accuracy").lower()   # accuracy | speed

_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    properties={
        "approach": types.Schema(type=types.Type.STRING),
        "why": types.Schema(type=types.Type.STRING),
        "avoid": types.Schema(type=types.Type.STRING),
    },
    required=["approach", "why"],
)

_TOOLS_NOTE = (
    "Tools: run_shell (PowerShell/bash), read_file (plain text), read_document "
    "(PDF/Word/text — use for CVs, resumes, any document), write_file, list_dir, "
    "web_search, fetch_url, gmail/calendar (read), use_computer_visually (drives "
    "the real screen/browser; ~10x slower than the rest).\n"
    "Distinction: PUBLIC info (jobs, research, comparisons) lives on the open web "
    "-> web_search + fetch_url. The USER'S OWN logged-in data (their YouTube "
    "history, Gmail) needs their account -> API or use_computer_visually on the "
    "direct page (e.g. youtube.com/feed/history); never bulk exports/Takeout."
)

_POSTURE = {
    "accuracy": (
        "PRIORITIZE ACCURACY over speed. NEVER answer a factual or current-info "
        "task from the model's memory — memory is often outdated or fabricated "
        "(that is the failure to avoid). ALWAYS ground in real, current sources: "
        "web_search, then fetch_url the ACTUAL pages to read real entries; when "
        "the real data only lives behind a site the agent must operate (or search "
        "can't reach it), use use_computer_visually to read it directly — the "
        "extra time is acceptable. Gather from MULTIPLE sources, capture a SOURCE "
        "URL for every item/fact, and include ONLY items actually found on a real "
        "page — never plausible guesses. For a 'list of N' task, keep going across "
        "sources until you have N real, verified entries (or report how many you "
        "could actually verify)."
    ),
    "speed": (
        "Prefer the FASTEST adequate method. Answering from knowledge is fine for "
        "general info where live precision isn't essential; otherwise web_search. "
        "Reserve use_computer_visually for when nothing faster can get the result."
    ),
}


def _build_prompt(task: str) -> str:
    return (
        "You are the strategist for an autonomous agent controlling a Windows PC.\n\n"
        + _POSTURE.get(PRIORITY, _POSTURE["accuracy"]) + "\n\n"
        + _TOOLS_NOTE + "\n\n"
        "Think of 2-3 candidate approaches, critique them, and pick the best for "
        "the stated priority. Return:\n"
        "  approach - concise directive for the executor (which tool, what to do, "
        "which URL/query/command; for accuracy, say to capture source URLs)\n"
        "  why      - one line on why this best serves the priority\n"
        "  avoid    - routes NOT to take (name them, e.g. answering from memory)\n\n"
        f"Task: {task}"
    )


def choose_approach(task: str) -> dict:
    resp = generate(
        STRATEGY_MODEL,
        _build_prompt(task),
        types.GenerateContentConfig(
            response_mime_type="application/json", response_schema=_SCHEMA),
    )
    return json.loads(resp.text)
