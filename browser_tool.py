"""
DOM-driven web browsing via browser-use (Chromium/CDP under the hood).

Reads the page's DOM/accessibility tree and acts on elements (not pixels), so it
works on any site without fighting coordinates.

Key to working on auth-walled / anti-bot sites (the GENERAL fix, no per-site
code): a PERSISTENT browser profile. Cookies and logins survive across runs, so
the agent browses as a logged-in human rather than a fresh, fingerprint-obvious
bot — which is what gets captcha'd / login-walled. Log into your sites once (run
with BROWSER_HEADLESS=false) and every later run is authenticated.

Env:
  BROWSER_MODEL          model driving the browser sub-agent (default gemini-2.5-flash)
  BROWSER_HEADLESS       false to show the window (needed to log in the first time)
  BROWSER_USER_DATA_DIR  persistent profile dir (default ~/.pc_agent_browser)
  CHROME_PATH            path to a real Chrome binary (optional; more human fingerprint)
  BROWSE_MAX_STEPS       cap browser steps per task (default 25)
"""
import asyncio
import os
from pathlib import Path


def browse_web(task: str) -> str:
    from browser_use import Agent, Browser, ChatGoogle

    model = os.getenv("BROWSER_MODEL", "gemini-2.5-flash")
    key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
    headless = os.getenv("BROWSER_HEADLESS", "true").lower() not in ("0", "false", "no")
    max_steps = int(os.getenv("BROWSE_MAX_STEPS", "25"))

    profile = os.getenv("BROWSER_USER_DATA_DIR") or str(Path.home() / ".pc_agent_browser")
    Path(profile).mkdir(parents=True, exist_ok=True)

    browser_kwargs = {"headless": headless, "user_data_dir": profile}
    chrome = os.getenv("CHROME_PATH")
    if chrome:
        browser_kwargs["executable_path"] = chrome

    llm = ChatGoogle(model=model, api_key=key)
    browser = Browser(**browser_kwargs)
    agent = Agent(
        task=task,
        llm=llm,
        browser=browser,
        enable_signal_handler=False,   # we run inside a worker thread (watchdog)
    )

    async def _run():
        return await agent.run(max_steps=max_steps)

    try:
        hist = asyncio.run(_run())
    except Exception as e:                       # noqa: BLE001
        return f"[browse_web error: {e}]"

    final = getattr(hist, "final_result", None)
    if callable(final):
        try:
            r = final()
            if r:
                return str(r)[:6000]
        except Exception:
            pass
    return str(hist)[:6000]
