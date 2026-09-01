"""simple_login: vault-backed login + OTP resume. The password is typed into the form but NEVER
returned to the model; no saved login -> graceful error that never asks for a password in chat."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from login_tool import SimpleLoginTool

from agent_runtime.application.run_context import RunContext, set_run_context
from agent_runtime.domain.credential import Credential


class FakePage:
    def __init__(self, present):
        self.url = "https://hp/login"
        self.fills, self.presses, self.clicks = [], [], []
        self.submits = 0
        self._present = present

    async def goto(self, url, **kw):
        self.url = url

    async def fill(self, sel, val):
        self.fills.append((sel, val))

    async def press(self, sel, key):
        self.presses.append((sel, key))
        self.submits += 1

    async def click(self, sel):
        self.clicks.append(sel)
        self.submits += 1

    async def query_selector(self, sel):
        return object() if self._present(sel, self) else None


class FakeMgr:
    def __init__(self, page):
        self._p = page

    async def ensure(self):
        pass

    def resolve_target(self, tid):
        return self._p

    async def settle(self):
        pass


class FakeStore:
    def __init__(self, cred):
        self._c = cred

    def get(self, agent_id, site):
        return self._c if (self._c and site == self._c.site) else None


def _run(tool, params):
    set_run_context(RunContext("main", "agent:main:dev", "interactive"))
    return asyncio.run(tool.execute("c", params, asyncio.Event()))


def _text(r):
    return r.content[0].text


def test_no_saved_login_is_graceful():
    tool = SimpleLoginTool(FakeStore(None), FakeMgr(FakePage(lambda s, p: False)))
    r = _run(tool, {"site": "hp"})
    assert r.is_error and "No saved login" in _text(r) and "password in chat" in _text(r)


def test_success_no_otp_and_password_never_leaks():
    cred = Credential(
        site="hp", login_url="https://hp/login", username="u@e.com", password="TOPSECRET"
    )

    def present(sel, page):  # password present before submit, gone after; no otp field
        return ("password" in sel) and page.submits == 0

    page = FakePage(present)
    tool = SimpleLoginTool(FakeStore(cred), FakeMgr(page))
    r = _run(tool, {"site": "hp"})
    assert not r.is_error and "Logged in" in _text(r)
    assert any(v == "TOPSECRET" for _, v in page.fills)  # the password WAS typed into the form
    assert "TOPSECRET" not in _text(r)  # but NEVER appears in the result


def test_otp_required_then_resume():
    cred = Credential(
        site="hp", login_url="https://hp/login", username="u", password="p", otp_selector="#otp"
    )

    def present(sel, page):
        if sel == "#otp":
            return page.submits == 1  # OTP page appears after the 1st submit only
        if "password" in sel:
            return page.submits == 0
        return False

    page = FakePage(present)
    tool = SimpleLoginTool(FakeStore(cred), FakeMgr(page))
    r1 = _run(tool, {"site": "hp"})
    assert "OTP_REQUIRED" in _text(r1)
    r2 = _run(tool, {"site": "hp", "otp": "402913"})  # resume with the code from the user
    assert not r2.is_error and "Logged in" in _text(r2)
    assert ("#otp", "402913") in page.fills
