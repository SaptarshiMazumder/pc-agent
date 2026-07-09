"""PlaywrightBrowserProvider — local Chromium via Playwright (launches its OWN
browser with a persistent or ephemeral profile). All session behaviour lives in
BaseBrowserSession; this adapter only knows how to LAUNCH the context."""

from __future__ import annotations

import json
import logging
import os
import shutil
import sys
from pathlib import Path

from agentd.application.tool_models import browser_knob
from agentd.infrastructure.tools.browser.providers.base import BaseBrowserSession

log = logging.getLogger("agentd")

# Directories never worth copying when seeding from a real Chrome profile (caches /
# transient state — excluding them keeps the copy small and avoids most locked files).
_PROFILE_EXCLUDE = (
    "Cache",
    "Code Cache",
    "GPUCache",
    "DawnCache",
    "DawnGraphiteCache",
    "DawnWebGPUCache",
    "GraphiteDawnCache",
    "ShaderCache",
    "Service Worker",
    "component_crx_cache",
    "extensions_crx_cache",
    "Crashpad",
    "Crash Reports",
    "blob_storage",
)


def _chrome_user_data_dir() -> Path | None:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA")
        return Path(base) / "Google" / "Chrome" / "User Data" if base else None
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
    return Path.home() / ".config" / "google-chrome"


def resolve_chrome_profile(name_or_path: str) -> Path | None:
    """Resolve a Chrome profile by absolute path, dir name ("Default"/"Profile 1"),
    or display name (matched via the User Data 'Local State' info_cache)."""
    p = Path(name_or_path)
    if p.is_absolute() and p.is_dir():
        return p
    udd = _chrome_user_data_dir()
    if not udd or not udd.is_dir():
        return None
    if (udd / name_or_path).is_dir():
        return udd / name_or_path
    try:
        ls = json.loads((udd / "Local State").read_text(encoding="utf-8"))
        for d, info in (ls.get("profile", {}).get("info_cache", {}) or {}).items():
            if info.get("name") == name_or_path and (udd / d).is_dir():
                return udd / d
    except Exception:  # noqa: BLE001
        pass
    return None


def seed_profile_from_chrome(name_or_path: str, target: Path) -> bool:
    """Copy a real Chrome profile (cookies/logins, caches excluded) into `target`
    ONCE, so the browser reuses that login. Idempotent (skips if already seeded).
    Best-effort: a locked Cookies DB (Chrome running) is skipped with a warning."""
    if (target / "Default").exists():
        return True  # already seeded — reuse
    src = resolve_chrome_profile(name_or_path)
    if src is None:
        log.warning("browser: Chrome profile '%s' not found; using a fresh profile", name_or_path)
        return False
    target.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(
            src,
            target / "Default",
            ignore=shutil.ignore_patterns(*_PROFILE_EXCLUDE),
            dirs_exist_ok=True,
        )
    except shutil.Error as e:  # some files locked (Chrome open) — keep what copied
        log.warning(
            "browser: some profile files were skipped (close Chrome for a full copy): %s",
            str(e)[:200],
        )
    except Exception as e:  # noqa: BLE001
        log.warning("browser: profile copy failed (%s); using a fresh profile", e)
        return False
    # 'Local State' holds the DPAPI-bound cookie-encryption key — needed to decrypt
    # the copied cookies as the same OS user.
    try:
        shutil.copy2(src.parent / "Local State", target / "Local State")
    except Exception:  # noqa: BLE001
        pass
    log.info("browser: seeded profile from Chrome '%s' -> %s", name_or_path, target)
    return True


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
    channel = (browser_knob(config, "channel", "chrome") or "").strip()
    if channel:
        kw["channel"] = channel
    if browser_knob(config, "stealth", True):
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
                kw["channel"],
                e,
            )
            kw = {k: v for k, v in kw.items() if k != "channel"}
            return await launcher(**kw)
        raise


class PlaywrightBrowserProvider(BaseBrowserSession):
    """Lazy singleton owning a launched Playwright browser; lives on the gateway loop."""

    def __init__(self, config):
        import playwright  # noqa: F401  (raise ImportError early if missing -> factory omits the tool)

        super().__init__(config)
        self.mode = "persistent" if browser_knob(config, "persistent", True) else "ephemeral"

    async def _create_context(self):
        from playwright.async_api import async_playwright

        pw = await async_playwright().start()
        if self.mode == "persistent":
            # Persistent context: cookies/logins are saved to the profile dir and
            # reused across runs (the browser stays signed in). Log in once headed
            # via `python -m agentd.main.browser_login`, OR seed from an existing
            # Chrome profile via browser_chrome_profile (login reuse, no manual login).
            chrome_profile = browser_knob(self.config, "chrome_profile", None)
            if chrome_profile:
                profile_dir = Path(self.config.state_dir) / "browser-profile-imported"
                seed_profile_from_chrome(chrome_profile, profile_dir)
            else:
                profile_dir = Path(self.config.state_dir) / "browser-profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            kw = stealth_chromium_kwargs(
                self.config, headless=browser_knob(self.config, "headless", True), persistent=True
            )
            context = await launch_with_fallback(
                lambda **k: pw.chromium.launch_persistent_context(str(profile_dir), **k), kw
            )
        else:  # ephemeral: a fresh, not-logged-in context each run
            kw = stealth_chromium_kwargs(
                self.config, headless=browser_knob(self.config, "headless", True), persistent=False
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
            "headless": browser_knob(self.config, "headless", True),
            "hint": "Log in once headed via `python -m agentd.main.browser_login`. To drive your "
            "LIVE Chrome instead, set plugins.browser.tools.browser.cdp_url in config.",
        }
