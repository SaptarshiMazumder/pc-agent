"""An agent app is served THIS engine's SDK, not the snapshot frozen into it at birth.

WHY THIS MATTERS IN PRODUCTION AND NOWHERE ELSE. `ui/vendor/agentd-client.js` is copied into an
agent when it is scaffolded. Agents that arrive as a PACKAGE get a fresh copy substituted at pack
time (test_pack_revendors_sdk.py); agents in the repo are refreshed by `npm run build`
(test_vendored_sdk_is_current.py). Neither reaches the case that actually bit users: an agent a
CUSTOMER created, sitting in their own account directory on the server, never packed and never
rebuilt. Those copies age against a daemon that keeps moving, and the aging is silent until an
interface changes underneath them — as it did when the accounts service stopped returning the
pre-token `login.token` field and every such app's sign-in began failing against a server that was
answering 200.

Substituting at the serving door fixes every one of them on deploy, with no migration.
"""

from __future__ import annotations

from pathlib import Path

from agent_runtime.presentation import gateway
from agent_runtime.presentation.gateway import VENDORED_SDK_REL, _app_asset_bytes

ON_DISK = b"// the SDK as it was the day this agent was scaffolded\n"
CURRENT = b"// the SDK this engine ships\n"


def _app(tmp_path: Path) -> Path:
    ui = tmp_path / "agents" / "customers-agent" / "ui"
    (ui / "vendor").mkdir(parents=True)
    (ui / VENDORED_SDK_REL).write_bytes(ON_DISK)
    (ui / "index.html").write_text("<!doctype html>", encoding="utf-8")
    (ui / "app.js").write_bytes(b"// the agent's own code, which is NOT ours to replace\n")
    return ui


def test_a_stale_vendored_sdk_is_replaced_at_the_door(tmp_path, monkeypatch):
    ui = _app(tmp_path)
    canonical = tmp_path / "dist" / "agentd-client.js"
    canonical.parent.mkdir()
    canonical.write_bytes(CURRENT)
    monkeypatch.setattr(gateway, "sdk_client_asset", lambda: canonical)

    assert _app_asset_bytes(ui / VENDORED_SDK_REL, ui) == CURRENT


def test_the_agents_own_files_are_served_untouched(tmp_path, monkeypatch):
    """Only the ONE vendored path is substituted. Everything else is the author's code."""
    ui = _app(tmp_path)
    canonical = tmp_path / "dist" / "agentd-client.js"
    canonical.parent.mkdir()
    canonical.write_bytes(CURRENT)
    monkeypatch.setattr(gateway, "sdk_client_asset", lambda: canonical)

    assert _app_asset_bytes(ui / "app.js", ui) == (ui / "app.js").read_bytes()
    assert _app_asset_bytes(ui / "index.html", ui) == (ui / "index.html").read_bytes()


def test_a_same_named_file_elsewhere_in_the_app_is_not_substituted(tmp_path, monkeypatch):
    """`vendor/agentd-client.js` exactly — not any file that happens to share the name.

    An app is free to ship `lib/agentd-client.js` of its own, and handing it our bundle instead
    would be us silently replacing somebody's source file.
    """
    ui = _app(tmp_path)
    decoy = ui / "lib" / "agentd-client.js"
    decoy.parent.mkdir()
    decoy.write_bytes(b"// the app author's own module, same name\n")
    canonical = tmp_path / "dist" / "agentd-client.js"
    canonical.parent.mkdir()
    canonical.write_bytes(CURRENT)
    monkeypatch.setattr(gateway, "sdk_client_asset", lambda: canonical)

    assert _app_asset_bytes(decoy, ui) == decoy.read_bytes()


def test_an_install_with_no_canonical_sdk_serves_what_is_on_disk(tmp_path, monkeypatch):
    """FAIL OPEN. A missing build asset must never become a 404 on the one script the page
    cannot start without — the app would be strictly worse off than with a stale SDK."""
    ui = _app(tmp_path)
    monkeypatch.setattr(gateway, "sdk_client_asset", lambda: None)

    assert _app_asset_bytes(ui / VENDORED_SDK_REL, ui) == ON_DISK


def test_an_unreadable_canonical_sdk_serves_what_is_on_disk(tmp_path, monkeypatch):
    ui = _app(tmp_path)
    monkeypatch.setattr(gateway, "sdk_client_asset", lambda: tmp_path / "gone" / "nothing.js")

    assert _app_asset_bytes(ui / VENDORED_SDK_REL, ui) == ON_DISK
