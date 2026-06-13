"""
Local CLI driver — run the agent straight from your terminal, no Discord.

  python local.py "open youtube, go to my watch history, tell me the last 5 videos"

With no argument it's interactive: type a task, 'reset' to clear, 'quit' to exit.

Backend is auto-selected:
  - GEMINI_API_KEY (or GOOGLE_API_KEY) set  -> Gemini computer-use
  - otherwise                                -> Anthropic
Force it with BACKEND=gemini or BACKEND=anthropic in your .env.

The Gemini backend runs the plan -> execute -> verify -> retry ORCHESTRATOR by
default. Add --plain (or -p) to fall back to the bare single loop.

Cancellation: press Ctrl+C to stop the current task promptly — it unwinds at the
next checkpoint (between actions). Press Ctrl+C twice to force-quit. You can also
slam the mouse into a screen corner for an instant physical abort (pyautogui).
"""
import os
import sys
import threading

try:
    sys.stdout.reconfigure(encoding="utf-8")   # never crash on emoji on cp1252
except Exception:
    pass

import control


def _pick_backend():
    forced = os.getenv("BACKEND", "").lower()
    if forced in ("gemini", "anthropic"):
        return forced
    if os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY"):
        return "gemini"
    return "anthropic"


# .env is loaded by whichever backend module we import; load it up front so the
# backend choice can see the keys.
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BACKEND = _pick_backend()

if BACKEND == "gemini":
    import gemini_agent as be

    def new_session():
        return be.new_session()

    def add_user_text(session, text):
        be.add_user_text(session, text)

else:  # anthropic — config.py needs Discord vars at import; give placeholders
    os.environ.setdefault("DISCORD_TOKEN", "unused-local")
    os.environ.setdefault("DISCORD_OWNER_ID", "0")
    import agent as be

    def new_session():
        return []

    def add_user_text(session, text):
        session.append({"role": "user", "content": text})


# --- flags: orchestrated is the default; --plain/-p opts out. (-o/--orchestrated
#     are still accepted but are now no-ops since it's the default.) ---
_ARGS = list(sys.argv[1:])
_PLAIN = any(f in _ARGS for f in ("--plain", "-p"))
_ARGS = [a for a in _ARGS if a not in ("--plain", "-p", "--orchestrated", "-o")]
ORCHESTRATED = (BACKEND == "gemini") and not _PLAIN   # anthropic = always plain


def on_text(text):
    print(f"\n🤖 {text}\n")


def approve_bash(desc):
    """Console version of the Discord approval gate (bash / sensitive actions)."""
    print(f"\n🔧 Allow this?\n    {desc}")
    return input("   [y/N] ").strip().lower() in ("y", "yes")


def _do(session, text):
    if ORCHESTRATED:
        import orchestrator                 # lazy: only Gemini path needs it
        orchestrator.run_orchestrated(text, on_text, approve_bash)
    else:
        add_user_text(session, text)
        be.run_turn(session, on_text, approve_bash)


def run(session, text):
    """Run one task in a worker thread so Ctrl+C on the main thread is prompt."""
    control.reset()
    err = {}

    def work():
        try:
            _do(session, text)
        except control.Cancelled:
            print("\n🛑 cancelled.")
        except Exception as e:               # noqa: BLE001 — surface anything
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
    mode = f"backend={BACKEND}" + (", orchestrated" if ORCHESTRATED else ", plain")
    print(f"pc-agent (local, {mode}).")
    session = new_session()

    if _ARGS:
        run(session, " ".join(_ARGS))
        return

    print("Type a task. 'reset' clears history, 'quit' exits.")
    print("Ctrl+C cancels a running task (twice = force-quit). Mouse to a screen")
    print("corner = instant abort.\n")
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
            session = new_session()
            print("🧹 reset")
            continue
        run(session, text)


if __name__ == "__main__":
    main()
