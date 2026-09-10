"""Launch the shipped Figure Creator in Chromium, with no daemon or paid services.

The gateway substitutes its current SDK for an agent's vendored copy. Exercising
that exact combination catches removed SDK calls that a successful build misses.
Only HTTP and WebSocket responses are faked; the app, SDK, React shell and sign-in
component all execute in a real browser.
"""

from __future__ import annotations

import json
import mimetypes
import re
import time
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse

import pytest

from agent_runtime.runtime_paths import sdk_client_asset

pytestmark = pytest.mark.browser

APP_ROOT = Path(__file__).resolve().parents[2] / "agents" / "figure-creator" / "ui"
ORIGIN = "http://figure.test"
APP_PATH = "/apps/figure-creator/"


@pytest.fixture(scope="module")
def chromium():
    playwright = pytest.importorskip("playwright.sync_api")
    with playwright.sync_playwright() as driver:
        try:
            browser = driver.chromium.launch(headless=True)
        except playwright.Error as exc:
            pytest.skip(f"Chromium is unavailable: {exc}")
        try:
            yield browser
        finally:
            browser.close()


@pytest.fixture
def figure_app(chromium):
    from playwright.sync_api import expect

    sdk = sdk_client_asset()
    assert sdk is not None, "Build the canonical SDK before checking shipped agent apps"
    context = chromium.new_context(viewport={"width": 1280, "height": 860})
    page = context.new_page()
    page.set_default_timeout(5000)
    state = SimpleNamespace(
        hosted=False,
        identity="ok",
        platform_online=True,
        accounts_available=True,
        email="figure@example.test",
        account_id="acct_figure",
    )
    errors = []
    unexpected = []
    methods = []
    requests = []
    sockets = []
    served = set()
    page.on("pageerror", lambda error: errors.append(str(error)))

    def token():
        if state.identity != "ok":
            return {"state": state.identity, "retryAfterSec": 15}
        return {
            "state": "ok",
            "accessToken": f"test-token-{state.account_id}",
            "expiresAt": time.time() + 3600,
            "email": state.email,
            "accountId": state.account_id,
        }

    def handle_http(route):
        request = route.request
        parsed = urlparse(request.url)
        path = parsed.path
        requests.append((request.method, path))

        def respond(payload, status=200):
            route.fulfill(status=status, json=payload)

        if parsed.netloc != "figure.test":
            unexpected.append(request.url)
            route.abort()
        elif path.startswith(APP_PATH):
            relative = path.removeprefix(APP_PATH) or "index.html"
            asset = (APP_ROOT / relative).resolve()
            if relative == "vendor/agentd-client.js":
                asset = sdk
            elif not asset.is_relative_to(APP_ROOT.resolve()):
                unexpected.append(request.url)
                route.abort()
                return
            if not asset.is_file():
                unexpected.append(request.url)
                route.fulfill(status=404, body="Missing app asset")
                return
            served.add(relative)
            route.fulfill(
                body=asset.read_bytes(),
                content_type=mimetypes.guess_type(asset.name)[0] or "application/octet-stream",
            )
        elif path == "/platform/status":
            if not state.platform_online:
                route.abort("connectionrefused")
                return
            respond({
                "accountsUrl": f"{ORIGIN}/accounts" if state.accounts_available else "",
                "canUseCloud": True,
                "mode": "cloud",
                "runModeLocked": state.hosted,
                "signInRequired": True,
            })
        elif path == "/auth/token":
            status = 200 if state.identity == "ok" else 401
            if state.identity == "accounts_unreachable":
                status = 503
            respond({} if state.hosted else token(), 404 if state.hosted else status)
        elif path == "/accounts/auth/refresh":
            if state.identity == "ok":
                respond({
                    "access_token": token()["accessToken"],
                    "expires_in": 3600,
                    "email": state.email,
                    "account_id": state.account_id,
                })
            else:
                respond({}, 503 if state.identity == "accounts_unreachable" else 401)
        elif path in ("/auth/login", "/accounts/auth/login"):
            if path == "/auth/login" and state.hosted:
                respond({}, 404)
                return
            if path.startswith("/accounts/"):
                payload = request.post_data_json
                assert payload["cookie"] is True
                state.email = payload["email"]
            else:
                state.email = request.headers["x-auth-email"]
            state.identity = "ok"
            respond({"state": "ok"})
        elif path in ("/auth/logout", "/accounts/auth/logout"):
            if path == "/auth/logout" and state.hosted:
                respond({}, 404)
                return
            state.identity = "signed_out"
            respond({"state": "signed_out"})
        elif path == "/accounts/me/credits":
            respond({"credits_remaining": 1200})
        elif path == "/favicon.ico":
            route.fulfill(status=204)
        else:
            unexpected.append(f"{request.method} {request.url}")
            route.abort()

    def handle_websocket(socket):
        sockets.append(socket)

        def receive(raw):
            frame = json.loads(raw)
            method = frame.get("method")
            methods.append(method)
            replies = {
                "hello": {"protocol": 1},
                "agents.list": {"agents": [], "default": "figure-creator"},
                "sessions.list": {"sessions": []},
                "sessions.history": {"messages": []},
                "plugins.catalog": {"plugins": []},
                "workspace.list": {"entries": []},
                "auth.update": {},
            }
            if method not in replies:
                unexpected.append(f"RPC {method}")
            socket.send(json.dumps({
                "type": "res",
                "id": frame["id"],
                "ok": method in replies,
                "payload": replies.get(method, {"error": "Unexpected test RPC"}),
            }))

        socket.on_message(receive)

    context.route("**/*", handle_http)
    context.route_web_socket("**/*", handle_websocket)

    def open_app():
        page.goto(f"{ORIGIN}{APP_PATH}?scope=agent:figure-creator&token=test-machine-token")

    def shell_visible():
        composer = page.locator("textarea")
        expect(composer).to_be_visible()
        expect(composer).to_be_enabled()
        expect(page.get_by_text(state.email, exact=True)).to_be_visible()
        return composer

    def identity_changed():
        assert sockets, "The current SDK must have opened an intercepted WebSocket"
        sockets[-1].send(json.dumps({"type": "event", "event": "auth.changed", "payload": {}}))

    try:
        yield SimpleNamespace(
            page=page,
            state=state,
            open=open_app,
            shell_visible=shell_visible,
            identity_changed=identity_changed,
            methods=methods,
            requests=requests,
            served=served,
            expect=expect,
        )
    finally:
        context.close()
        assert not errors, f"Uncaught app errors: {errors}"
        assert not unexpected, f"Unexpected network requests or RPCs: {unexpected}"
        assert "chat.send" not in methods, "Launching/signing in must not start a paid generation"


