"""RazorpayApiClient — the HTTP call to Razorpay, and nothing else.

WHY httpx AND NOT THE `razorpay` SDK — the same reasoning as StripeApiClient, which this
mirrors: the surface we need is three endpoints, and hand-rolling them means every part is
testable with an `httpx.MockTransport` against the real request-building code instead of a mock
of a vendor package's attribute chain.

TWO MECHANICAL DIFFERENCES FROM STRIPE, so the reader does not diff the files to find them:

  * AUTH IS HTTP BASIC — `key_id:key_secret` — not a bearer token.
  * THE BODY IS JSON, not form-encoded bracket notation, so there is no encoder function here;
    the payload dict is sent as it is.

THERE IS NO IDEMPOTENCY HEADER. Razorpay does not offer Stripe's replay-the-response semantics;
what it offers instead is a UNIQUE `reference_id` on a Payment Link — a second create with the
same reference errors rather than double-charging. The gateway turns that error back into
idempotency by fetching the link the first attempt made (see RazorpayPaymentGateway), which is
why this client also has `get`.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.razorpay.com"
DEFAULT_TIMEOUT = 20.0


class RazorpayApiError(RuntimeError):
    """Razorpay answered, and said no.

    Carries the rail's OWN description, because that is the only wording of the problem a user
    can act on, and inventing our own would be a worse translation of a system we do not own.
    """

    def __init__(self, message: str, *, status: int = 0, code: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code


class RazorpayApiClient:
    def __init__(
        self,
        key_id: str,
        key_secret: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any = None,
    ) -> None:
        if not key_id or not key_secret:
            raise ValueError("a Razorpay key id and key secret are required")
        self._auth = (key_id, key_secret)
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def post(self, path: str, data: dict) -> dict:
        with httpx.Client(
            timeout=self._timeout, transport=self._transport, auth=self._auth
        ) as client:
            response = client.post(f"{self._base_url}{path}", json=data)
        return self._read(response)

    def get(self, path: str, params: dict | None = None) -> dict:
        with httpx.Client(
            timeout=self._timeout, transport=self._transport, auth=self._auth
        ) as client:
            response = client.get(f"{self._base_url}{path}", params=params or {})
        return self._read(response)

    @staticmethod
    def _read(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error = body.get("error") or {}
            raise RazorpayApiError(
                str(error.get("description") or f"Razorpay returned HTTP {response.status_code}"),
                status=response.status_code,
                code=str(error.get("code") or ""),
            )
        return body if isinstance(body, dict) else {}
