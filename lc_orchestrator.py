"""
Orchestration layer for the model-agnostic agent: a LangGraph StateGraph that
runs plan -> execute -> verify -> (retry | advance) -> finalize.

  plan      : LLM decomposes the task into ordered {step, done_when} steps
  execute   : a ReAct sub-agent (the lc_agent tools) does the current step
  verify    : LLM judges, from the step's own output, whether done_when is met
  route     : ok -> next step; not ok & attempts left -> retry; else move on
  finalize  : LLM writes a short summary of the whole run

Planning and verification use plain-JSON prompting (parsed here) rather than
provider-specific structured-output APIs, so this works across any LiteLLM model.
"""
import json
import os
import re
from typing import TypedDict

from langchain_core.messages import AIMessage, ToolMessage
from langchain_litellm import ChatLiteLLM
from langgraph.graph import StateGraph, START, END
from langgraph.prebuilt import create_react_agent

import lc_tools
from lc_agent import MODEL, _print

VERIFY_RETRIES = int(os.getenv("VERIFY_RETRIES", "2"))

_llm = ChatLiteLLM(model=MODEL, temperature=0)
_executor = create_react_agent(
    _llm, lc_tools.TOOLS,
    prompt=(
        "You are an autonomous agent doing ONE step of a larger task on the "
        "user's Windows PC. Prefer the simplest, most direct method. For facts/"
        "research use web_search + fetch_url and cite sources; for any website use "
        "the web browser loop (web_open → web_snapshot → web_click/web_fill/"
        "web_press → re-snapshot → web_close) — use_computer_visually only for "
        "native desktop apps; for PDF/Word use read_document; use run_shell for "
        "system work; to locate a file use find_file (never recursive scans — they "
        "hang). Do only the requested step, verify your own output, then stop."
    ),
)

_PLAN_SYS = (
    "Break the task into 2-6 ordered, individually verifiable steps using the "
    "SIMPLEST, fastest approach (prefer direct pages/URLs/commands over bulk "
    "exports or pipelines). Reply with ONLY JSON, no prose:\n"
    '{"steps":[{"step":"concrete action","done_when":"observable success check"}]}'
)
_VERIFY_SYS = (
    "Judge whether the step achieved its INTENT from the evidence (the agent's "
    "actions, command output, and final text). Judge substance, not exact "
    "format. Reply with ONLY JSON, no prose:\n"
    '{"ok":true|false,"reason":"one sentence"}'
)


def _json_call(system: str, user: str):
    """Invoke the model and parse the first JSON value out of its reply."""
    for _ in range(2):
        resp = _llm.invoke([("system", system), ("user", user)])
        txt = resp.content if isinstance(resp.content, str) else str(resp.content)
        m = re.search(r"(\{.*\}|\[.*\])", txt, re.S)
        if m:
            try:
                return json.loads(m.group(1))
            except json.JSONDecodeError:
                continue
    return None


class S(TypedDict, total=False):
    task: str
    plan: list
    i: int
    attempts: int
    messages: list
    evidence: str
    ok: bool
    reason: str
    log: list


def _plan(state: S):
    print("📋 Planning the task…")
    data = _json_call(_PLAN_SYS, f"Task: {state['task']}")
    plan = (data or {}).get("steps") if isinstance(data, dict) else None
    if not plan:
        print("(planning fell back to a single step)")
        plan = [{"step": state["task"], "done_when": "the task is complete"}]
    for idx, s in enumerate(plan, 1):
        print(f"  {idx}. {s.get('step')}   (done when: {s.get('done_when')})")
    return {"plan": plan, "i": 0, "attempts": 0, "messages": [], "log": []}


def _execute(state: S):
    item = state["plan"][state["i"]]
    print(f"\n▶ Step {state['i'] + 1}/{len(state['plan'])}: {item.get('step')}")
    instr = (f"Overall goal: {state['task']}\n"
             f"Do ONLY this step now: {item.get('step')}\n"
             f"This step is complete when: {item.get('done_when')}")
    if state.get("attempts", 0) > 0 and state.get("reason"):
        instr = (f"Your previous attempt failed verification: {state['reason']}\n"
                 "Try a different approach.\n" + instr)

    msgs = list(state.get("messages", [])) + [("user", instr)]
    captured, seen, final = [], 0, None
    for st in _executor.stream({"messages": msgs},
                               config={"recursion_limit": 50},
                               stream_mode="values"):
        for x in st["messages"][seen:]:
            _print(x)
            if isinstance(x, (AIMessage, ToolMessage)):
                captured.append(str(x.content))
        seen = len(st["messages"])
        final = st
    return {
        "messages": final["messages"] if final else msgs,
        "evidence": "\n".join(captured)[-4000:],
    }


def _verify(state: S):
    item = state["plan"][state["i"]]
    data = _json_call(
        _VERIFY_SYS,
        f"Step: {item.get('step')}\nSuccess means: {item.get('done_when')}\n\n"
        f"Evidence:\n{state.get('evidence', '')}")
    if isinstance(data, dict) and "ok" in data:
        ok, reason = bool(data["ok"]), data.get("reason", "")
    else:
        ok, reason = True, "(verify unavailable — assuming success)"
    a = state.get("attempts", 0) + 1
    print(f"{'✅' if ok else '❌'} verify (attempt {a}/{VERIFY_RETRIES}): {reason}")
    return {"ok": ok, "reason": reason,
            "log": list(state.get("log", [])) +
            [f"Step {state['i'] + 1}: {'ok' if ok else 'failed'} — {reason}"]}


def _route(state: S):
    last = state["i"] >= len(state["plan"]) - 1
    if state.get("ok"):
        return "finalize" if last else "advance"
    if state.get("attempts", 0) + 1 < VERIFY_RETRIES:
        return "retry"
    if not last:
        print("⚠️ step unverified after retries — moving on")
    return "finalize" if last else "advance"


def _advance(state: S):
    return {"i": state["i"] + 1, "attempts": 0, "reason": ""}


def _retry(state: S):
    print("  ↻ retrying step")
    return {"attempts": state.get("attempts", 0) + 1}


def _finalize(state: S):
    try:
        resp = _llm.invoke([
            ("system", "Summarize the outcome for the user in a few sentences, "
                       "based on the run log and final result."),
            ("user", f"Task: {state['task']}\n\nLog:\n" +
             "\n".join(state.get("log", [])) +
             f"\n\nFinal result:\n{state.get('evidence', '')[:2000]}")])
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        print(f"\n🏁 {text}")
    except Exception:
        print("\n🏁 Orchestrated run finished.")
    return {}


def build_graph():
    g = StateGraph(S)
    g.add_node("plan", _plan)
    g.add_node("execute", _execute)
    g.add_node("verify", _verify)
    g.add_node("advance", _advance)
    g.add_node("retry", _retry)
    g.add_node("finalize", _finalize)
    g.add_edge(START, "plan")
    g.add_edge("plan", "execute")
    g.add_edge("execute", "verify")
    g.add_conditional_edges("verify", _route,
                            {"retry": "retry", "advance": "advance",
                             "finalize": "finalize"})
    g.add_edge("retry", "execute")
    g.add_edge("advance", "execute")
    g.add_edge("finalize", END)
    return g.compile()


def run(graph, task):
    try:
        graph.invoke({"task": task}, config={"recursion_limit": 100})
    except KeyboardInterrupt:
        print("\n🛑 cancelled.")
    except Exception as e:                       # noqa: BLE001
        print(f"💥 {e}")
