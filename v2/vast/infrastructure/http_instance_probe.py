"""Asks ComfyUI on a rented machine whether it is serving. Stdlib only, like the rest of this
module's infrastructure.

`/api/system_stats` is the endpoint the agent's own `comfy_probe` uses, so "the platform says
ready" and "the agent can reach it" are the SAME check — the platform just makes it first.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request


class HttpInstanceProbe:
    def __init__(self, timeout_s: float = 10.0) -> None:
        # Short, but not too short: this runs inside a poll the window makes every 20 seconds,
        # and a machine that takes longer than this to answer a stats call is not ready anyway.
        # Ten rather than five because ComfyUI's FIRST /api/system_stats initialises CUDA and
        # can take several seconds on a cold process; a probe that gave up at five would keep
        # reporting a serving machine as "not yet" for as long as that first call kept getting
        # cut off.
        self._timeout_s = timeout_s

    def answers(self, url: str, auth: str = "") -> bool:
        headers = {"Accept": "application/json"}
        if auth:
            # The portal fronts ComfyUI with auth; WEB_PASSWORD is honoured as a Bearer token.
            # Without it a booted, healthy machine answers 401 and would never read as ready.
            headers["Authorization"] = f"Bearer {auth}"
        req = urllib.request.Request(f"{url.rstrip('/')}/api/system_stats", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self._timeout_s) as res:
                if res.status != 200:
                    return False
                body = res.read(65536).decode("utf-8", "replace")
        except (urllib.error.URLError, OSError, ValueError):
            # Refused, timed out, reset, a bad address: all mean "not yet", none mean "error".
            return False
        try:
            return isinstance(json.loads(body), dict)
        except ValueError:
            # Something answered on the port but it was not ComfyUI's API — the image's portal
            # page, say, which the supervisor serves before ComfyUI itself is up.
            return False