@pytest.mark.parametrize("hosted", [False, True], ids=["runtime", "hosted-cookie"])
def test_signed_in_launch_renders_the_shipped_shell(figure_app, hosted):
    app = figure_app
    app.state.hosted = hosted
    app.open()
    app.shell_visible()
    assert {"app.js", "vendor/agentd-client.js", "vendor/agentd-canvas.js"} <= app.served
    if hosted:
        assert ("POST", "/accounts/auth/refresh") in app.requests


@pytest.mark.parametrize("hosted", [False, True], ids=["runtime", "hosted-cookie"])
def test_signed_out_launch_can_sign_in_and_sign_out(figure_app, hosted):
    app = figure_app
    app.state.hosted = hosted
    app.state.identity = "signed_out"
    app.open()
    app.expect(app.page.locator("#signin-email")).to_be_visible()
    app.expect(app.page.locator("textarea")).to_have_count(0)
    app.page.get_by_label("Email", exact=True).fill("new@example.test")
    app.page.get_by_label("Password", exact=True).fill("test-password")
    app.page.get_by_role("button", name="Sign in", exact=True).click()
    app.shell_visible()
    app.page.get_by_title("Sign out", exact=True).click()
    app.expect(app.page.locator("#signin-email")).to_be_visible()
    app.expect(app.page.locator("textarea")).to_have_count(0)
    app.expect(app.page.get_by_text("new@example.test", exact=True)).to_have_count(0)


def test_account_change_replaces_the_previous_workspace_and_expiry_regates(figure_app):
    app = figure_app
    app.open()
    composer = app.shell_visible()
    composer.fill("This draft belongs to the previous account")
    app.state.email = "other@example.test"
    app.state.account_id = "acct_other"
    app.identity_changed()
    app.shell_visible()
    app.expect(composer).to_have_value("")
    app.expect(app.page.get_by_text("figure@example.test", exact=True)).to_have_count(0)

    app.state.identity = "session_expired"
    app.identity_changed()
    app.expect(app.page.locator("#signin-email")).to_be_visible()
    app.expect(app.page.locator("textarea")).to_have_count(0)


def test_returning_to_the_tab_picks_up_a_sign_in_from_another_window(figure_app):
    app = figure_app
    app.state.identity = "signed_out"
    app.open()
    app.expect(app.page.locator("#signin-email")).to_be_visible()

    # This tab closed its socket when it showed sign-in. Another window's login
    # therefore cannot arrive as auth.changed; returning to the tab must re-read.
    app.state.identity = "ok"
    app.state.email = "other-window@example.test"
    app.state.account_id = "acct_other_window"
    app.page.evaluate("window.dispatchEvent(new Event('focus'))")
    app.shell_visible()
    app.expect(app.page.locator("#signin-email")).to_have_count(0)


@pytest.mark.parametrize("outage", ["platform", "identity"])
def test_unavailable_service_has_a_visible_retry_that_recovers(figure_app, outage):
    app = figure_app
    if outage == "platform":
        app.state.platform_online = False
    else:
        app.state.identity = "accounts_unreachable"
    app.open()
    app.expect(app.page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    app.expect(app.page.locator("#root")).to_contain_text(re.compile("connect|unavailable", re.I))
    app.expect(app.page.locator("textarea")).to_have_count(0)
    app.state.platform_online = True
    app.state.identity = "ok"
    app.page.get_by_role("button", name="Retry", exact=True).click()
    app.shell_visible()


def test_transient_identity_outage_preserves_the_open_conversation(figure_app):
    app = figure_app
    app.open()
    composer = app.shell_visible()
    composer.fill("Keep my unfinished figure description")
    app.state.identity = "accounts_unreachable"
    app.identity_changed()
    app.expect(app.page.get_by_role("button", name="Retry", exact=True)).to_be_visible()
    app.expect(composer).to_have_value("Keep my unfinished figure description")
    app.state.identity = "ok"
    app.page.get_by_role("button", name="Retry", exact=True).click()
    app.shell_visible()
    app.expect(composer).to_have_value("Keep my unfinished figure description")


def test_missing_accounts_service_explains_why_the_app_cannot_start(figure_app):
    app = figure_app
    app.state.identity = "signed_out"
    app.state.accounts_available = False
    app.open()
    app.expect(app.page.locator("#root")).to_contain_text(re.compile("accounts service", re.I))
    app.expect(app.page.locator("textarea")).to_have_count(0)
    app.expect(app.page.locator("#signin-email")).to_have_count(0)
