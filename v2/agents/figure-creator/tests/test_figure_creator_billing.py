"""Real Chromium + shipped Figure Creator assets; every payment and network response is fake.

Reuse the existing launch fixture read-only. These product-specific tests stay inside the
agent so migrating its credits screen does not modify runtime tests or another agent.
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from tests.integration.test_figure_creator_app import chromium, figure_app

pytestmark = pytest.mark.browser


@pytest.fixture
def payments(figure_app):
    app = figure_app
    context = app.page.context
    state = SimpleNamespace(balance=1200, org_id="", mode="interactive", checkouts=[], reads=[])

    def accounts(route):
        request = route.request
        url = urlparse(request.url)
        if url.path == "/accounts/me/credits":
            state.reads.append(parse_qs(url.query))
            route.fulfill(json={
                "credits_remaining": state.balance,
                "funding_source": "org_pool" if state.org_id else "platform",
                "org_id": state.org_id,
            })
        elif url.path == "/accounts/products":
            route.fulfill(json={
                "products": [{"id": "pack-1000", "title": "Test credit pack", "credits": 1000, "price_usd": 10}],
                "provider": "test-only",
                "payment_note": "Test checkout. No real money moves.",
            })
        elif url.path == "/accounts/me/checkout":
            assert request.method == "POST"
            assert request.headers["authorization"] == f"Bearer test-token-{app.state.account_id}"
            body = request.post_data_json
            state.checkouts.append(body)
            assert body["product_id"] == "pack-1000"
            assert body["idempotency_key"]
            assert body["success_url"] == "http://figure.test/accounts/checkout/complete"
            assert body["cancel_url"] == body["success_url"]
            assert set(body) == {"product_id", "idempotency_key", "success_url", "cancel_url"}
            if state.mode == "failed":
                route.fulfill(status=402, json={"detail": "Payment provider rejected checkout"})
            elif state.mode in ("instant", "replayed"):
                state.balance = 2200
                route.fulfill(json={
                    "credits": 1000, "credits_remaining": state.balance,
                    "replayed": state.mode == "replayed", "payment": {"detail": "Test receipt"},
                })
            else:
                route.fulfill(json={"checkout_url": "https://checkout.test/pay/order-1"})
        else:
            # Legacy /me/purchase is deliberately NOT handled: the base fixture fails any
            # unrecognized request, so this regression cannot silently reintroduce it.
            route.fallback()

    context.route("http://figure.test/accounts/**", accounts)
    context.route("https://checkout.test/**", lambda route: route.fulfill(
        content_type="text/html", body="<h1>Intercepted test checkout</h1>",
    ))

    # Settings also reads config.get. Keep all WebSocket replies local and refuse mutations.
    def websocket(socket):
        def receive(raw):
            frame = json.loads(raw)
            method = frame["method"]
            app.methods.append(method)
            replies = {
                "hello": {"protocol": 1},
                "agents.list": {"agents": [], "default": "figure-creator"},
                "sessions.list": {"sessions": []},
                "sessions.history": {"messages": []},
                "plugins.catalog": {"plugins": []},
                "workspace.list": {"entries": []},
                "auth.update": {},
                "config.get": {"values": {}, "catalogs": {}},
            }
            assert method in replies, f"Unexpected RPC: {method}"
            socket.send(json.dumps({"type": "res", "id": frame["id"], "ok": True, "payload": replies[method]}))
        socket.on_message(receive)

    context.route_web_socket("**/*", websocket)
    state.app = app
    return state


def open_credits(app):
    app.page.get_by_role("button", name="Credits & billing", exact=True).first.click()
    dialog = app.page.get_by_role("dialog", name="Credits & billing")
    app.expect(dialog).to_be_visible()
    app.expect(dialog.locator(".plan-name")).to_have_text("1,200 credits")
    return dialog


@pytest.mark.parametrize("hosted", [False, True], ids=["runtime", "hosted-cookie"])
def test_interactive_checkout_refreshes_sidebar_and_preserves_draft(payments, hosted):
    app = payments.app
    app.state.hosted = hosted
    app.open()
    composer = app.shell_visible()
    composer.fill("Keep my unfinished figure description")
    dialog = open_credits(app)
    with app.page.expect_popup() as checkout:
        dialog.get_by_role("button", name=re.compile(r"^Buy")).click()
    popup = checkout.value
    app.expect(popup.get_by_role("heading")).to_have_text("Intercepted test checkout")
    app.expect(dialog).to_contain_text("Complete the payment")
    app.expect(dialog.locator(".plan-name")).to_have_text("1,200 credits")
    dialog.get_by_role("button", name="Back to Figure Creator").click()
    app.expect(composer).to_have_value("Keep my unfinished figure description")
    # This simulates only the server's eventual grant. The actual SDK awaitGrant polling
    # and credits event bus must notice it and update the original window themselves.
    payments.balance = 2200
    app.expect(app.page.locator(".app-account-credits")).to_have_text("2,200 credits", timeout=10000)
    app.page.get_by_role("button", name="Credits & billing", exact=True).first.click()
    app.expect(dialog.locator(".plan-name")).to_have_text("2,200 credits")
    assert len(payments.checkouts) == 1
    assert all(query.get("agent_id") == ["figure-creator"] for query in payments.reads)
    popup.close()


@pytest.mark.parametrize("mode", ["instant", "replayed"])
def test_instant_purchase_or_replay_updates_the_same_balance(payments, mode):
    payments.mode = mode
    app = payments.app
    app.open()
    app.shell_visible()
    dialog = open_credits(app)
    dialog.get_by_role("button", name=re.compile(r"^Buy")).click()
    app.expect(dialog).to_contain_text("Already bought" if mode == "replayed" else "Added 1,000 credits")
    app.expect(dialog.locator(".plan-name")).to_have_text("2,200 credits")
    app.expect(app.page.locator(".app-account-credits")).to_have_text("2,200 credits")
    assert len(payments.checkouts) == 1
    assert len(app.page.context.pages) == 1


def test_rejected_checkout_shows_error_and_allows_retry(payments):
    payments.mode = "failed"
    app = payments.app
    app.open()
    app.shell_visible()
    dialog = open_credits(app)
    buy = dialog.get_by_role("button", name=re.compile(r"^Buy"))
    buy.click()
    app.expect(dialog).to_contain_text("Payment provider rejected checkout")
    app.expect(buy).to_be_enabled()
    app.expect(dialog.locator(".plan-name")).to_have_text("1,200 credits")
    app.expect(app.page.locator(".app-account-credits")).to_have_text("1,200 credits")
    payments.mode = "instant"
    buy.click()
    app.expect(dialog).to_contain_text("Added 1,000 credits")
    assert len(payments.checkouts) == 2


def test_cancelled_checkout_never_reports_granted_credits(payments):
    app = payments.app
    app.page.clock.install()
    app.open()
    app.shell_visible()
    dialog = open_credits(app)
    with app.page.expect_popup() as checkout:
        dialog.get_by_role("button", name=re.compile(r"^Buy")).click()
    checkout.value.close()
    app.page.clock.fast_forward(5000)
    app.expect(dialog.locator(".plan-name")).to_have_text("1,200 credits")
    app.expect(dialog.get_by_text(re.compile("Added .* credits"))).to_have_count(0)
    app.page.keyboard.press("Escape")
    app.expect(dialog).not_to_be_visible()
    app.expect(app.page.get_by_role("button", name="Credits & billing", exact=True).first).to_be_focused()
    app.expect(app.page.locator(".app-account-credits")).to_have_text("1,200 credits")


def test_org_funded_account_uses_shared_store_restrictions(payments):
    payments.org_id = "org_example"
    app = payments.app
    app.open()
    app.shell_visible()
    dialog = open_credits(app)
    app.expect(dialog).to_contain_text("Your organization funds your usage")
    app.expect(dialog.get_by_role("button", name=re.compile(r"^Buy"))).to_have_count(0)
    assert not payments.checkouts


def test_settings_uses_shared_credits_from_collapsed_sidebar(payments):
    app = payments.app
    app.open()
    app.shell_visible()
    app.page.get_by_title("collapse sidebar", exact=True).click()
    app.page.get_by_title("Settings", exact=True).click()
    app.expect(app.page.get_by_text("Buy credits", exact=True)).to_have_count(0)
    dialog = open_credits(app)
    payments.mode = "instant"
    dialog.get_by_role("button", name=re.compile(r"^Buy")).click()
    app.expect(dialog.locator(".plan-name")).to_have_text("2,200 credits")
    app.page.keyboard.press("Escape")
    app.expect(app.page.locator(".settings")).to_contain_text("2,200")


@pytest.mark.parametrize("width,theme", [(1280, "light"), (1280, "dark"), (390, "light")])
def test_shared_credits_layout_and_keyboard_navigation(payments, width, theme):
    app = payments.app
    app.page.set_viewport_size({"width": width, "height": 860})
    app.open()
    app.shell_visible()
    if theme == "dark":
        app.page.get_by_title("Switch to dark", exact=True).click()
    # Activate the entry point with the keyboard, not only mouse events.
    entry = app.page.get_by_role("button", name="Credits & billing", exact=True).first
    entry.focus()
    app.page.keyboard.press("Enter")
    dialog = app.page.get_by_role("dialog", name="Credits & billing")
    app.expect(dialog.locator(".plan-name")).to_have_text("1,200 credits")
    assert dialog.evaluate("el => el.scrollWidth <= el.clientWidth")
    for _ in range(5):
        app.page.keyboard.press("Tab")
        # Native modal dialogs may hand focus to browser chrome at the end of the tab order;
        # they must never focus a control in the inert application behind the dialog.
        assert dialog.evaluate("el => !document.hasFocus() || el.contains(document.activeElement)")
    if os.environ.get("FIGURE_CREATOR_SCREENSHOTS") == "1":
        output = Path(__file__).resolve().parents[1] / ".test-artifacts"
        output.mkdir(exist_ok=True)
        app.page.screenshot(path=str(output / f"billing-{theme}-{width}.png"), animations="disabled")
    app.page.keyboard.press("Escape")
    app.expect(dialog).not_to_be_visible()
    app.expect(entry).to_be_focused()
