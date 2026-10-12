"""Scoped RPC client for a disposable browser/MCP task. Never launches code locally."""
from __future__ import annotations

import asyncio
import json
import tempfile
import time
from pathlib import Path

import httpx

from agent_runtime.application.run_context import current_run_context
from agent_runtime.infrastructure import accounts, tenancy

from .microvm_backend import MicrovmPluginSandbox, _apply_changes_guarded, _tree_manifest


def session_owner() -> str:
    ctx = current_run_context()
    if ctx is None or not ctx.session_key or not accounts.account_id():
        raise PermissionError("authenticated chat context required")
    return tenancy.partition(ctx.agent_id) + ":" + ctx.session_key


class IsolatedSession:
    def __init__(self, config, kind: str, options: dict | None = None):
        self.backend = MicrovmPluginSandbox(config)
        self.kind, self.options = kind, options or {}
        self.owner = session_owner()
        self.slots = None
        self.index = 0
        self.lock = asyncio.Lock()
        self.last_used = time.monotonic()

    async def call(self, method: str, arguments: dict):
        if session_owner() != self.owner:
            raise PermissionError("runtime owner mismatch")
        async with self.lock:
            if self.slots and (time.monotonic() - self.last_used > 270 or float(self.slots.get("expires_at") or 0) < time.time() + 240):
                await self.close()
            workspace = Path(current_run_context().workspace).resolve()
            workspace.mkdir(parents=True, exist_ok=True)
            if self.slots is None:
                self.index = 0
                self.slots = await self.backend._ask({
                    "op": "session", "session_kind": self.kind, "config": self.options,
                    "host_workspace": str(workspace),
                })
                if not self.slots.get("ok"):
                    raise RuntimeError(self.slots.get("error") or "isolated session unavailable")
            if self.index >= len(self.slots["rpc_put_urls"]):
                raise RuntimeError("isolated session call limit reached")
            before = await asyncio.to_thread(_tree_manifest, workspace, frozenset())
            async with httpx.AsyncClient(timeout=60) as client:
                with tempfile.TemporaryDirectory(prefix="agentd-session-") as td:
                    archive = Path(td) / "workspace.zip"
                    await asyncio.to_thread(self.backend._zip_dir, str(workspace), frozenset(), archive)
                    from .microvm_backend import _file_chunks
                    response = await client.put(self.slots["workspace_put_url"], content=_file_chunks(archive),
                                                headers={"Content-Length": str(archive.stat().st_size)})
                    response.raise_for_status()
                index = self.index
                self.index += 1
                response = await client.put(self.slots["rpc_put_urls"][index],
                    content=json.dumps({"method": method, "arguments": arguments, "workspace": True}).encode())
                response.raise_for_status()
                deadline = time.monotonic() + 240
                status_check = 0.0
                while time.monotonic() < deadline:
                    response = await client.get(self.slots["rpc_get_urls"][index])
                    if response.status_code in (403, 404):
                        if self.slots.get("status_url") and time.monotonic() >= status_check:
                            status_check = time.monotonic() + 5
                            status = await client.get(self.slots["status_url"])
                            if status.status_code == 200:
                                failure = status.json().get("error") or "isolated session ended"
                                await self.close()
                                raise RuntimeError(failure)
                            if status.status_code not in (403, 404):
                                status.raise_for_status()
                        await asyncio.sleep(0.25)
                        continue
                    response.raise_for_status()
                    if len(response.content) > 10 * 1024 * 1024:
                        raise RuntimeError("runtime response too large")
                    answer = response.json()
                    if not answer.get("ok"):
                        raise RuntimeError(answer.get("error") or "runtime call failed")
                    if answer.get("changes_url"):
                        await _apply_changes_guarded(self.backend, answer["changes_url"], workspace, before, None, [])
                    self.last_used = time.monotonic()
                    return answer["result"]
                await self.close()
                raise TimeoutError("isolated session timed out; its runtime was stopped")

    async def close(self):
        if self.slots:
            try:
                await self.backend._ask({"op": "cancel", "job_id": self.slots["job_id"]})
            finally:
                self.slots = None
