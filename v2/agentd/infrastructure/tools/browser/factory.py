"""build_browser_provider — select the browser backend ONCE at startup.

CDP-attach (drive the user's live Chrome) when AGENTD_BROWSER_CDP_URL is set;
otherwise a launched Playwright browser (own persistent/ephemeral profile).
Returns None if Playwright isn't installed, so the browser tool (and the fetch
browser-render escalation) are simply omitted."""

from __future__ import annotations

import logging

log = logging.getLogger("agentd")


def build_browser_provider(config):
    try:
        if getattr(config, "browser_cdp_url", None):
            from agentd.infrastructure.tools.browser.providers.cdp import CdpBrowserProvider

            log.info("browser: attaching to running Chrome via CDP at %s", config.browser_cdp_url)
            return CdpBrowserProvider(config)

        from agentd.infrastructure.tools.browser.providers.playwright import (
            PlaywrightBrowserProvider,
        )

        return PlaywrightBrowserProvider(config)
    except ImportError:
        log.warning("playwright not installed; browser tool disabled")
        return None
