"""Built-in 'web' bundle — web_search + web_fetch and their provider subpackages (search/, fetch/).

Migrated out of agentd core. The search/fetch PROVIDER interfaces stay in agentd.application
(inner layer); their concrete providers + the tools live here. web_fetch's browser-render provider
gets the shared browser via DI (``ctx.browser``) — None when the browser subsystem is off, in which
case it falls back to plain httpx fetching.
"""

from __future__ import annotations

from fetch import build_fetch_providers
from search import build_search_providers
from web_fetch import WebFetchTool
from web_search import WebSearchTool


def register(api, ctx):
    cfg = ctx.config
    api.register_tool(WebSearchTool(cfg, build_search_providers(cfg)))
    api.register_tool(WebFetchTool(cfg, build_fetch_providers(cfg, ctx.browser)))
