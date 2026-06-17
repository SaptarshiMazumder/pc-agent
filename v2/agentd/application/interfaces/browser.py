"""BrowserProvider — the contract for a driven browser session.

The `browser` tool drives a single provider (no fallback chain — a session can't
switch browsers mid-run). Today that's Playwright (local Chromium); a remote-CDP /
cloud-browser adapter can drop in behind this port later via the factory, without
touching the tool.

Browser automation is inherently tied to its driver, so the port exposes the live
session surface the tool needs (the active page + context) plus the high-level
helpers (ensure/snapshot/resolve_ref/settle/close). The `browser_render` fetch
provider also depends on `ensure()` + `context` to open an isolated page.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrowserProvider(Protocol):
    active_page: Any  # the live driver page the tool acts on
    context: Any      # the browser context (for opening isolated pages / tabs)
    ref_map: dict

    async def ensure(self) -> None: ...
    async def close(self) -> None: ...
    async def settle(self) -> None: ...
    async def snapshot(self, **kwargs) -> str: ...
    def resolve_ref(self, ref: str): ...
