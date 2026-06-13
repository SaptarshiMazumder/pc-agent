"""
Model-agnostic agent orchestrator: LangGraph (orchestration) + LiteLLM (any LLM).

The MODEL env var selects the provider/model via LiteLLM — switch providers with
a one-line change, no code edits. Examples:
  gemini/gemini-2.5-flash        (default; uses GEMINI_API_KEY)
  anthropic/claude-opus-4-8      (ANTHROPIC_API_KEY)
  openai/gpt-4.1                 (OPENAI_API_KEY)
  deepseek/deepseek-chat         (DEEPSEEK_API_KEY)
  moonshot/kimi-k2.5             (MOONSHOT_API_KEY)
  minimax/MiniMax-Text-01        (MINIMAX_API_KEY)

  python lc_agent.py "your task"          # one-shot (flat ReAct agent)
  python lc_agent.py -o "your task"       # orchestrated: plan -> execute -> verify
  python lc_agent.py                      # interactive

This is the model-agnostic path; the Gemini-only custom framework (auto.py) stays
as a separate backup.
"""
import os
import sys

try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from langchain_core.messages import AIMessage, ToolMessage
from langchain_litellm import ChatLiteLLM
from langgraph.prebuilt import create_react_agent

import lc_tools

MODEL = os.getenv("MODEL", "gemini/gemini-2.5-pro")
RECURSION_LIMIT = int(os.getenv("AGENT_MAX_STEPS", "50"))

_ARGS = list(sys.argv[1:])
ORCHESTRATED = any(f in _ARGS for f in ("-o", "--orchestrated"))
_ARGS = [a for a in _ARGS if a not in ("-o", "--orchestrated")]

SYSTEM = (
    "You are an autonomous agent operating the user's Windows PC to accomplish "
    "their goal end to end, the way a fast, resourceful person would.\n\n"
    "PICK THE SIMPLEST PATH. Prefer the most direct method. For information/"
    "WEB ROUTING — follow exactly:\n"
    "• To FIND / LIST / EXTRACT anything from websites (jobs, products, search "
    "results, listings, prices, page content) → use the WEB BROWSER: web_open(the "
    "site's SEARCH-RESULTS URL with query params) → read the WHOLE LIST from that "
    "one snapshot (it now contains the page's readable TEXT + link URLs, not just "
    "buttons) → open or web_get_text individual entries only for detail you still "
    "need → web_close. Get the full list from the results page FIRST; do NOT open "
    "results one link at a time and stop after two. The snapshot usually already "
    "contains the data to extract.\n"
    "• If web_open returns '[NOT LOGGED IN]' or a sign-in wall, do NOT flail to "
    "web_search — call web_login(the site's login URL) so the USER can sign in, "
    "then retry web_open. Never give up on a walled page without prompting login.\n"
    "• If a page needs a captcha, 2FA, or a permission that web_login can't "
    "resolve, STOP and tell the user exactly what is blocking — never fall back to "
    "web_search to fabricate an answer.\n"
    "• web_search is ONLY for a single quick fact — NOT for gathering a list and "
    "NOT for reading a specific site. NEVER answer a 'find/list X' task from "
    "web_search summaries; open the site and read it.\n"
    "• fetch_url is ONLY for one specific static page; major sites (LinkedIn, "
    "OpenAI, job boards) return 403 / login walls — use the web browser for those.\n"
    "• use_computer_visually ONLY for native desktop apps, never websites.\n"
    "For "
    "files use read_document (PDF/Word) or read_file (text); to LOCATE a file use "
    "find_file — never recursive directory scans (they hang). Use run_shell for "
    "system/install/script work. Avoid heavyweight detours when a direct page, "
    "command, or file read works.\n\n"
    "GROUND FACTS, THEN BE DECISIVE. The factual LIST (the actual jobs/products/"
    "items and their real URLs) must come from real pages you opened — never invent "
    "entries. But the ANALYSIS the user asks for is YOURS to give: score fit, rank, "
    "and ESTIMATE values (e.g. salary from market norms) using your own judgement "
    "even when the page doesn't state them — just LABEL estimates as estimates "
    "(\"est. ¥15–20M, market-based\"). Don't hedge to uselessness or answer \"not "
    "specified\" when a reasoned estimate helps; give a complete, confident, ranked "
    "answer. Only stop short when a real blocker (login/captcha/2FA) prevents "
    "getting the underlying facts.\n\n"
    "Work step by step: call a tool, read the result, decide the next. When the "
    "goal is done, give a short summary. If an action is destructive or the request "
    "is ambiguous, explain and ask first."
)


# Gemini blocks responses on its own safety filters and then returns ZERO
# candidates; litellm/langchain then does choices[0] on an empty list →
# "list index out of range" mid-run. Job posts / web content occasionally trip
# this (recitation, etc.). Disable the filters so the model always answers.
_GEMINI_SAFETY = [
    {"category": c, "threshold": "BLOCK_NONE"} for c in (
        "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
        "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT",
    )
]


def build():
    kwargs = {"model": MODEL, "temperature": 0}
    if MODEL.startswith("gemini"):
        kwargs["model_kwargs"] = {"safety_settings": _GEMINI_SAFETY}
    llm = ChatLiteLLM(**kwargs)
    return create_react_agent(llm, lc_tools.TOOLS, prompt=SYSTEM)


def _print(msg):
    if isinstance(msg, AIMessage):
        for tc in msg.tool_calls or []:
            print(f"\n→ {tc['name']}({tc.get('args', {})})")
        text = msg.content if isinstance(msg.content, str) else ""
        if text.strip():
            print(f"\n🤖 {text}\n")
    elif isinstance(msg, ToolMessage):
        out = str(msg.content).replace("\n", " ")
        if len(out) > 160:
            out = out[:160] + f"… [{len(str(msg.content))} chars]"
        print(f"✓ {out}")


def run(agent, task):
    seen = 0
    try:
        for state in agent.stream(
            {"messages": [("user", task)]},
            config={"recursion_limit": RECURSION_LIMIT},
            stream_mode="values",
        ):
            msgs = state["messages"]
            for m in msgs[seen:]:
                _print(m)
            seen = len(msgs)
    except KeyboardInterrupt:
        print("\n🛑 cancelled.")
    except IndexError:
        print("💥 The model returned an empty response (often a Gemini safety "
              "block or an over-long page). Retrying once usually works; if it "
              "persists, lower BROWSER_SNAPSHOT_MAX.")
    except Exception as e:                       # noqa: BLE001
        import traceback
        print(f"💥 {e}")
        traceback.print_exc()


def main():
    mode = "orchestrated" if ORCHESTRATED else "react"
    print(f"pc-agent (LangGraph + LiteLLM, model={MODEL}, {mode}).")

    if ORCHESTRATED:
        import lc_orchestrator
        graph = lc_orchestrator.build_graph()
        do = lambda task: lc_orchestrator.run(graph, task)
    else:
        agent = build()
        do = lambda task: run(agent, task)

    if _ARGS:
        do(" ".join(_ARGS))
        return

    print("Type a task. 'quit' exits. Ctrl+C cancels a running task.\n")
    while True:
        try:
            text = input("> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not text:
            continue
        if text.lower() in ("quit", "exit"):
            break
        do(text)


if __name__ == "__main__":
    main()
