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
    "Tools: run_shell (PowerShell/bash), read_file (text), read_document (PDF/Word "
    "— CVs, docs), write_file, list_dir, find_file (locate files fast), web_search "
    "(quick facts), fetch_url (static page), a WEB BROWSER (web_open → web_snapshot "
    "→ web_click/web_fill/web_press → re-snapshot → web_close; CDP + accessibility "
    "@refs, for any site incl. logged-in pages), gmail/calendar (read), "
    "use_computer_visually (LAST RESORT, ~10x slower — native DESKTOP apps only, "
    "NEVER websites).\n"
    "Routing: anything on the web -> the web browser loop (or web_search for a "
    "quick fact); files -> read_document/find_file; system -> run_shell; native "
    "desktop GUI -> use_computer_visually. Never use the visual tool for websites; "
    "navigate straight to a target URL rather than operating a site's UI."
)

_POSTURE = {
    "accuracy": (
        "GROUND THE FACTS, THEN BE DECISIVE. The factual LIST — the actual items "
        "and their real URLs — must come from real, current pages, never from "
        "memory: web_open the site's SEARCH-RESULTS URL and read the whole list "
        "from that snapshot (it carries the page TEXT + links); fetch_url only for "
        "a specific static page. Capture a SOURCE URL for every item; for a 'list "
        "of N', keep going until you have N verified entries (or report how many). "
        "But the ANALYSIS the user wants — fit scores, rankings, salary/value "
        "ESTIMATES — is yours to give from market knowledge even when the page "
        "omits it; just LABEL estimates as estimates. Aim for a complete, "
        "confident, ranked answer, not hedging — only stop short when a real "
        "blocker (login/captcha/2FA) prevents getting the underlying facts."
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
