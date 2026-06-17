"""browser tool: dispatcher (BrowserTool) + BrowserProvider adapters + factory."""

from agentd.infrastructure.tools.browser.factory import build_browser_provider
from agentd.infrastructure.tools.browser.providers import PlaywrightBrowserProvider
from agentd.infrastructure.tools.browser.tool import BrowserTool

# Backward-compat alias (the provider was formerly named BrowserManager).
BrowserManager = PlaywrightBrowserProvider

__all__ = [
    "BrowserTool",
    "PlaywrightBrowserProvider",
    "BrowserManager",
    "build_browser_provider",
]
