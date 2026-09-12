"""Figure Creator-only layout checks over the shipped UI. All HTTP/RPC traffic is mocked."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.integration.test_figure_creator_app import chromium, figure_app

pytestmark = pytest.mark.browser

SVG = '<svg xmlns="http://www.w3.org/2000/svg" width="240" height="160"><rect x="10" y="10" width="80" height="50" fill="red"/><text x="20" y="100">Test label</text></svg>'


@pytest.fixture
def sidebar_app(figure_app):
    app = figure_app
    state = SimpleNamespace(chat_count=1, file_count=3, empty=False)
    names = ["mitosis_clean_shaded.pdf", "mitosis_clean_shaded.png", "mitosis_clean_shaded.svg"]

    def entries(parent):
        if state.empty:
            return []
        if parent:
            return [{
                "name": "a_long_nested_figure_name_with_readable_extension.svg", "kind": "image",
                "size": 11068, "modified": 1789045378,
                "rel": parent + "/nested.svg", "path": "/workspace/" + parent + "/nested.svg",
            }]
        return [
            {"name": "Reference figures", "kind": "folder", "size": 0, "modified": 1789045378,
             "rel": "reference", "path": "/workspace/reference"},
            *[{
                "name": names[i] if i < 3 else f"extra_figure_{i:02d}.svg",
                "kind": "file" if i == 0 else "image", "size": 2000000 if i < 2 else 11068,
                "modified": 1789045378,
                "rel": names[i] if i < 3 else f"extra_figure_{i:02d}.svg",
                "path": "/workspace/" + (names[i] if i < 3 else f"extra_figure_{i:02d}.svg"),
            } for i in range(state.file_count)],
        ]

    def websocket(socket):
        def receive(raw):
            frame = json.loads(raw)
            method = frame["method"]
            app.methods.append(method)
            replies = {
                "hello": {"protocol": 1},
                "agents.list": {"agents": [], "default": "figure-creator"},
                "sessions.list": {"sessions": [
                    {"sessionId": f"chat-{i}", "title": "The stages of mitosis, clean shaded style" if i == 0 else f"Figure conversation {i:02d}",
                     "agentId": "figure-creator", "messages": 4, "modified": 1789045378}
                    for i in range(0 if state.empty else state.chat_count)
                ]},
                "sessions.history": {"messages": []},
                "plugins.catalog": {"plugins": []},
                "workspace.list": {"entries": entries(frame.get("params", {}).get("path", ""))},
                "auth.update": {},
                "config.get": {"values": {}, "catalogs": {}},
            }
            assert method in replies, f"Unexpected RPC: {method}"
            socket.send(json.dumps({"type": "res", "id": frame["id"], "ok": True, "payload": replies[method]}))
        socket.on_message(receive)

    app.page.context.route_web_socket("**/*", websocket)
    app.page.context.route("**/file?*", lambda route: route.fulfill(content_type="image/svg+xml", body=SVG))
    return SimpleNamespace(app=app, state=state)


def assert_within(child, parent):
    box = child.bounding_box()
    outer = parent.bounding_box()
    assert box is not None and outer is not None
    assert box["x"] >= outer["x"] - 1
    assert box["x"] + box["width"] <= outer["x"] + outer["width"] + 1
    assert box["y"] >= outer["y"] - 1
    assert box["y"] + box["height"] <= outer["y"] + outer["height"] + 1


@pytest.mark.parametrize("width,height,theme", [(1915, 1008, "light"), (1280, 860, "dark"), (760, 600, "light")])
def test_readable_file_rows_compact_controls_and_account(sidebar_app, width, height, theme):
    app = sidebar_app.app
    app.page.set_viewport_size({"width": width, "height": height})
    app.page.emulate_media(reduced_motion="reduce")
    app.open()
    app.shell_visible()
    if theme == "dark":
        app.page.get_by_title("Switch to dark", exact=True).click()
    sidebar = app.page.locator(".sidebar:not(.sidebar--rail)")
    files = sidebar.locator(".app-files")
    app.expect(files.locator(".ws-row")).to_have_count(4)
    for row in files.locator(".ws-row").all():
        name, meta = row.locator(".ws-name"), row.locator(".ws-meta")
        assert name.evaluate("el => el.scrollWidth <= el.clientWidth")
        name_box, meta_box = name.bounding_box(), meta.bounding_box()
        assert meta_box["y"] >= name_box["y"] + name_box["height"] - 1
        assert_within(name, sidebar)

    toolbar = files.locator(".ws-toolbar")
    for button in toolbar.get_by_role("button").all():
        assert_within(button, toolbar)
        assert button.bounding_box()["height"] <= 36
    assert files.bounding_box()["y"] < height * 0.4, "Files should not be stranded beneath empty chat space"
    footer = sidebar.locator(".fc-account-container")
    assert_within(footer, sidebar)
    assert footer.bounding_box()["height"] <= 145
    app.expect(footer.get_by_text(app.state.email, exact=True)).to_be_visible()
    app.expect(footer.get_by_role("button", name="Credits & billing", exact=True)).to_be_visible()
    for button in footer.locator(".app-account-actions button").all():
        assert_within(button, footer)
    if os.environ.get("FIGURE_CREATOR_SCREENSHOTS") == "1":
        output = Path(__file__).resolve().parents[1] / ".test-artifacts"
        output.mkdir(exist_ok=True)
        sidebar.screenshot(path=str(output / f"sidebar-{theme}-{width}.png"), animations="disabled")


def test_long_lists_scroll_independently_and_keep_file_controls_visible(sidebar_app):
    app = sidebar_app.app
    sidebar_app.state.chat_count = 35
    sidebar_app.state.file_count = 40
    app.page.set_viewport_size({"width": 1000, "height": 650})
    app.page.emulate_media(reduced_motion="reduce")
    app.open()
    app.shell_visible()
    app.expect(app.page.locator(".session-row")).to_have_count(35)
    app.expect(app.page.locator(".ws-row")).to_have_count(41)
    chats, files = app.page.locator(".sidebar-scroll"), app.page.locator(".app-files")
    for pane in (chats, files):
        assert pane.evaluate("el => el.scrollHeight > el.clientHeight")
    files.evaluate("el => { el.scrollTop = 400 }")
    assert chats.evaluate("el => el.scrollTop") == 0
    app.expect(files.get_by_role("button", name="Upload", exact=True)).to_be_in_viewport()
    assert_within(files.locator(".ws-toolbar"), files)
    chats.evaluate("el => { el.scrollTop = 180 }")
    assert files.evaluate("el => el.scrollTop") == 400
    assert_within(app.page.locator(".fc-account-container"), app.page.locator(".sidebar"))


def test_nested_files_open_in_canvas_and_editor_still_mounts(sidebar_app):
    app = sidebar_app.app
    app.page.emulate_media(reduced_motion="reduce")
    app.open()
    composer = app.shell_visible()
    composer.fill("Keep this figure draft")
    app.page.get_by_text("Reference figures", exact=True).click()
    nested = app.page.get_by_text("a_long_nested_figure_name_with_readable_extension.svg", exact=True)
    app.expect(nested).to_be_visible()
    assert nested.evaluate("el => el.scrollWidth <= el.clientWidth")
    nested.click()
    app.expect(app.page.locator(".cv-img")).to_be_visible()
    app.page.get_by_role("button", name="Edit", exact=True).click()
    app.expect(app.page.locator(".lower-canvas")).to_be_visible()
    app.expect(composer).to_have_value("Keep this figure draft")


def test_empty_sidebar_controls_and_collapsed_rail_remain_usable(sidebar_app):
    app = sidebar_app.app
    sidebar_app.state.empty = True
    app.page.emulate_media(reduced_motion="reduce")
    app.open()
    app.shell_visible()
    app.expect(app.page.get_by_text("no conversations yet", exact=True)).to_be_visible()
    app.expect(app.page.locator(".app-files")).to_contain_text("No files yet")
    button = app.page.get_by_role("button", name="New folder", exact=True)
    button.focus()
    app.page.keyboard.press("Enter")
    app.expect(app.page.locator(".ws-newfolder")).to_be_focused()
    app.page.keyboard.press("Escape")
    app.page.get_by_title("collapse sidebar", exact=True).click()
    rail = app.page.locator(".sidebar--rail")
    app.expect(rail).to_be_visible()
    assert rail.bounding_box()["width"] == 64
    app.page.get_by_title("expand sidebar", exact=True).click()
    app.expect(app.page.get_by_role("button", name="Credits & billing", exact=True).first).to_be_visible()
