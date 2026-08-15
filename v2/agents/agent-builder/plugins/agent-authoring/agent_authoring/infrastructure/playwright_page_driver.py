"""PlaywrightPageDriver — the real browser behind VerifyAppService.

A FRESH HEADLESS PAGE, never the user's browser. The shared `browser` tool deliberately drives a
signed-in profile, which is right for reading a private dashboard and wrong for this: an app
verified through somebody's logged-in session with their extensions and their cookies is not the
app a new user opens.

Everything here is observation. Not one decision about whether what it saw is acceptable — those
live in domain/app_checks.py, which is why they can be tested without Chromium.
"""

from __future__ import annotations

import time
from pathlib import Path

from agent_authoring.application.verify_app_service import Step
from agent_authoring.domain.app_checks import GATE_IDS, FailedRequest, PageObservation

#: A React app mounts in tens of milliseconds; a socket handshake and a first render take longer.
#: This is the wait after `load` before anything is judged — too short and every app is "blank".
SETTLE_MS = 2500

#: One step's own wait. Clicking Refresh on a dashboard means a tool call over the socket.
STEP_SETTLE_MS = 1500

VIEWPORT = {"width": 1280, "height": 860}

#: Screenshots are DOWNSCALED before they leave here, and the reason is not disk space.
#:
#: A tool's declared artifact becomes a base64 image block inside the conversation, and it stays
#: there — re-sent on every later turn for the rest of the session. One full-size PNG of this
#: viewport is ~114KB, which is what a real build hit: two verifications and the transcript was
#: mostly pictures, then the model started returning empty responses because the context was
#: full. A JPEG at this width is ~15KB and shows the same layout problems.
SHOT_WIDTH = 700
SHOT_QUALITY = 60


