"""Accounts-only client for the private platform administration API."""

import json
import os
import urllib.request


def call(action, payload=None):
    base, key = (
        os.environ.get("PLATFORM_CONTROL_URL", ""),
        os.environ.get("PLATFORM_CONTROL_KEY", ""),
    )
    if not base or not key:
        raise RuntimeError("platform administration is not configured")
    request = urllib.request.Request(
        base.rstrip("/") + "/admin/secrets/" + action,
        data=json.dumps(payload or {}).encode(),
        method="POST",
        headers={"Content-Type": "application/json", "X-Platform-Control-Key": key},
    )
    with urllib.request.urlopen(request, timeout=15) as response:
        return json.load(response)
