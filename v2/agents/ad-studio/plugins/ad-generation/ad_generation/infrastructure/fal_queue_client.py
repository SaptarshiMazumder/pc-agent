"""fal's queue API: submit, poll the status until done, read the result.

The key rides as `${FAL_KEY}` — the host substitutes it at the last moment, so this code never
holds it (the app's global Settings hold it; plugin.toml lists it as a secret).
"""

from __future__ import annotations

import time

from agent_runtime.infrastructure.net.outbound import fetch

from ad_generation.application.interfaces.provider_refused import ProviderRefused

_QUEUE = "https://queue.fal.run/"
_AUTH = {"Authorization": "Key ${FAL_KEY}"}
# A 4xx that is about THIS input - fal's content filter (422 content_policy_violation), a
# rejected file, a bad field - as opposed to the key (401/403), the pace (429) or the clock (408).
_NOT_A_REFUSAL = (401, 403, 408, 429)


def _refusal(status: int) -> bool:
    return 400 <= status < 500 and status not in _NOT_A_REFUSAL


class FalQueueClient:
    def __init__(self, poll_s: float) -> None:
        self._poll_s = poll_s

    def run(self, model: str, payload: dict, timeout_s: float) -> dict:
        res = fetch(_QUEUE + model, method="POST", headers=_AUTH, json=payload, timeout_s=120)
        if not res.ok:
            if _refusal(int(res.status or 0)):
                raise ProviderRefused("fal", model, f"HTTP {res.status}: {res.text[:500]}")
            raise RuntimeError(f"fal would not take {model}: {res.error or f'HTTP {res.status}: {res.text[:500]}'}")
        queued = res.json()
        status_url, response_url = queued.get("status_url"), queued.get("response_url")
        if not status_url or not response_url:
            raise RuntimeError(f"fal queued {model} but named no status/result URL: {res.text[:300]}")

        deadline = time.monotonic() + timeout_s
        while True:
            status = fetch(status_url, headers=_AUTH, timeout_s=60)
            if not status.ok:
                raise RuntimeError(f"fal status for {model} failed: {status.error or f'HTTP {status.status}: {status.text[:300]}'}")
            body = status.json()
            if body.get("error"):
                raise RuntimeError(f"fal failed {model}: {body.get('error_type') or ''} {body['error']}".strip())
            if body.get("status") == "COMPLETED":
                break
            if time.monotonic() > deadline:
                raise TimeoutError(f"fal did not finish {model} within {int(timeout_s)} s (request {queued.get('request_id')})")
            time.sleep(self._poll_s)

        result = fetch(response_url, headers=_AUTH, timeout_s=120)
        if not result.ok:
            # A model's own refusal (moderation, a bad input) comes back here, with its reason.
            if _refusal(int(result.status or 0)):
                raise ProviderRefused("fal", model, f"HTTP {result.status}: {result.text[:500]}")
            raise RuntimeError(f"fal failed {model}: {result.error or f'HTTP {result.status}: {result.text[:500]}'}")
        return result.json()