class PlaywrightPageDriver:
    def __init__(self, screenshot_dir: Path, label: str = "app"):
        self._dir = Path(screenshot_dir)
        self._label = label
        self._pw = None
        self._browser = None
        self._page = None
        self._errors: list[str] = []
        self._console: list[str] = []
        self._failed: list[FailedRequest] = []
        self._socket_open: bool | None = None
        self._shots = 0

    # -- lifecycle ---------------------------------------------------------------------

    def _start(self):
        try:
            from playwright.sync_api import sync_playwright
        except ImportError as e:  # pragma: no cover - environment
            raise RuntimeError(
                "playwright is not installed in this runtime, so the window cannot be opened"
            ) from e

        self._pw = sync_playwright().start()
        try:
            self._browser = self._pw.chromium.launch(headless=True)
        except Exception as e:  # noqa: BLE001 - the message IS the fix
            self._pw.stop()
            self._pw = None
            raise RuntimeError(
                f"could not start a headless browser ({e}). The browser binaries are a separate "
                f"download from the python package: run `playwright install chromium`."
            ) from e

        # LAY OUT BIG, CAPTURE SMALL. The page still measures 1280px wide, so wrapping and
        # overflow are exactly what a user at that size would see; only the captured pixels
        # shrink. Resizing the VIEWPORT instead would test a different layout.
        self._page = self._browser.new_page(
            viewport=VIEWPORT, device_scale_factor=SHOT_WIDTH / VIEWPORT["width"]
        )
        page = self._page
        page.on("pageerror", lambda e: self._errors.append(str(e)))
        page.on(
            "console",
            lambda m: self._console.append(m.text) if m.type in ("error", "warning") else None,
        )
        page.on(
            "requestfailed",
            lambda r: self._failed.append(
                FailedRequest(url=r.url, reason=(r.failure or "") if isinstance(r.failure, str) else "")
            ),
        )
        page.on("response", self._note_response)
        # The socket is the difference between an agent window and a static page, and nothing
        # else observes it: a failed WS shows up as neither a console error nor a failed request.
        page.on("websocket", lambda _ws: setattr(self, "_socket_open", True))

    def _note_response(self, response) -> None:
        if response.status >= 400:
            self._failed.append(FailedRequest(url=response.url, status=response.status))

    def close(self) -> None:
        for closer in (self._page, self._browser):
            if closer is not None:
                try:
                    closer.close()
                except Exception:  # noqa: BLE001 - teardown must not mask the verdict
                    pass
        if self._pw is not None:
            try:
                self._pw.stop()
            except Exception:  # noqa: BLE001
                pass
        self._page = self._browser = self._pw = None

    # -- driving -----------------------------------------------------------------------

    def open(self, url: str) -> PageObservation:
        self._start()
        self._socket_open = False  # now observable: no `websocket` event means none opened
        self._page.goto(url, wait_until="load", timeout=30_000)
        self._page.wait_for_timeout(SETTLE_MS)
        return self._observe(url)

    def drive(self, steps: list[Step]) -> PageObservation:
        for step in steps:
            self._one(step)
        self._page.wait_for_timeout(STEP_SETTLE_MS)
        return self._observe(self._page.url)

    def sign_in(self, email: str, password: str) -> PageObservation:
        """Drive the SDK's gate. Its ids are a contract (gate.ts refuses to rename them because
        the desktop's end-to-end login hook drives the same ones), so this is not scraping."""
        self._page.fill("#gateEmail", email, timeout=10_000)
        self._page.fill("#gatePass", password, timeout=10_000)
        self._page.press("#gatePass", "Enter")
        # A round trip to the accounts service, then the app mounts and opens its socket.
        self._page.wait_for_timeout(SETTLE_MS * 2)
        return self._observe(self._page.url)

    def _one(self, step: Step) -> None:
        action = (step.action or "").lower()
        if action == "wait":
            self._page.wait_for_timeout(int(step.text or 1000))
            return
        if action == "press":
            self._page.keyboard.press(step.target or "Enter")
            return
        locator = self._locate(step.target)
        if action == "click":
            locator.click(timeout=10_000)
        elif action == "type":
            locator.fill(step.text, timeout=10_000)
        else:
            raise ValueError(f"unknown step action {step.action!r}")
        self._page.wait_for_timeout(STEP_SETTLE_MS)

    def _locate(self, target: str):
        """Visible text first, CSS second. A model knows what a button SAYS; it is guessing when
        it writes a selector, and a wrong selector fails as 'element not found' — which reads
        like the control is missing rather than like the query was wrong."""
        by_text = self._page.get_by_text(target, exact=False)
        try:
            if by_text.count() > 0:
                return by_text.first
        except Exception:  # noqa: BLE001 - fall through to CSS
            pass
        return self._page.locator(target).first

    # -- observation -------------------------------------------------------------------

    def _observe(self, url: str) -> PageObservation:
        page = self._page
        text = page.evaluate("() => document.body ? document.body.innerText : ''") or ""
        metrics = page.evaluate(
            "() => ({scroll: document.documentElement.scrollWidth, view: window.innerWidth})"
        )
        controls = page.evaluate(
            """() => Array.from(
                 document.querySelectorAll('button, a[href], input, textarea, select')
               )
               .filter(el => el.offsetParent !== null)
               .slice(0, 40)
               .map(el => `${el.tagName.toLowerCase()}: ${
                 (el.innerText || el.getAttribute('placeholder') ||
                  el.getAttribute('aria-label') || el.getAttribute('title') || '').trim().slice(0, 60)
               }`)"""
        )
        # The gate, by its contract ids. Checked BEFORE anything is judged, because a gated page
        # legitimately has no content and no socket — and calling that a defect blames the agent
        # for a login it never got past.
        gated = bool(
            page.evaluate(
                "(ids) => ids.some(id => !!document.getElementById(id))", list(GATE_IDS)
            )
        )
        return PageObservation(
            url=url,
            sign_in_gate=gated,
            text=text,
            page_errors=list(self._errors),
            console_errors=list(self._console),
            failed_requests=list(self._failed),
            socket_open=self._socket_open,
            scroll_width=int(metrics.get("scroll") or 0),
            viewport_width=int(metrics.get("view") or 0),
            controls=[c for c in controls if c],
            screenshot=self._screenshot(),
        )

    def _screenshot(self) -> str:
        self._dir.mkdir(parents=True, exist_ok=True)
        self._shots += 1
        stamp = time.strftime("%H%M%S")
        path = self._dir / f"{self._label}-{stamp}-{self._shots}.jpg"
        try:
            # A JPEG, scaled down by the DEVICE ratio rather than by resizing after the fact —
            # the page still lays out at the real viewport width (so overflow and wrapping are
            # what a user would see) and only the pixels shrink.
            self._page.screenshot(path=str(path), full_page=False, type="jpeg", quality=SHOT_QUALITY)
        except Exception:  # noqa: BLE001 - a missing screenshot must not lose the findings
            return ""
        return str(path)
