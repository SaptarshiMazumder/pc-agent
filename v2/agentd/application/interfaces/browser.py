"""BrowserProvider — the contract for a driven browser session.

The `browser` tool drives a single provider. Today that's Playwright (local
Chromium, own profile) or a CDP-attach adapter (the user's already-running
Chrome); a session can't switch browsers mid-run, so the backend is chosen once
at startup by the factory. A remote / cloud-browser adapter can drop in behind
this port later without touching the tool.

Browser automation is inherently tied to its driver, so the port exposes the live
session surface the tool needs (the active page + context) plus the high-level
helpers. The `browser_render` fetch provider also depends on `ensure()` +
`context` to open an isolated page.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class BrowserProvider(Protocol):
    active_page: Any  # the live driver page the tool acts on
    context: Any      # the browser context (for opening isolated pages / tabs)

    async def ensure(self) -> None: ...
    async def close(self) -> None: ...
    async def settle(self) -> None: ...
    async def snapshot(self, **kwargs) -> str: ...
    def resolve_ref(self, ref: str, page: Any = None): ...

    # --- tab handles (stable ids + labels) ---------------------------------
    def resolve_target(self, target_id: str | None): ...   # -> page
    def tab_handle(self, page: Any) -> str: ...             # stable id/label for a page
    def list_tabs(self) -> list[dict]: ...

    # --- introspection / extras --------------------------------------------
    async def status(self) -> dict: ...
    async def doctor(self) -> dict: ...
    async def profiles(self) -> dict: ...
