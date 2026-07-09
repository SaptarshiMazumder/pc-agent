"""ensure() self-heals a dead browser: a closed/crashed session (stale non-null context) is
torn down and relaunched on the next ensure(), instead of wedging every later new_page() with
TargetClosedError. Pure-fake (no real Chromium)."""

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentd.infrastructure.tools.browser.providers.base import BaseBrowserSession


class _FakePage:
    def on(self, _ev, _fn):
        pass


class _FakeContext:
    def __init__(self):
        self.pages = []
        self._handlers = {}
        self.closed = False

    def set_default_timeout(self, _ms):
        pass

    def on(self, ev, fn):
        self._handlers.setdefault(ev, []).append(fn)

    def once(self, ev, fn):
        self._handlers.setdefault(ev, []).append(fn)

    def fire_close(self):
        for fn in self._handlers.get("close", []):
            fn()

    async def new_page(self):
        p = _FakePage()
        self.pages.append(p)
        for fn in self._handlers.get("page", []):
            fn(p)
        return p

    async def close(self):
        self.closed = True


class _FakeBrowser:
    def __init__(self):
        self.connected = True

    def is_connected(self):
        return self.connected

    async def close(self):
        self.connected = False


class _FakeProvider(BaseBrowserSession):
    def __init__(self, config):
        super().__init__(config)
        self.launches = 0

    async def _create_context(self):
        self.launches += 1
        self._browser = _FakeBrowser()
        return None, _FakeContext()


def _mgr():
    return _FakeProvider(SimpleNamespace(browser_action_timeout_ms=1000, browser_console_buffer=10))


def test_first_ensure_launches_once():
    mgr = _mgr()
    asyncio.run(mgr.ensure())
    assert mgr.launches == 1 and mgr.context is not None and mgr.active_page is not None


def test_alive_session_is_reused_not_relaunched():
    mgr = _mgr()
    asyncio.run(mgr.ensure())
    asyncio.run(mgr.ensure())  # still connected -> no relaunch
    assert mgr.launches == 1


def test_dead_browser_relaunches_on_next_ensure():
    mgr = _mgr()
    asyncio.run(mgr.ensure())
    first = mgr.context
    mgr._browser.connected = False  # browser crashed / window closed
    asyncio.run(mgr.ensure())  # stale handle -> tear down + relaunch
    assert mgr.launches == 2 and mgr.context is not first and mgr._connected()


def test_context_close_event_clears_handle_and_relaunches():
    mgr = _mgr()
    asyncio.run(mgr.ensure())
    mgr.context.fire_close()  # context closed under us (browser still "up")
    assert mgr.context is None  # handle dropped immediately
    asyncio.run(mgr.ensure())
    assert mgr.launches == 2 and mgr.context is not None
