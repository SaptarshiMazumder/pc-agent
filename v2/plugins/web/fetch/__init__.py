"""web_fetch adapters: fetch providers, extraction, factory."""

from fetch.extract import (
    DEFAULT_MAX_CHARS,
    MAX_CHARS_CAP,
    sanitize_url,
)
from fetch.factory import build_fetch_providers
from fetch.providers import BrowserRenderProvider, HttpxFetchProvider

__all__ = [
    "build_fetch_providers",
    "sanitize_url",
    "DEFAULT_MAX_CHARS",
    "MAX_CHARS_CAP",
    "HttpxFetchProvider",
    "BrowserRenderProvider",
]
