"""PaymentEventService — the webhook, as four steps that must happen in this order.

    verify → claim → record → post-process

VERIFY FIRST, ALWAYS. The endpoint is open to the internet; until the signature checks out there
is no event, only bytes a stranger posted.

CLAIM BEFORE RECORD, RECORD BEFORE POST-PROCESSING. Claiming is what makes a redelivery — which
is normal, Stripe retries for three days — cost nothing. Recording before delivering means that
if delivery blows up, we still know money arrived; the reverse order loses the payment entirely
on a crash between the two.

AN EVENT WE HAVE NO RULE FOR IS RECORDED AND IGNORED, not treated as a failure. Rails emit dozens
of event types nobody subscribed to, and answering 500 to those makes the rail retry them
forever and eventually disable the endpoint — taking the events we DO care about with it.
"""

from __future__ import annotations

import time
from typing import Callable, Mapping

from payments.application.interfaces.payment_intent_store import PaymentIntentStore
from payments.application.interfaces.payments_post_processor import PaymentsPostProcessor
from payments.application.interfaces.webhook_verifier import WebhookVerifier
from payments.domain import payment_event


class PaymentEventService:
    def __init__(
        self,
        verifier: WebhookVerifier,
        intents: PaymentIntentStore,
        post_processor: PaymentsPostProcessor,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._verifier = verifier
        self._intents = intents
        self._post_processor = post_processor
        self._clock = clock

    def handle(self, body: bytes, headers: Mapping[str, str]) -> dict:
        """Raises `WebhookRejected` when the delivery is not the rail's. Everything else is a
        result, because the rail reads our status code as "retry or not". Which headers prove
        the delivery is the verifier's knowledge, not this service's — the mapping passes
        through untouched (keys lowercase, the router's contract)."""
        at = self._clock()
        event = self._verifier.verify(body, headers)
        if not self._intents.claim_event(event.id, at=at):
            return {"ok": True, "event": event.id, "duplicate": True, "processed": False}

        self._intents.record(event.payment, at=at)

        # TWO EVENTS CHANGE ANYTHING; the rest are recorded and acted on by nothing.
        #
        # A REFUND IS NOT OPTIONAL TO HANDLE, and for a while it was: this branch read
        # `!= PURCHASE_SUCCEEDED`, so `refund.processed` arrived, was recorded, and returned
        # `processed: False`. The money went back and the credits stayed — a refunded customer
        # kept a spendable balance, and nothing anywhere said so.
        #
        # It matters most for the refund nobody here initiated. Support issues one from the
        # rail's own dashboard, which is the normal way to honour a refund policy, and this
        # callback is the ONLY thing that will ever hear about it.
        if event.type == payment_event.REFUND_SUCCEEDED:
            done = self._post_processor.refund(event.payment)
        elif event.type == payment_event.PURCHASE_SUCCEEDED:
            done = self._post_processor.process(event.payment)
        else:
            return {"ok": True, "event": event.id, "type": event.type, "processed": False}
        return {
            "ok": True,
            "event": event.id,
            "type": event.type,
            "processed": True,
            "reference": done.reference,
            "created": done.created,
        }
