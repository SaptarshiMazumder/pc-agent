"""CheckoutService — start a payment, and finish it here if the rail already can.

THE ORDER OF THE THREE STEPS IS THE DESIGN:

  1. ask the rail          this is the step that costs money and can be declined
  2. RECORD THE ATTEMPT    unconditionally, before anything is delivered
  3. POST-PROCESS, only if the rail says the money is actually ours

Step 2 happens even when step 1 failed, because a decline is evidence too — it is what a support
question ("my card was refused") is answered from, and what a fraud pattern is spotted in.

WHY POST-PROCESSING SOMETIMES HAPPENS HERE AND SOMETIMES ON A WEBHOOK. That is not a branch on
which rail is configured — nothing in this system is allowed to check that. It is a branch on
what the rail SAID: if the returned intent already succeeded, waiting for a webhook that will
never arrive would leave a paying customer with nothing. If it did not, handing over the credits
now would deliver before the money moved.
"""

from __future__ import annotations

import time
from typing import Callable

from payments.application.interfaces.payment_gateway import PaymentGateway, PurchaseRequest
from payments.application.interfaces.payment_intent_store import PaymentIntentStore
from payments.application.interfaces.payments_post_processor import PaymentsPostProcessor
from payments.domain.payment_intent import PaymentIntent
from payments.domain.processed_payment import ProcessedPayment


class CheckoutService:
    def __init__(
        self,
        gateway: PaymentGateway,
        intents: PaymentIntentStore,
        post_processor: PaymentsPostProcessor,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._gateway = gateway
        self._intents = intents
        self._post_processor = post_processor
        self._clock = clock

    def begin(
        self, request: PurchaseRequest, *, off_session: bool = False
    ) -> tuple[PaymentIntent, ProcessedPayment | None]:
        """Take the money and, when it is already ours, deliver.

        Returns the intent always, and the ProcessedPayment ONLY when delivery happened.
        A `None` therefore means one of two very different things — declined, or waiting
        on the customer — and the caller must read `intent.status` to tell them apart
        rather than treating both as failure. `intent.awaiting_customer` needs a redirect.
        """
        at = self._clock()
        charge = self._gateway.charge_off_session if off_session else self._gateway.begin_purchase
        intent = charge(request)
        self._intents.record(intent, at=at)
        if not intent.succeeded:
            return intent, None
        return intent, self._post_processor.process(intent)
