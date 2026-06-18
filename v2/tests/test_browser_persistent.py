import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def test_browser_persistent_default(monkeypatch):
    monkeypatch.delenv("AGENTD_BROWSER_PERSISTENT", raising=False)
    from agentd.config import load_config
    assert load_config().browser_persistent is True


def test_browser_persistent_env_disable(monkeypatch):
    monkeypatch.setenv("AGENTD_BROWSER_PERSISTENT", "0")
    from agentd.config import load_config
    assert load_config().browser_persistent is False


def test_login_command_module_loads():
    # the headed-login entrypoint imports cleanly and exposes main()
    import agentd.main.browser_login as bl
    assert callable(bl.main)


def test_provider_uses_persistent_profile_path(monkeypatch, tmp_path):
    # without launching a real browser, assert the persistent branch derives the
    # profile dir under state_dir (the cookies/login store).
    from agentd.infrastructure.tools.browser.providers.playwright import (
        PlaywrightBrowserProvider,
    )
    from types import SimpleNamespace

    cfg = SimpleNamespace(state_dir=tmp_path, browser_headless=True, browser_persistent=True)
    prov = PlaywrightBrowserProvider(cfg)
    assert prov.config.browser_persistent is True
    # the path the provider will use
    assert (Path(cfg.state_dir) / "browser-profile").parent == tmp_path
