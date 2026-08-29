"""DodoApiClient — the HTTP call to Dodo Payments, and nothing else.

WHY httpx AND NOT THEIR SDK — the same reasoning as the Stripe and Razorpay clients this
mirrors: two endpoints, testable with an `httpx.MockTransport` against the real
request-building code.

MECHANICS: Bearer auth, JSON bodies. Dodo runs SEPARATE HOSTS for test and live mode rather
than test-prefixed keys on one host, which is why the base url is constructor input that the
factory reads from the environment — pointing a live key at the test host is a config error
their side reports, not something this client guesses about.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://live.dodopayments.com"
DEFAULT_TIMEOUT = 20.0


class DodoApiError(RuntimeError):
    """Dodo answered, and said no.

    Carries the rail's OWN message where it gives one, because that is the only description of
    the problem a user can act on.
    """

    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class DodoApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("a Dodo Payments API key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def post(self, path: str, data: dict) -> dict:
        headers = {"Authorization": f"Bearer {self._api_key}"}
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            response = client.post(f"{self._base_url}{path}", json=data, headers=headers)
        return self._read(response)

    @staticmethod
    def _read(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            message = ""
            if isinstance(body, dict):
                # Dodo's error body is not stable across endpoints; take whichever of its two
                # message fields is present, and fall back to the status line rather than guess.
                message = str(body.get("message") or body.get("error") or "")
            raise DodoApiError(
                message or f"Dodo Payments returned HTTP {response.status_code}",
                status=response.status_code,
                code=str(body.get("code") or "") if isinstance(body, dict) else "",
            )
        return body if isinstance(body, dict) else {}
