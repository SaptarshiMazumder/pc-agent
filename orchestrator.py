"""
The orchestrator — the "brain" that turns the single reactive loop into a
plan -> execute -> verify -> retry cycle (Layers 1 + 2).

Roles:
  planner.make_plan   -> decompose the task                       (Layer 1)
  gemini_agent.run_turn -> EXECUTE one step (your existing loop, reused as-is)
  verifier.verify     -> confirm the step actually succeeded       (Layer 2)

State is just the plan list plus one shared executor session (so the model keeps
visual/context continuity across steps). On a failed verification we re-run the
same step up to VERIFY_RETRIES times before moving on.
"""
import os

import control
import gemini_agent
import gemini_computer
import planner
import verifier

VERIFY_RETRIES = int(os.getenv("VERIFY_RETRIES", "2"))


def run_orchestrated(task, on_text, approve_bash):
    on_text("📋 Planning the task…")
    try:
        plan = planner.make_plan(task)
    except Exception as e:
        on_text(f"⚠️ Planning failed ({e}); falling back to the plain single loop.")
        session = gemini_agent.new_session()
        gemini_agent.add_user_text(session, task)
        gemini_agent.run_turn(session, on_text, approve_bash)
        return

    plan_view = "\n".join(
        f"  {i}. {p['step']}   (done when: {p['done_when']})"
        for i, p in enumerate(plan, 1))
    on_text(f"Plan — {len(plan)} steps:\n{plan_view}")

    # One session shared across steps: the executor remembers what it just did.
    session = gemini_agent.new_session()

    for i, item in enumerate(plan, 1):
        control.check()                       # bail before each step
        step, done_when = item["step"], item["done_when"]
        on_text(f"\n▶ Step {i}/{len(plan)}: {step}")
        ok = False

        for attempt in range(1, VERIFY_RETRIES + 1):
            control.check()                   # bail before each attempt
            instr = (
                f"Overall goal: {task}\n"
                f"Do ONLY this step now, then stop: {step}\n"
                f"This step is complete when: {done_when}"
            )
            if attempt > 1:
                instr = ("Your last attempt did NOT satisfy the success check. "
                         "Look at the current screen and try a different approach.\n" + instr)

            gemini_agent.add_user_text(session, instr)        # seeds a fresh screenshot
            gemini_agent.run_turn(session, on_text, approve_bash)

            control.check()                   # bail before the verify call
            ok, reason = verifier.verify(
                step, done_when, gemini_computer.screenshot_bytes())
            on_text(f"{'✅' if ok else '❌'} verify (attempt {attempt}/{VERIFY_RETRIES}): {reason}")
            if ok:
                break

        if not ok:
            on_text(f"⚠️ Step {i} couldn't be verified after {VERIFY_RETRIES} attempts — "
                    "continuing to the next step anyway.")

    on_text("\n🏁 Orchestrated run finished.")
