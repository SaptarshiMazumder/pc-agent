"""web_fetch adapters: fetch providers, extraction, factory."""

from agentd.infrastructure.tools.fetch.extract import (
    DEFAULT_MAX_CHARS,
    MAX_CHARS_CAP,
    sanitize_url,
)
from agentd.infrastructure.tools.fetch.factory import build_fetch_providers
from agentd.infrastructure.tools.fetch.providers import BrowserRenderProvider, HttpxFetchProvider

__all__ = [
    "build_fetch_providers",
    "sanitize_url",
    "DEFAULT_MAX_CHARS",
    "MAX_CHARS_CAP",
    "HttpxFetchProvider",
    "BrowserRenderProvider",
]
