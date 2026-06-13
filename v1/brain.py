"""
The autonomous tool-calling agent — the general-purpose "brain".

Unlike the Gemini computer-use path (screenshot -> click), this drives the PC by
calling real function tools (shell, files, search, MCP, ...). The model decides
which tool to call, sees the result, and loops until the task is done.

Same callback contract as the other loops:
  on_text(str)        -> show the model's text / tool activity
  approve(desc)->bool -> confirm a sensitive action (e.g. a shell command)

Reuses gemini_agent's client + retry wrapper, and control for cancellation.
"""
import os
import time

from google.genai import types

import control
import tools
from gemini_agent import generate

BRAIN_MODEL = os.getenv("BRAIN_MODEL", "gemini-2.5-flash")
MAX_STEPS = int(os.getenv("AGENT_MAX_STEPS", "50"))
PRIORITY = os.getenv("PRIORITY", "accuracy").lower()   # accuracy | speed

_ACCURACY = (
    "\n\nGROUND FACTS, THEN BE DECISIVE. The factual LIST — the actual items and "
    "their real URLs — must come from real pages (cite the URL for each); never "
    "invent entries, and for a 'list of N' keep fetching until you have N verified "
    "entries or state how many you confirmed. But the ANALYSIS the user asks for is "
    "yours to give: score fit, rank, and ESTIMATE values (e.g. salary from market "
    "norms) with your own judgement even when the page omits them — just LABEL "
    "estimates clearly (\"est. ¥15–20M, market-based\"). Give a complete, confident, "
    "ranked answer rather than hedging to 'not specified'. Only stop short when a "
    "real blocker (login/captcha/2FA) prevents getting the underlying facts."
)

SYSTEM = (
    "You are an autonomous agent operating the user's Windows PC to accomplish "
    "their goal end to end — the way a fast, resourceful person would.\n\n"
    "PICK THE SIMPLEST PATH. Before acting, think about the most DIRECT way to "
    "get the result. If several approaches exist, choose the one a knowledgeable "
    "person would do fastest. State that approach in one short line, then begin. "
    "Strongly prefer direct routes over heavyweight ones: open a site's own page "
    "or a known URL instead of exporting bulk data or building a pipeline. "
    "Example: to see recent YouTube watch history, open youtube.com/feed/history "
    "directly — do NOT use Google Takeout. Avoid over-engineering; if a path is "
    "taking many steps or feels slow, STOP and switch to a simpler one.\n\n"
    "TOOLS, fastest first — go down only when the faster tool can't do it:\n"
    "  • answer from your own knowledge (for general info where live precision "
    "isn't essential — note a recency caveat)\n"
    "  • web_search (quick fact lookup) / fetch_url (read a static page)\n"
    "  • web browser (for ANY website) — loop: web_open(url) → web_snapshot (reads "
    "the page's TEXT + @eN refs + link URLs) → act on a ref (web_click/web_fill/"
    "web_press) → re-snapshot → web_close. To FIND/LIST many items, web_open the "
    "SEARCH-RESULTS URL and read the whole list from that one snapshot — don't open "
    "results one link at a time. On a '[NOT LOGGED IN]' wall call web_login; on a "
    "captcha/2FA/permission wall, STOP and tell the user what's blocking — never "
    "fall back to web_search to fabricate an answer.\n"
    "  • run_shell, read_file, read_document (PDF/Word/text — use for CVs & "
    "documents), write_file, list_dir, find_file (locate a file fast — never "
    "recursive scans, which hang on OneDrive), gmail_recent, calendar_events\n"
    "  • use_computer_visually — LAST RESORT, ~10x slower; ONLY for NATIVE DESKTOP "
    "apps, NEVER for websites (use browse_web for the web).\n\n"
    "Work in small steps: call a tool, read the result, decide the next. Verify "
    "your work (check output, re-read a file). When the goal is done, reply with a "
    "short summary and NO tool call. If an action is destructive or the request is "
    "ambiguous, explain and ask first."
)

if PRIORITY == "accuracy":
    SYSTEM += _ACCURACY

CONFIG = types.GenerateContentConfig(
    system_instruction=SYSTEM,
    tools=[types.Tool(function_declarations=tools.DECLARATIONS)],
)


def new_session():
    return []


def run_agent(task, on_text, approve, session=None, guidance=None):
    if session is None:
        session = []
    msg = task
    if guidance:
        msg = f"{task}\n\n[Chosen approach — follow this unless it proves wrong]\n{guidance}"
    session.append(types.Content(role="user", parts=[types.Part(text=msg)]))

    for _ in range(MAX_STEPS):
        control.check()
        on_text("⏳ thinking…")
        resp = generate(BRAIN_MODEL, session, CONFIG)
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
            control.check()
            args = dict(fc.args or {})
            t0 = time.time()
            result = tools.dispatch(fc.name, args, approve, on_text)
            dt = time.time() - t0
            preview = str(result).replace("\n", " ")
            if len(preview) > 160:
                preview = preview[:160] + f"… [{len(str(result))} chars]"
            on_text(f"✓ {fc.name} ({dt:.1f}s): {preview}")
            responses.append(types.Part(function_response=types.FunctionResponse(
                name=fc.name, response={"result": result})))
        session.append(types.Content(role="user", parts=responses))
    else:
        on_text("⚠️ Hit the step cap — stopping to avoid a runaway loop.")
    return session
