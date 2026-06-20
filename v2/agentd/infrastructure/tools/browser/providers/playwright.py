"""PlaywrightBrowserProvider — local Chromium via Playwright (launches its OWN
browser with a persistent or ephemeral profile). All session behaviour lives in
BaseBrowserSession; this adapter only knows how to LAUNCH the context."""

from __future__ import annotations

import logging
from pathlib import Path

from agentd.infrastructure.tools.browser.providers.base import BaseBrowserSession

log = logging.getLogger("agentd")


def stealth_chromium_kwargs(config, *, headless: bool, persistent: bool) -> dict:
    """Launch kwargs that (a) prefer the installed Chrome channel and (b) strip
    Playwright's automation fingerprints — so logins on automation-sensitive sites
    (Google's "this browser is not secure") work. Shared by the provider and the
    headed login entrypoint."""
    # chromium_sandbox=True keeps Chrome's sandbox ON. Playwright DEFAULTS it off,
    # which injects --no-sandbox and triggers Chrome's "unsupported command-line
    # flag / stability and security will suffer" warning bar. On = no banner + safer.
    kw: dict = {"headless": headless, "chromium_sandbox": True}
    if persistent:
        kw["accept_downloads"] = True
    channel = (getattr(config, "browser_channel", "chrome") or "").strip()
    if channel:
        kw["channel"] = channel
    if getattr(config, "browser_stealth", True):
        kw["args"] = ["--disable-blink-features=AutomationControlled"]
        kw["ignore_default_args"] = ["--enable-automation"]
    return kw


async def launch_with_fallback(launcher, kw: dict):
    """Call an async Playwright launcher with `kw`; if a requested `channel` (real
    Chrome) isn't installed, retry once with bundled Chromium."""
    try:
        return await launcher(**kw)
    except Exception as e:  # noqa: BLE001
        if "channel" in kw:
            log.warning(
                "browser channel '%s' unavailable (%s); falling back to bundled Chromium",
                kw["channel"], e,
            )
            kw = {k: v for k, v in kw.items() if k != "channel"}
            return await launcher(**kw)
        raise


class PlaywrightBrowserProvider(BaseBrowserSession):
    """Lazy singleton owning a launched Playwright browser; lives on the gateway loop."""

    def __init__(self, config):
        import playwright  # noqa: F401  (raise ImportError early if missing -> factory omits the tool)

        super().__init__(config)
        self.mode = "persistent" if getattr(config, "browser_persistent", True) else "ephemeral"

    async def _create_context(self):
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        if self.mode == "persistent":
            # Persistent context: cookies/logins are saved to the profile dir and
            # reused across runs (the browser stays signed in). Log in once headed
            # via `python -m agentd.main.browser_login`.
            profile_dir = Path(self.config.state_dir) / "browser-profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            kw = stealth_chromium_kwargs(
                self.config, headless=self.config.browser_headless, persistent=True
            )
            context = await launch_with_fallback(
                lambda **k: pw.chromium.launch_persistent_context(str(profile_dir), **k), kw
            )
        else:  # ephemeral: a fresh, not-logged-in context each run
            kw = stealth_chromium_kwargs(
                self.config, headless=self.config.browser_headless, persistent=False
            )
            self._browser = await launch_with_fallback(pw.chromium.launch, kw)
            context = await self._browser.new_context(accept_downloads=True)
        return pw, context

    async def profiles(self) -> dict:
        profile_dir = Path(self.config.state_dir) / "browser-profile"
        return {
            "active": self.mode,
            "available": ["persistent", "ephemeral"],
            "profileDir": str(profile_dir) if self.mode == "persistent" else None,
            "headless": self.config.browser_headless,
            "hint": "Log in once headed via `python -m agentd.main.browser_login`. "
                    "To drive your LIVE Chrome instead, set AGENTD_BROWSER_CDP_URL.",
        }
