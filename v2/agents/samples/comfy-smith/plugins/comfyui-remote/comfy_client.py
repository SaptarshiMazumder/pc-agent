"""ComfyClient — the one place this agent talks to a ComfyUI server.

THE SERVER IS SOMEWHERE ELSE. Not on this machine: a pod on RunPod, an instance on Vast, a box
in the next room. Everything the agent needs to know about it — the GPU, the VRAM, which nodes
are installed, which checkpoints exist, whether a graph actually runs — is answerable over its
HTTP API. That is the whole design: the agent never guesses what is installed and never asks the
user to describe their hardware, because the server will say.

WHY ONE CLIENT AND NOT AN httpx CALL PER TOOL. Six tools share a base URL, an auth header, a
timeout policy and one large cached document (`/object_info` is megabytes). Spreading that across
six files means six subtly different versions of it, and the one that forgets the auth header
fails only for the users who put ComfyUI behind a proxy.

FAILURES ARE RETURNED, NOT SWALLOWED. Every error here carries what the agent must do next —
which setting is missing, which URL refused, which node the server rejected. A tool that returns
"could not connect" teaches the agent nothing and it retries the same call.
"""

from __future__ import annotations

import json
import os
from typing import Any

import aiohttp

from agent_runtime.application.run_context import current_setting_env

#: Declared in agent.toml. Repeated here because a tool has to be able to name the exact field
#: the user must fill in — "set COMFY_URL in Settings" is actionable, "not configured" is not.
URL_KEY = "COMFY_URL"
TOKEN_KEY = "COMFY_TOKEN"
SSH_KEY = "COMFY_SSH"

#: Long enough for a cold `/object_info` on a busy pod, short enough that an unreachable host
#: fails inside one turn instead of hanging the conversation.
HTTP_TIMEOUT_S = 60


class ComfyError(RuntimeError):
    """Anything the agent should read and act on: a missing setting, a refused connection, a
    rejected graph. The message IS the instruction."""


def setting(key: str) -> str:
    """A declared setting's value for THIS agent.

    Never `os.environ[key]` directly: a declared setting is stored under the agent's own
    prefixed name so two agents can hold different values for COMFY_URL. `current_setting_env`
    is the one resolver that knows that rule.
    """
    return os.environ.get(current_setting_env(key), "").strip()


def ssh_target() -> str:
    """The remote shell, if the user gave one. Optional by design — everything except installing
    custom nodes and downloading models is doable over HTTP."""
    return setting(SSH_KEY)


class ComfyClient:
    """One ComfyUI server, addressed over HTTP."""

    def __init__(self, base: str, token: str = "") -> None:
        self.base = base.rstrip("/")
        self._token = token

    @classmethod
    def from_settings(cls) -> ComfyClient:
        url = setting(URL_KEY)
        if not url:
            raise ComfyError(
                "No ComfyUI server configured. Open this agent's Settings and set "
                f"{URL_KEY} to the address of your ComfyUI — a RunPod/Vast proxy URL like "
                "https://abc123-8188.proxy.runpod.net, or http://host:8188. "
                "Ask the user for it if they have not given you one; do not guess."
            )
        if not url.startswith(("http://", "https://")):
            raise ComfyError(f"{URL_KEY} must start with http:// or https:// — got {url!r}")
        return cls(url, setting(TOKEN_KEY))

    @property
    def headers(self) -> dict[str, str]:
        # Some hosts put the pod behind an authenticating proxy. Sent only when set, because a
        # bare ComfyUI rejects nothing and an empty Authorization header confuses some proxies.
        return {"Authorization": f"Bearer {self._token}"} if self._token else {}

    def url(self, path: str) -> str:
        return f"{self.base}/{path.lstrip('/')}"

    # -- requests ---------------------------------------------------------------------------

    async def get_json(self, path: str, params: dict | None = None) -> Any:
        return await self._json("GET", path, params=params)

    async def post_json(self, path: str, body: dict) -> Any:
        return await self._json("POST", path, json_body=body)

    async def _json(
        self, method: str, path: str, params: dict | None = None, json_body: dict | None = None
    ) -> Any:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
                async with session.request(
                    method, self.url(path), params=params, json=json_body
                ) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        # ComfyUI reports a rejected graph as a 400 with the per-node reason in
                        # the body. That body is the single most useful thing the agent can read,
                        # so it is passed through verbatim rather than replaced with the status.
                        raise ComfyError(
                            f"{method} {path} -> HTTP {resp.status}\n{_trim(text, 4000)}"
                        )
                    if not text.strip():
                        return {}
                    try:
                        return json.loads(text)
                    except ValueError as e:
                        raise ComfyError(
                            f"{method} {path} returned {resp.status} but not JSON ({e}). "
                            f"Is {self.base} really a ComfyUI server, and not a login page?\n"
                            f"{_trim(text, 500)}"
                        ) from e
        except aiohttp.ClientError as e:
            raise ComfyError(_unreachable(self.base, e)) from e
        except TimeoutError as e:
            raise ComfyError(
                f"{self.base} did not answer within {HTTP_TIMEOUT_S}s. The pod may be starting, "
                f"asleep, or loading a model."
            ) from e

    async def get_bytes(self, path: str, params: dict | None = None) -> bytes:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S * 5)  # images can be large
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
                async with session.get(self.url(path), params=params) as resp:
                    if resp.status >= 400:
                        raise ComfyError(f"GET {path} -> HTTP {resp.status}")
                    return await resp.read()
        except aiohttp.ClientError as e:
            raise ComfyError(_unreachable(self.base, e)) from e

    async def upload_image(self, filename: str, data: bytes, subfolder: str = "") -> dict:
        """POST /upload/image — puts a file in the server's INPUT folder so a LoadImage node can
        name it. This is how a local image becomes an img2img/controlnet input on a remote box."""
        form = aiohttp.FormData()
        form.add_field("image", data, filename=filename, content_type="application/octet-stream")
        form.add_field("type", "input")
        form.add_field("overwrite", "true")
        if subfolder:
            form.add_field("subfolder", subfolder)
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_S * 5)
        try:
            async with aiohttp.ClientSession(timeout=timeout, headers=self.headers) as session:
                async with session.post(self.url("/upload/image"), data=form) as resp:
                    text = await resp.text()
                    if resp.status >= 400:
                        raise ComfyError(f"upload failed: HTTP {resp.status}\n{_trim(text, 1000)}")
                    return json.loads(text) if text.strip() else {}
        except aiohttp.ClientError as e:
            raise ComfyError(_unreachable(self.base, e)) from e

    def ws_url(self, client_id: str) -> str:
        # Same host, ws/wss to match http/https — a wss page cannot open a ws socket, and a
        # RunPod proxy is always https.
        scheme = "wss" if self.base.startswith("https") else "ws"
        rest = self.base.split("://", 1)[1]
        return f"{scheme}://{rest}/ws?clientId={client_id}"


def _unreachable(base: str, err: Exception) -> str:
    return (
        f"Could not reach ComfyUI at {base} ({type(err).__name__}: {err}). "
        f"Check the pod is running and the port is exposed. If it is behind an authenticating "
        f"proxy, set {TOKEN_KEY} in Settings."
    )


def _trim(text: str, limit: int) -> str:
    text = text.strip()
    return text if len(text) <= limit else f"{text[:limit]}\n… ({len(text) - limit} more chars)"
