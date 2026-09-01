import sys
from pathlib import Path

from agent_runtime.application.tool_models import browser_knob
from agent_runtime.config import load_config

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_browser_persistent_default():
    assert browser_knob(load_config(), "persistent", default=True) is True


def test_browser_persistent_config_disable():
    cfg = load_config()
    cfg.plugins = {"browser": {"tools": {"browser": {"persistent": False}}}}
    assert browser_knob(cfg, "persistent", default=True) is False


def test_login_command_module_loads():
    # the headed-login entrypoint imports cleanly and exposes main()
    import agent_runtime.main.browser_login as bl

    assert callable(bl.main)


def test_provider_uses_persistent_profile_path(monkeypatch, tmp_path):
    # without launching a real browser, assert the persistent branch derives the
    # profile dir under state_dir (the cookies/login store).
    from types import SimpleNamespace

    from agent_runtime.infrastructure.tools.browser.providers.playwright import (
        PlaywrightBrowserProvider,
    )

    cfg = SimpleNamespace(state_dir=tmp_path, browser_headless=True, browser_persistent=True)
    prov = PlaywrightBrowserProvider(cfg)
    assert prov.config.browser_persistent is True
    # the path the provider will use
    assert (Path(cfg.state_dir) / "browser-profile").parent == tmp_path
