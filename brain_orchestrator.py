"""
Orchestrated wrapper around the autonomous tool agent (brain.py).

plan (tools mode) -> for each step: run the brain on JUST that step -> verify the
step from the agent's own output/tool results -> retry on failure. One shared
brain session is kept across steps so it remembers earlier results.

This is the tool-agent counterpart to orchestrator.py (which drives the
screenshot/click computer-use loop).
"""
import os

import brain
import control
import planner
import verifier

VERIFY_RETRIES = int(os.getenv("VERIFY_RETRIES", "2"))


def run_orchestrated(task, on_text, approve):
    on_text("📋 Planning the task…")
    try:
        plan = planner.make_plan(task, mode="tools")
    except Exception as e:
        on_text(f"⚠️ Planning failed ({e}); running the agent directly.")
        brain.run_agent(task, on_text, approve)
        return

    plan_view = "\n".join(
        f"  {i}. {p['step']}   (done when: {p['done_when']})"
        for i, p in enumerate(plan, 1))
    on_text(f"Plan — {len(plan)} steps:\n{plan_view}")

    session = brain.new_session()        # shared across steps for continuity

    for i, item in enumerate(plan, 1):
        control.check()
        step, done_when = item["step"], item["done_when"]
        on_text(f"\n▶ Step {i}/{len(plan)}: {step}")
        ok, reason = False, ""

        for attempt in range(1, VERIFY_RETRIES + 1):
            control.check()
            captured = []

            def cap(text):               # tee the brain's output -> user + evidence
                captured.append(text)
                on_text(text)

            instr = (f"Overall goal: {task}\n"
                     f"Do ONLY this step now: {step}\n"
                     f"This step is complete when: {done_when}")
            if attempt > 1:
                instr = (f"Your previous attempt failed verification: {reason}\n"
                         "Try a different approach.\n" + instr)

            brain.run_agent(instr, cap, approve, session)

            control.check()
            evidence = "\n".join(captured)[-4000:]
            ok, reason = verifier.verify_text(step, done_when, evidence)
            on_text(f"{'✅' if ok else '❌'} verify (attempt {attempt}/{VERIFY_RETRIES}): {reason}")
            if ok:
                break

        if not ok:
            on_text(f"⚠️ Step {i} couldn't be verified after {VERIFY_RETRIES} "
                    "attempts — continuing anyway.")

    on_text("\n🏁 Orchestrated run finished.")
