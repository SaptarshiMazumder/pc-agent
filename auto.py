"""
CLI for the autonomous tool-calling agent (brain.py).

  python auto.py "create a python venv in C:/tmp/demo and install requests in it"

Add --orchestrated (or -o) to run plan -> execute -> verify -> retry over the
task instead of a single bare agent loop:

  python auto.py -o "research the top 3 python web frameworks and write a summary.md"

No argument -> interactive. 'reset' clears history, 'quit' exits.
Ctrl+C cancels the running task (twice = force-quit).
"""
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")   # never crash on emoji on cp1252
except Exception:
    pass

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import control
import brain

# --orchestrated/-o -> plan->execute->verify->retry; else a single agent loop.
_ARGS = list(sys.argv[1:])
ORCHESTRATED = any(f in _ARGS for f in ("--orchestrated", "-o"))
_ARGS = [a for a in _ARGS if a not in ("--orchestrated", "-o")]

# Reflection: let the pro strategist pick the simplest approach before executing.
REFLECT = os.getenv("REFLECT", "1").lower() not in ("0", "false", "no")


def on_text(text):
    print(f"\n🤖 {text}\n")


def approve(desc):
    print(f"\n🔧 Allow this?\n    {desc}")
    return input("   [y/N] ").strip().lower() in ("y", "yes")


def run(session, text):
    control.reset()
    err = {}

    def work():
        try:
            if ORCHESTRATED:
                import brain_orchestrator
                brain_orchestrator.run_orchestrated(text, on_text, approve)
                return
            guidance = None
            if REFLECT:
                try:
                    import strategist
                    on_text("🧠 choosing the simplest approach…")
                    ap = strategist.choose_approach(text)
                    guidance = (f"Approach: {ap['approach']}\n"
                                f"Why: {ap.get('why', '')}\n"
                                f"Avoid: {ap.get('avoid', '')}")
                    on_text(f"Approach: {ap['approach']}")
                except control.Cancelled:
                    raise
                except Exception as e:       # strategy is best-effort
                    on_text(f"(reflection skipped: {e})")
            brain.run_agent(text, on_text, approve, session, guidance=guidance)
        except control.Cancelled:
            print("\n🛑 cancelled.")
        except Exception as e:               # noqa: BLE001
            err["e"] = e

    t = threading.Thread(target=work, daemon=True)
    t.start()
    try:
        while t.is_alive():
            t.join(0.2)
    except KeyboardInterrupt:
        control.request_stop()
        print("\n🛑 cancelling… (Ctrl+C again to force-quit)")
        try:
            while t.is_alive():
                t.join(0.2)
        except KeyboardInterrupt:
            print("\n💥 force-quitting.")
            os._exit(130)
    if "e" in err:
        print(f"💥 {err['e']}")


def main():
    mode = f"brain={brain.BRAIN_MODEL}" + (", orchestrated" if ORCHESTRATED else "")
    print(f"pc-agent autonomous ({mode}).")
    session = brain.new_session()
    if _ARGS:
        run(session, " ".join(_ARGS))
        return

    print("Type a task. 'reset' clears history, 'quit' exits. Ctrl+C cancels.\n")
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
        if text.lower() in ("reset", "clear"):
            session = brain.new_session()
            print("🧹 reset")
            continue
        run(session, text)


if __name__ == "__main__":
    main()
