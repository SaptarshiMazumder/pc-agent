"""PlaywrightBrowserProvider — local Chromium via Playwright (the BrowserProvider
adapter; formerly browser.BrowserManager, behavior unchanged)."""

from __future__ import annotations

from pathlib import Path

from agentd.infrastructure.tools.browser.snapshot import (
    CONTENT_ROLES,
    DEFAULT_AI_SNAPSHOT_MAX_CHARS,
    INTERACTIVE_ROLES,
    STRUCTURAL_ROLES,
    _NETWORKIDLE_TIMEOUT_MS,
    _NODE_RE,
)


class PlaywrightBrowserProvider:
    """Lazy singleton owning the Playwright browser; lives on the gateway loop."""

    def __init__(self, config):
        import playwright  # noqa: F401  (raise ImportError early if missing)

        self.config = config
        self._pw = None
        self._browser = None
        self.context = None
        self.active_page = None
        self.ref_map: dict[str, dict] = {}

    async def ensure(self):
        if self.context is not None:
            return
        from playwright.async_api import async_playwright

        self._pw = await async_playwright().start()
        if getattr(self.config, "browser_persistent", True):
            # Persistent context: cookies/logins are saved to the profile dir and
            # reused across runs (the browser stays signed in). Log in once headed
            # via `python -m agentd.main.browser_login`.
            profile_dir = Path(self.config.state_dir) / "browser-profile"
            profile_dir.mkdir(parents=True, exist_ok=True)
            self.context = await self._pw.chromium.launch_persistent_context(
                str(profile_dir), headless=self.config.browser_headless
            )
            # a persistent context owns its own browser; it opens with one page
            self.active_page = (
                self.context.pages[0] if self.context.pages else await self.context.new_page()
            )
        else:  # ephemeral: a fresh, not-logged-in context each run
            self._browser = await self._pw.chromium.launch(headless=self.config.browser_headless)
            self.context = await self._browser.new_context()
            self.active_page = await self.context.new_page()

    async def close(self):
        if self.context is not None:
            try:
                await self.context.close()
            except Exception:
                pass
        if self._browser is not None:
            try:
                await self._browser.close()
            except Exception:
                pass
        if self._pw is not None:
            try:
                await self._pw.stop()
            except Exception:
                pass
        self.context = None
        self._browser = None
        self._pw = None
        self.active_page = None

    async def settle(self):
        """Best-effort wait for the network to go idle after nav/interaction."""
        try:
            await self.active_page.wait_for_load_state("networkidle", timeout=_NETWORKIDLE_TIMEOUT_MS)
        except Exception:
            pass

    # ----------------------------------------------------------- snapshot

    async def snapshot(
        self,
        *,
        interactive: bool = False,
        compact: bool = False,
        depth: int | None = None,
        max_chars: int = DEFAULT_AI_SNAPSHOT_MAX_CHARS,
    ) -> str:
        await self.ensure()
        page = self.active_page
        raw = await page.locator("body").aria_snapshot()
        self.ref_map = {}
        counter = 0
        seen: dict[tuple[str, str], int] = {}
        out_lines = []
        for line in raw.splitlines():
            m = _NODE_RE.match(line)
            if not m:
                out_lines.append(line)
                continue
            indent, role, name, rest = m.group(1), m.group(2), m.group(3), m.group(4)
            indent_depth = len(indent) // 2
            if depth is not None and indent_depth > depth:
                continue
            is_interactive = role in INTERACTIVE_ROLES
            if interactive and not is_interactive and role not in CONTENT_ROLES:
                continue
            if compact and role in STRUCTURAL_ROLES and not name:
                continue
            if is_interactive:
                counter += 1
                ref = f"e{counter}"
                key = (role, name or "")
                nth = seen.get(key, 0)
                seen[key] = nth + 1
                self.ref_map[ref] = {"role": role, "name": name or "", "nth": nth}
                name_part = f' "{name}"' if name else ""
                out_lines.append(f"{indent}- {role}{name_part}{rest} [ref={ref}]")
            else:
                out_lines.append(line)
        text = "\n".join(out_lines)
        if len(text) > max_chars:
            text = text[:max_chars] + "\n\n[...TRUNCATED - page too large; use mode=efficient or a higher limit]"
        title = await page.title()
        return f"Page: {title}\nURL: {page.url}\n\n{text}"

    def resolve_ref(self, ref: str):
        entry = self.ref_map.get(ref)
        if entry is None:
            raise ValueError(
                f"Unknown ref '{ref}'. Run a new snapshot and use a ref from that snapshot."
            )
        page = self.active_page
        loc = (
            page.get_by_role(entry["role"], name=entry["name"], exact=True)
            if entry["name"]
            else page.get_by_role(entry["role"])
        )
        return loc.nth(entry["nth"])
