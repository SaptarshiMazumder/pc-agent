"""StripeApiClient — the HTTP call to Stripe, and nothing else.

WHY httpx AND NOT THE `stripe` SDK. The surface we need is three endpoints, and what we gain by
hand-rolling it is that every part is testable without a network or a vendor package: a test
passes an `httpx.MockTransport` and exercises the real request-building code. Mocking the SDK's
nested attribute chain (`stripe.checkout.Session.create`) tests the mock instead. The SDK's real
value — API versioning, typed errors, retries — is in the parts this does not use. If that
changes, this class is the only thing that has to be replaced.

STRIPE TAKES FORM ENCODING, NOT JSON, and expresses nesting through bracket notation
(`line_items[0][price_data][unit_amount]`). That is the one genuinely fiddly part of talking to
it directly, so it is a single function with its own tests rather than string-building at each
call site.

THE IDEMPOTENCY KEY IS A HEADER, and it is the reason a retried checkout does not charge twice.
Stripe remembers the key for 24 hours and replays the original response.
"""

from __future__ import annotations

from typing import Any

import httpx

DEFAULT_BASE_URL = "https://api.stripe.com"
DEFAULT_TIMEOUT = 20.0


class StripeApiError(RuntimeError):
    """Stripe answered, and said no.

    Carries the rail's OWN message, because that is the only description of the problem a user
    can act on ("your card was declined") and inventing our own wording would be a worse
    translation of a system we do not own.
    """

    def __init__(self, message: str, *, status: int = 0, code: str = "", kind: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.kind = kind


def form_encode(data: dict, prefix: str = "") -> list[tuple[str, str]]:
    """Flatten to Stripe's bracket notation. `None` is dropped rather than sent as "None", which
    is a string Stripe would happily store."""
    out: list[tuple[str, str]] = []
    for key, value in data.items():
        name = f"{prefix}[{key}]" if prefix else str(key)
        if value is None:
            continue
        if isinstance(value, dict):
            out.extend(form_encode(value, name))
        elif isinstance(value, (list, tuple)):
            for index, item in enumerate(value):
                if isinstance(item, dict):
                    out.extend(form_encode(item, f"{name}[{index}]"))
                else:
                    out.append((f"{name}[{index}]", str(item)))
        elif isinstance(value, bool):
            out.append((name, "true" if value else "false"))
        else:
            out.append((name, str(value)))
    return out


class StripeApiClient:
    def __init__(
        self,
        api_key: str,
        *,
        base_url: str = DEFAULT_BASE_URL,
        timeout: float = DEFAULT_TIMEOUT,
        transport: Any = None,
    ) -> None:
        if not api_key:
            raise ValueError("a Stripe secret key is required")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._transport = transport

    def post(self, path: str, data: dict, *, idempotency_key: str = "") -> dict:
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/x-www-form-urlencoded",
        }
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key
        with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
            response = client.post(
                f"{self._base_url}{path}", content=_urlencode(form_encode(data)), headers=headers
            )
        return self._read(response)

    @staticmethod
    def _read(response: httpx.Response) -> dict:
        try:
            body = response.json()
        except ValueError:
            body = {}
        if response.status_code >= 400:
            error = body.get("error") or {}
            raise StripeApiError(
                str(error.get("message") or f"Stripe returned HTTP {response.status_code}"),
                status=response.status_code,
                code=str(error.get("code") or ""),
                kind=str(error.get("type") or ""),
            )
        return body if isinstance(body, dict) else {}


def _urlencode(pairs: list[tuple[str, str]]) -> bytes:
    from urllib.parse import urlencode

    return urlencode(pairs).encode("utf-8")
