"""Agent Builder must be able to LOOK at the window it built.

`validate_agent` proves an agent is well-formed. `agentd ask` proves its brain runs. Neither one
opens the screen — and the screen is the part that can be perfectly built, perfectly served, and
blank. Every failure pinned here looks like success from the author's side: the build printed no
errors and the files are on disk.

The browser is injected, so every rule below is checked against a plain observation. A test suite
that needed Chromium would be skipped on the machine that most needs it.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from agent_authoring.application.verify_app_service import (
    Step,
    VerifyAppService,
    VerifyError,
)
from agent_authoring.domain.app_checks import FailedRequest, PageObservation, check_page

# --------------------------------------------------------------------------- the rules


def _healthy(**over) -> PageObservation:
    base = dict(
        url="http://127.0.0.1:8787/apps/x/",
        text="Build a workflow  Ask anything",
        socket_open=True,
        scroll_width=1280,
        viewport_width=1280,
    )
    base.update(over)
    return PageObservation(**base)


def _codes(obs: PageObservation) -> set[str]:
    return {f.code for f in check_page(obs)}


def test_a_working_window_reports_nothing():
    """The rules have to be quiet on a good page, or every real finding is noise."""
    assert check_page(_healthy()) == []


def test_a_missing_chunk_is_an_error_and_names_the_cause():
    """THE COMMONEST BLANK WINDOW: an absolute /assets/ path in a page served from /apps/<id>/.
    The raw 404 does not suggest that; the finding has to."""
    obs = _healthy(failed_requests=[FailedRequest(url="/assets/index-a1.js", status=404)])
    finding = next(f for f in check_page(obs) if f.code == "APP_ASSET_MISSING")

    assert finding.is_error
    assert "base: './'" in finding.fix


def test_a_crash_on_mount_is_an_error():
    """React renders nothing and says nothing. Without this the only symptom is an empty page."""
    assert "APP_CRASHED" in _codes(_healthy(page_errors=["TypeError: x is not a function"]))


def test_a_console_error_is_a_warning_not_a_failure():
    """The page drew, so the window is not broken — but a feature is dead, which is what a user
    reports as 'it does nothing'. Failing the whole verification for it would train the model to
    ignore the result."""
    findings = check_page(_healthy(console_errors=["failed to load workflows"]))

    assert [f.code for f in findings] == ["APP_CONSOLE_ERROR"]
    assert not findings[0].is_error


def test_browser_noise_is_not_reported():
    """A filter that is too broad silences the errors this exists to surface, so it is short and
    every entry is something no app can prevent."""
    assert check_page(_healthy(console_errors=["/favicon.ico 404 (Not Found)"])) == []


def test_a_blank_page_is_an_error():
    assert "APP_BLANK" in _codes(_healthy(text="   "))


def test_a_page_that_never_opened_a_socket_is_an_error():
    """Without it the window is a static page pretending to be an agent: nothing streams, no
    tool runs, every panel stays empty. It shows up as neither a console error nor a failed
    request, so nothing else would catch it."""
    assert "APP_NOT_CONNECTED" in _codes(_healthy(socket_open=False))


def test_an_unknown_socket_state_is_not_reported():
    """A driver that cannot tell must not produce a finding — inventing one teaches the model to
    chase a bug that may not exist."""
    assert check_page(_healthy(socket_open=None)) == []


def test_a_page_wider_than_its_window_is_a_warning():
    """One unbreakable filename in a text block scrolls the whole layout sideways. A screenshot
    does not reliably show it; the numbers do."""
    finding = next(
        f for f in check_page(_healthy(scroll_width=1900)) if f.code == "APP_OVERFLOWS"
    )
    assert "overflow-wrap" in finding.fix


def test_sub_pixel_rounding_does_not_flap():
    assert check_page(_healthy(scroll_width=1281)) == []


# --------------------------------------------------------------------------- the service


class FakeReader:
    def __init__(self, root: Path):
        self._root = root

    def agent_dir(self, agent_id: str):
        d = self._root / agent_id
        return d if d.is_dir() else None

    def known_ids(self):
        return ["known"]


class FakeDriver:
    def __init__(
        self,
        first: PageObservation,
        after: PageObservation | None = None,
        after_sign_in: PageObservation | None = None,
    ):
        self.first, self.after, self.after_sign_in = first, after, after_sign_in
        self.opened = ""
        self.steps: list[Step] = []
        self.credentials: tuple[str, str] | None = None
        self.closed = False
        self.want_shot = False

    def open(self, url: str) -> PageObservation:
        self.opened = url
        return self.first

    def drive(self, steps):
        self.steps = list(steps)
        return self.after or self.first

    def sign_in(self, email, password):
        self.credentials = (email, password)
        return self.after_sign_in or self.first

    def close(self):
        self.closed = True


class FakeGateway:
    host, port, token, pid, version, started_at = "127.0.0.1", 8787, "tok", 1, "0", ""


def _agent(tmp_path: Path, toml: str = '[app]\nentry = "ui/index.html"\n') -> Path:
    d = tmp_path / "known"
    (d / "ui").mkdir(parents=True)
    (d / "ui" / "index.html").write_text("<html></html>", encoding="utf-8")
    (d / "agent.toml").write_text(toml, encoding="utf-8")
    return d


def _service(tmp_path, driver, gateway=FakeGateway()):
    def factory(screenshot: bool):
        # The factory takes the screenshot decision — the cost is in TAKING the image, so the
        # driver is told before it looks at anything.
        driver.want_shot = screenshot
        return driver

    return VerifyAppService(
        FakeReader(tmp_path),
        driver_factory=factory,
        gateway_reader=lambda: gateway,
        screenshot_dir=tmp_path / "shots",
    )


def test_it_opens_the_agents_own_window(tmp_path):
    _agent(tmp_path)
    driver = FakeDriver(_healthy())

    result = _service(tmp_path, driver).verify("known")

    assert "/apps/known/" in driver.opened
    assert "scope=agent:known" in driver.opened
    assert result.passed


def test_the_daemon_token_never_comes_back_out(tmp_path):
    """It is resolved inside the service precisely so it does not travel through the model's
    context — echoing it in the result would undo that in one line."""
    _agent(tmp_path)
    driver = FakeDriver(_healthy())

    result = _service(tmp_path, driver).verify("known")

    assert "tok" not in result.url


def test_the_browser_is_closed_even_when_the_page_explodes(tmp_path):
    """A leaked Chromium per failed verification is how a long session runs a machine out of
    memory, and the failure it leaks on is the common case."""
    _agent(tmp_path)

    class Exploding(FakeDriver):
        def open(self, url):
            raise RuntimeError("navigation failed")

    driver = Exploding(_healthy())
    with pytest.raises(RuntimeError):
        _service(tmp_path, driver).verify("known")

    assert driver.closed


def test_steps_are_driven_and_re_checked(tmp_path):
    """MOST WINDOWS ARE FINE UNTIL YOU TOUCH THEM: the handler that throws only throws on click.
    A verification that never interacts reports a healthy page with a dead button."""
    _agent(tmp_path)
    broken_after = _healthy(page_errors=["TypeError: onRefresh is not a function"])
    driver = FakeDriver(_healthy(), after=broken_after)

    result = _service(tmp_path, driver).verify("known", [Step(action="click", target="Refresh")])

    assert not result.passed
    assert driver.steps[0].target == "Refresh"
    assert any("after your steps" in f.message for f in result.findings)


def test_an_agent_with_no_window_says_so_rather_than_failing(tmp_path):
    """A chat-only agent is a legitimate shape, not a defect — reporting it as one teaches the
    model to bolt a window onto agents that should not have one."""
    d = tmp_path / "known"
    d.mkdir()
    (d / "agent.toml").write_text('name = "x"\n', encoding="utf-8")

    with pytest.raises(VerifyError) as e:
        _service(tmp_path, FakeDriver(_healthy())).verify("known")
    assert "agentd ask" in str(e.value)


def test_a_missing_entry_file_is_caught_before_the_browser_starts(tmp_path):
    """Opening it would 404 and report a blank page — true, and about the wrong thing."""
    d = tmp_path / "known"
    d.mkdir()
    (d / "agent.toml").write_text('[app]\nentry = "ui/index.html"\n', encoding="utf-8")

    with pytest.raises(VerifyError) as e:
        _service(tmp_path, FakeDriver(_healthy())).verify("known")
    assert "does not exist" in str(e.value)


def test_no_daemon_is_a_setup_error_not_a_verdict(tmp_path):
    _agent(tmp_path)

    with pytest.raises(VerifyError) as e:
        _service(tmp_path, FakeDriver(_healthy()), gateway=None).verify("known")
    assert "no daemon" in str(e.value)


def test_a_stale_build_fails_before_anything_is_believed(tmp_path):
    """THE MOST WASTEFUL RESULT AVAILABLE: everything passes, against the previous screen. The
    author reads a green verdict about code that is not running."""
    d = _agent(tmp_path)
    (d / "app").mkdir()
    (d / "app" / "package.json").write_text("{}", encoding="utf-8")
    (d / "app" / "src").mkdir()
    source = d / "app" / "src" / "App.tsx"
    source.write_text("export default () => null", encoding="utf-8")
    # Newer than the built output by a clear margin.
    built = (d / "ui" / "index.html").stat().st_mtime
    import os

    os.utime(source, (built + 60, built + 60))

    result = _service(tmp_path, FakeDriver(_healthy())).verify("known")

    assert not result.passed
    assert any(f.code == "UI_BUILD_STALE" for f in result.findings)


# --------------------------------------------------------------------------- the tool


@pytest.mark.asyncio
async def test_the_tool_does_not_drive_the_browser_on_the_event_loop(tmp_path):
    """THE BUG THIS EXISTS FOR, and the reason a standalone check could not find it.

    Playwright's sync API refuses to run inside a live asyncio loop, and a tool's `execute` is
    always inside one. Driving it directly failed in the daemon with "It looks like you are using
    Playwright Sync API inside the asyncio loop" while passing perfectly in a script — the only
    difference being the loop, which the script did not have and production always does.

    So the tool must hand the blocking work to a thread. This test asserts the driver never sees
    a running loop, which is the actual requirement; asserting "it used to_thread" would pass for
    an implementation that still blocked.
    """
    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    seen: dict = {}

    class LoopSpy(FakeDriver):
        def open(self, url):
            try:
                seen["loop"] = asyncio.get_running_loop()
            except RuntimeError:
                seen["loop"] = None  # no running loop here — which is the point
            return super().open(url)

    tool = VerifyAppTool(_service(tmp_path, LoopSpy(_healthy())))
    result = await tool.execute("call-1", {"agent_id": "known"}, asyncio.Event())

    assert seen["loop"] is None, "the browser was driven on the event loop — Playwright refuses"
    assert not result.is_error


@pytest.mark.asyncio
async def test_a_blocked_run_is_not_an_error_result(tmp_path):
    """`is_error` is what makes the model treat a result as something to fix. A login screen is
    not something to fix in the agent."""
    from agent_authoring.presentation.verify_app_tool import VerifyAppTool

    _agent(tmp_path)
    tool = VerifyAppTool(_service(tmp_path, FakeDriver(_gated())))

    result = await tool.execute("call-1", {"agent_id": "known"}, asyncio.Event())

    assert not result.is_error
    assert "NOT VERIFIED" in "".join(getattr(b, "text", "") for b in result.content)


# --------------------------------------------------------------------------- the gate


def _gated() -> PageObservation:
    """What a headless browser ACTUALLY meets: a fresh profile has no stored session, so the
    SDK's gate renders and the app never mounts. No content, no socket — both descriptions of
    the gate rather than defects behind it."""
    return PageObservation(
        url="http://127.0.0.1:8787/apps/x/",
        sign_in_gate=True,
        text="Sign in to Comfy Smith",
        socket_open=False,
        scroll_width=1280,
        viewport_width=1280,
    )


def test_a_gated_page_produces_no_errors():
    """THE FALSE ACCUSATION THIS PREVENTS, observed on the first real run: `FAILED — the page
    never opened a socket`, about an app that was never given the chance to load. It sends the
    author to fix code that has not been executed."""
    findings = check_page(_gated())

    assert not any(f.is_error for f in findings)
    assert [f.code for f in findings] == ["APP_SIGN_IN_REQUIRED"]


def test_the_gate_finding_says_it_is_not_a_defect():
    """The model reads this line and decides whether to go and change something."""
    finding = check_page(_gated())[0]
    assert "NOT a defect" in finding.fix


def test_a_gated_result_is_neither_passed_nor_failed(tmp_path):
    """A THIRD OUTCOME, because both others are lies: the agent is not broken, and it is not
    verified. Reporting either one is how a login screen becomes 'ready to ship'."""
    _agent(tmp_path)

    result = _service(tmp_path, FakeDriver(_gated())).verify("known")

    assert result.blocked
    assert not result.passed


def test_verification_asks_the_gate_to_stand_aside(tmp_path):
    """The daemon decides whether sign-in is REQUIRED; where it is not, the flag removes a prompt
    rather than a check. Without it every verification of every agent photographs a login form."""
    _agent(tmp_path)
    driver = FakeDriver(_healthy())

    _service(tmp_path, driver).verify("known")

    assert "verify=1" in driver.opened


def test_credentials_are_used_only_when_the_gate_is_actually_up(tmp_path):
    """Signing in unasked would spend a real login on every run and hide the fact that the gate
    was never in the way."""
    _agent(tmp_path)
    driver = FakeDriver(_healthy())

    _service(tmp_path, driver).verify("known", email="a@b.c", password="pw")

    assert driver.credentials is None


def test_signing_in_gets_past_the_gate_and_verifies_the_real_window(tmp_path):
    _agent(tmp_path)
    driver = FakeDriver(_gated(), after_sign_in=_healthy())

    result = _service(tmp_path, driver).verify("known", email="a@b.c", password="pw")

    assert driver.credentials == ("a@b.c", "pw")
    assert result.signed_in
    assert result.passed and not result.blocked


def test_a_wrong_password_leaves_it_blocked_rather_than_failed(tmp_path):
    """Still not the agent's fault. A bad credential must not be reported as broken code."""
    _agent(tmp_path)
    driver = FakeDriver(_gated(), after_sign_in=_gated())

    result = _service(tmp_path, driver).verify("known", email="a@b.c", password="wrong")

    assert result.blocked
    assert not result.signed_in
    assert not any(f.is_error for f in result.findings)


def test_a_freshly_built_app_is_not_reported_as_stale(tmp_path):
    d = _agent(tmp_path)
    (d / "app").mkdir()
    (d / "app" / "package.json").write_text("{}", encoding="utf-8")
    (d / "app" / "src").mkdir()
    source = d / "app" / "src" / "App.tsx"
    source.write_text("export default () => null", encoding="utf-8")
    import os

    built = (d / "ui" / "index.html")
    os.utime(built, (source.stat().st_mtime + 60, source.stat().st_mtime + 60))

    assert _service(tmp_path, FakeDriver(_healthy())).verify("known").passed
