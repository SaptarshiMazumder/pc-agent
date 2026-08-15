"""What a rendered window looks like when it is broken — as pure rules over one observation.

NO BROWSER IN HERE. The service drives a real page and hands the result down as a
``PageObservation``; everything that decides whether that result is GOOD lives here, where it can
be tested with a dict instead of Chromium. The same split as every other rule set in this bundle.

WHY THESE CHECKS AND NOT OTHERS. Each one is a way a window fails while looking, to the author,
exactly like a window that works — the build succeeds, the file is served, and the screen is
wrong or empty:

  a 404 on a chunk        the single commonest cause of a blank window. Almost always an
                          absolute asset path where the app is served under /apps/<id>/.
  an uncaught error       a crash during mount. React renders nothing and says nothing.
  a console error         the app caught it, so the page still draws — and the feature is dead.
  an empty body           it mounted and produced no content: the shell of an app.
  the socket never opened the window is a static page pretending to be an agent.
  horizontal scroll       something is wider than the viewport. The layout is broken, and a
                          screenshot alone does not reliably show it.

None of them can tell whether the agent does what the USER asked. That is what driving it
afterwards is for, and nothing here pretends otherwise.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .finding import ERROR, INFO, WARN, Finding

#: A page that draws less than this has not rendered a UI — it is a mount point and a spinner at
#: best. Deliberately tiny: the check is for "nothing at all", not for "not enough".
MIN_RENDERED_TEXT = 8

#: Console noise that is not the app's fault. Kept SHORT on purpose — a broad filter here would
#: silence the errors this tool exists to surface.
IGNORED_CONSOLE = (
    "favicon.ico",  # every page without one, in every browser
    "Download the React DevTools",
)


@dataclass(frozen=True)
class FailedRequest:
    url: str
    status: int = 0  # 0 = the request never completed (connection refused, blocked)
    reason: str = ""


#: The SDK sign-in gate's element ids. A CONTRACT, not a guess: the desktop shell's end-to-end
#: login hook already drives these, which is why gate.ts refuses to rename them.
GATE_IDS = ("gate", "gateForm", "gateEmail", "gatePass")


@dataclass
class PageObservation:
    """Everything the driver saw. A value object — the driver fills it, the rules read it."""

    url: str = ""
    #: Is the SDK's sign-in gate on screen? A page stopped there has not failed — it has not
    #: been CHECKED, and those are different results with different next actions.
    sign_in_gate: bool = False
    #: Visible text of <body>. The one honest answer to "did anything render".
    text: str = ""
    #: Uncaught exceptions (pageerror). A crash on mount lands here and nowhere else.
    page_errors: list[str] = field(default_factory=list)
    #: console.error / console.warn lines, verbatim.
    console_errors: list[str] = field(default_factory=list)
    failed_requests: list[FailedRequest] = field(default_factory=list)
    #: Did a WebSocket to the daemon open? None = the driver could not tell.
    socket_open: bool | None = None
    scroll_width: int = 0
    viewport_width: int = 0
    #: Visible interactive elements, as "<tag>: <label>" — what the model can drive next.
    controls: list[str] = field(default_factory=list)
    #: Where the screenshot was written, if one was taken.
    screenshot: str = ""


def check_page(obs: PageObservation) -> list[Finding]:
    """Every problem visible in one page load, worst first.

    A GATED PAGE PRODUCES NO ERRORS. When the sign-in gate is up, the app never mounted — so
    "nothing rendered" and "no socket" are descriptions of the gate, not defects in the agent.
    Reporting them cost a real verification run a FAILED verdict about an app that had not been
    given the chance to load, which is the worst output this tool can produce: it sends the
    author to fix code that was never run.
    """
    out: list[Finding] = []
    out += _failed_requests(obs)
    out += _errors(obs)
    if obs.sign_in_gate:
        return out + [
            Finding(
                level=INFO,
                code="APP_SIGN_IN_REQUIRED",
                message="the sign-in gate is on screen, so the app itself was never reached — "
                "nothing behind it has been checked",
                path="",
                fix="this is NOT a defect in the agent. Either the daemon advertises an accounts "
                "service (verification normally skips the gate), or sign in by passing email + "
                "password to verify_app",
            )
        ]
    out += _rendered(obs)
    out += _socket(obs)
    out += _layout(obs)
    return out


def _failed_requests(obs: PageObservation) -> list[Finding]:
    out = []
    for req in obs.failed_requests:
        # The asset case gets its own wording because the fix is specific and unguessable from
        # the raw 404: it is nearly always an absolute path in a page served from a subpath.
        is_asset = any(req.url.endswith(ext) for ext in (".js", ".css", ".mjs"))
        out.append(
            Finding(
                level=ERROR,
                code="APP_ASSET_MISSING" if is_asset else "APP_REQUEST_FAILED",
                message=f"{req.url} did not load"
                + (f" (HTTP {req.status})" if req.status else f" ({req.reason or 'no response'})"),
                path="ui/",
                fix=(
                    "the app is served under /apps/<id>/, so an absolute '/assets/…' asks the "
                    "daemon root and 404s — set base: './' in vite.config.ts and rebuild"
                )
                if is_asset
                else "",
            )
        )
    return out


def _errors(obs: PageObservation) -> list[Finding]:
    out = []
    for message in obs.page_errors:
        out.append(
            Finding(
                level=ERROR,
                code="APP_CRASHED",
                message=f"uncaught error while loading: {message}",
                path="ui/",
                fix="the window renders nothing after this — fix it before anything else",
            )
        )
    for message in obs.console_errors:
        if any(skip in message for skip in IGNORED_CONSOLE):
            continue
        out.append(
            Finding(
                level=WARN,
                code="APP_CONSOLE_ERROR",
                message=f"console error: {message}",
                path="ui/",
                # WARN, not ERROR: the page drew. But something it tried to do failed, and a
                # feature that fails quietly is what a user reports as "it does nothing".
                fix="the page still renders, so this is a feature that is dead rather than a "
                "window that is broken — check what stopped working",
            )
        )
    return out


def _rendered(obs: PageObservation) -> list[Finding]:
    if len(obs.text.strip()) >= MIN_RENDERED_TEXT:
        return []
    return [
        Finding(
            level=ERROR,
            code="APP_BLANK",
            message="the window rendered nothing — the page loaded but the body is empty",
            path="ui/",
            fix="usually a crash on mount or a missing bundle; read the errors above first, and "
            "if there are none, check that index.html actually loads your entry script",
        )
    ]


def _socket(obs: PageObservation) -> list[Finding]:
    if obs.socket_open is not False:
        return []
    return [
        Finding(
            level=ERROR,
            code="APP_NOT_CONNECTED",
            message="the page never opened a socket to the daemon",
            path="ui/",
            fix="without it the window is a static page: nothing streams, no tool runs, and "
            "every panel stays empty. Check the app calls fromPage() and connects on mount",
        )
    ]


def _layout(obs: PageObservation) -> list[Finding]:
    # A couple of pixels of slack: sub-pixel rounding makes an exact comparison flap.
    if not obs.viewport_width or obs.scroll_width <= obs.viewport_width + 2:
        return []
    return [
        Finding(
            level=WARN,
            code="APP_OVERFLOWS",
            message=f"the page is {obs.scroll_width}px wide in a {obs.viewport_width}px window — "
            f"something overflows and the whole layout scrolls sideways",
            path="ui/",
            fix="usually one unbreakable string (a long filename, a URL) in a text block: "
            "overflow-wrap: anywhere on the text, and overflow-x: hidden on its container",
        )
    ]
