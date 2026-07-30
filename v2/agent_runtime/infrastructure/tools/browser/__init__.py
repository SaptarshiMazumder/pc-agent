"""browser INFRASTRUCTURE: the BrowserProvider adapters (the shared browser session) + factory.

This is the framework dependency the container builds and INJECTS; the browser TOOL itself
(the dispatcher the model calls) migrated out to the built-in 'browser' plugin (plugins/browser/),
which receives this provider via ``ctx.browser``. The ``snapshot`` helper stays here (browser
infra shared with the tool)."""

from agent_runtime.infrastructure.tools.browser.factory import build_browser_provider
from agent_runtime.infrastructure.tools.browser.providers import PlaywrightBrowserProvider

# Backward-compat alias (the provider was formerly named BrowserManager).
BrowserManager = PlaywrightBrowserProvider

__all__ = [
    "PlaywrightBrowserProvider",
    "BrowserManager",
    "build_browser_provider",
]
