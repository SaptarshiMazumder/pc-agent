"""
Local CLI driver — run the agent straight from your terminal, no Discord.

  python local.py "open youtube, go to my watch history, tell me the last 5 videos"

With no argument it's interactive: type a task, 'reset' to clear, 'quit' to exit.

Backend is auto-selected:
  - GEMINI_API_KEY (or GOOGLE_API_KEY) set  -> Gemini computer-use
  - otherwise                                -> Anthropic
Force it with BACKEND=gemini or BACKEND=anthropic in your .env.
"""
import os
import sys


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


def on_text(text):
    print(f"\n🤖 {text}\n")


def approve_bash(desc):
    """Console version of the Discord approval gate (bash / sensitive actions)."""
    print(f"\n🔧 Allow this?\n    {desc}")
    return input("   [y/N] ").strip().lower() in ("y", "yes")


def run(session, text):
    add_user_text(session, text)
    try:
        be.run_turn(session, on_text, approve_bash)
    except Exception as e:
        print(f"💥 {e}")


def main():
    print(f"pc-agent (local, backend={BACKEND}).")
    session = new_session()

    if len(sys.argv) > 1:
        run(session, " ".join(sys.argv[1:]))
        return

    print("Type a task. 'reset' clears history, 'quit' exits.")
    print("Heads up: this drives your REAL mouse/keyboard. Slam the mouse into a")
    print("screen corner to abort instantly.\n")
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
