"""The webhook route, as a mountable router.

WHY A ROUTER AND NOT A ROUTE IN THE HOST APP. Three things about this endpoint are payment
knowledge and would be re-derived (wrongly) by anyone adding it themselves:

  1. THE BODY MUST NOT BE PARSED. The signature covers the exact bytes. Declaring `payload: dict`
     lets FastAPI parse and hand over a re-serialised object, and verification then fails 100% of
     the time for reasons that look like a wrong secret.
  2. THE STATUS CODE IS AN INSTRUCTION TO THE RAIL, not a report to a user. Stripe reads non-2xx
     as "retry", and disables an endpoint that keeps failing — so an event we merely have no rule
     for must answer 200, or it takes the events we DO care about down with it. Only a rejected
     signature is a 4xx.
  3. IT RUNS IN A THREADPOOL. Handling touches SQLite, and doing that inline in an async endpoint
     blocks the event loop for every other request on the service.

The host supplies `handle` — a callable that owns the database and the order. This module never
learns what post-processing means.
"""

from __future__ import annotations

import logging
from typing import Callable

from fastapi import APIRouter, HTTPException, Request
from fastapi.concurrency import run_in_threadpool

from payments.application.interfaces.webhook_verifier import WebhookRejected

log = logging.getLogger("payments")

WEBHOOK_PATH = "/payments/webhook"


def build_payment_router(
    handle: Callable[[bytes, str], dict], *, path: str = WEBHOOK_PATH
) -> APIRouter:
    router = APIRouter()

    @router.post(path)
    async def payment_webhook(request: Request) -> dict:
        body = await request.body()
        signature = request.headers.get("stripe-signature", "")
        try:
            return await run_in_threadpool(handle, body, signature)
        except WebhookRejected as e:
            # Deliberately no body contents in the log: a rejected delivery is the shape an
            # attack takes, and echoing it into CloudWatch is how the payload gets stored.
            log.warning("payment webhook rejected: %s", e)
            raise HTTPException(status_code=400, detail=str(e)) from e

    return router
