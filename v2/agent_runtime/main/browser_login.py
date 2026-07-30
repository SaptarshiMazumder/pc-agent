"""One-time browser login — open the browser VISIBLY so you can sign into sites.

The `browser` tool runs headless during agent runs but shares a PERSISTENT profile
at ``<state_dir>/browser-profile``. Run this once to log in by hand; your cookies
are saved there and reused (signed in) by the headless browser tool afterwards.

    python -m agent_runtime.main.browser_login

Log into the sites you need (LinkedIn, Gmail, ...), then CLOSE the window to save.
Note: don't run this while the agent's browser is open — Chromium locks the profile.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from agent_runtime.config import load_config


async def _run() -> None:
    from playwright.async_api import async_playwright

    config = load_config()
    profile_dir = Path(config.state_dir) / "browser-profile"
    profile_dir.mkdir(parents=True, exist_ok=True)

    print(f"Opening a visible browser using profile:\n  {profile_dir}")
    print("Log into the sites you need (LinkedIn, Gmail, ...), then CLOSE the window to save.")

    from agent_runtime.infrastructure.tools.browser.providers.playwright import (
        launch_with_fallback,
        stealth_chromium_kwargs,
    )

    pw = await async_playwright().start()
    # Same stealth launch as the agent's browser (real Chrome channel + no automation
    # fingerprints) so Google et al. don't block the login with "browser not secure".
    kw = stealth_chromium_kwargs(config, headless=False, persistent=True)
    context = await launch_with_fallback(
        lambda **k: pw.chromium.launch_persistent_context(str(profile_dir), **k), kw
    )
    page = context.pages[0] if context.pages else await context.new_page()
    try:
        await page.goto("https://www.google.com")
    except Exception:
        pass  # a blank/failed start page is fine; the user navigates themselves

    closed = asyncio.Event()
    context.on("close", lambda *_: closed.set())  # fires when the user closes the window
    try:
        await closed.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    finally:
        try:
            await pw.stop()
        except Exception:
            pass
    print("Saved. The browser tool will now reuse this login while it stays valid.")


def main() -> None:
    if sys.platform == "win32":  # Playwright needs the Proactor loop on Windows
        asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())
    asyncio.run(_run())


if __name__ == "__main__":
    main()
