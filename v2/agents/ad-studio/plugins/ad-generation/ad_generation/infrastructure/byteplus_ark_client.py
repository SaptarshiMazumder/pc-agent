"""BytePlus ModelArk: images are one synchronous call; videos are a task created, then polled.

The key rides as `${BYTEPLUS_API_KEY}` — substituted by the host, never held here.
"""

from __future__ import annotations

import time

from agent_runtime.infrastructure.net.outbound import fetch

from ad_generation.application.interfaces.provider_refused import ProviderRefused

_BASE = "https://ark.ap-southeast.bytepluses.com/api/v3"
_AUTH = {"Authorization": "Bearer ${BYTEPLUS_API_KEY}"}
_DONE = "succeeded"
_FAILED = ("failed", "cancelled", "expired")


def _error(res) -> str:
    return res.error or f"HTTP {res.status}: {res.text[:500]}"


# A 4xx about THIS input (moderation, a rejected file, a bad field) - not the key (401/403), the
# pace (429) or the clock (408).
_NOT_A_REFUSAL = (401, 403, 408, 429)


def _refusal(status: int) -> bool:
    return 400 <= status < 500 and status not in _NOT_A_REFUSAL


class BytePlusArkClient:
    def __init__(self, poll_s: float) -> None:
        self._poll_s = poll_s

    def generate_image(self, body: dict, timeout_s: float) -> dict:
        res = fetch(f"{_BASE}/images/generations", method="POST", headers=_AUTH, json=body, timeout_s=timeout_s)
        if not res.ok:
            if _refusal(int(res.status or 0)):
                raise ProviderRefused("byteplus", str(body.get("model")), _error(res))
            raise RuntimeError(f"BytePlus would not take the image ({body.get('model')}): {_error(res)}")
        return res.json()

    def run_video_task(self, body: dict, timeout_s: float) -> dict:
        res = fetch(f"{_BASE}/contents/generations/tasks", method="POST", headers=_AUTH, json=body, timeout_s=120)
        if not res.ok:
            if _refusal(int(res.status or 0)):
                raise ProviderRefused("byteplus", str(body.get("model")), _error(res))
            raise RuntimeError(f"BytePlus would not take the video ({body.get('model')}): {_error(res)}")
        task_id = res.json().get("id")
        if not task_id:
            raise RuntimeError(f"BytePlus created no task: {res.text[:300]}")

        deadline = time.monotonic() + timeout_s
        while True:
            poll = fetch(f"{_BASE}/contents/generations/tasks/{task_id}", headers=_AUTH, timeout_s=60)
            if not poll.ok:
                raise RuntimeError(f"BytePlus task {task_id} could not be read: {_error(poll)}")
            task = poll.json()
            status = task.get("status")
            if status == _DONE:
                return task
            if status in _FAILED:
                err = task.get("error") or {}
                raise RuntimeError(f"BytePlus task {task_id} {status}: {err.get('code', '')} {err.get('message', '')}".strip())
            if time.monotonic() > deadline:
                raise TimeoutError(f"BytePlus task {task_id} did not finish within {int(timeout_s)} s (still {status})")
            time.sleep(self._poll_s)
