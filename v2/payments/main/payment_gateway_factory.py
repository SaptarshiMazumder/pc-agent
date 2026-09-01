"""Which rail is in play — decided in exactly one place, from the environment.

    AGENTD_PAYMENT_PROVIDER      null | stripe | razorpay (unset => null)
    STRIPE_SECRET_KEY            sk_test_… / sk_live_…    (required when stripe)
    STRIPE_WEBHOOK_SECRET        whsec_…                  (required to verify callbacks)
    STRIPE_STATEMENT_DESCRIPTOR  what shows on the card statement (optional, 22 chars)
    RAZORPAY_KEY_ID              rzp_test_… / rzp_live_…  (required when razorpay)
    RAZORPAY_KEY_SECRET          the key's secret half    (required when razorpay)
    RAZORPAY_WEBHOOK_SECRET      set when creating the webhook in their dashboard
    DODO_API_KEY                 required when dodo
    DODO_PRODUCT_ID              the ONE pay-what-you-want product in Dodo's catalog; a
                                 merchant of record can only sell from its catalog, and its
                                 dashboard currency must be the currency the books charge in
    DODO_WEBHOOK_SECRET          whsec_…                  (required when dodo)
    DODO_API_BASE_URL            optional; defaults to the live host — Dodo separates test
                                 and live by HOST (https://test.dodopayments.com), not by key

IT RAISES ON A NAME IT DOES NOT KNOW. The version this replaces fell back to the mock rail, on
the reasoning that sign-in must not break because a payment setting is wrong. That was defensible
while no rail existed and is dangerous now: with `AGENTD_PAYMENT_PROVIDER=stipe` the fallback
means every checkout SUCCEEDS, grants the credits, records a sale, and takes no money — a
misspelling turns the shop into a money printer, and nothing anywhere looks wrong. A service that
will not start is a five-minute outage; this is unrecoverable revenue loss discovered at month
end, if at all. It raises on a MISSING KEY for the same reason.

Sign-in is unaffected either way: nothing on the identity path builds a gateway.

THE IMPORT OF THE STRIPE ADAPTER IS DEFERRED, and this is the one place in the codebase where
that is correct rather than a smell: it is the composition root, and the adapter pulls in httpx,
which the accounts image should not be forced to carry to run on the mock rail.
"""

from __future__ import annotations

import os

from payments.application.interfaces.payment_gateway import (
    PaymentConfigurationError,
    PaymentGateway,
)
from payments.application.interfaces.webhook_verifier import WebhookVerifier
from payments.infrastructure.null_payment_gateway import NullPaymentGateway

NULL = "null"
STRIPE = "stripe"
RAZORPAY = "razorpay"
DODO = "dodo"

KNOWN = (NULL, STRIPE, RAZORPAY, DODO)
#: The rails that call back. The mock rail settles inline; it has nothing to deliver later.
WEBHOOK_RAILS = (STRIPE, RAZORPAY, DODO)


def configured_provider_name() -> str:
    return (os.environ.get("AGENTD_PAYMENT_PROVIDER") or NULL).strip().lower() or NULL


def _require(name: str) -> str:
    value = (os.environ.get(name) or "").strip()
    if not value:
        raise PaymentConfigurationError(
            f"{name} is not set, and AGENTD_PAYMENT_PROVIDER={configured_provider_name()}. "
            f"Refusing to start a checkout that cannot take money."
        )
    return value


def build_payment_gateway() -> PaymentGateway:
    name = configured_provider_name()
    if name == NULL:
        return NullPaymentGateway()
    if name == STRIPE:
        from payments.infrastructure.stripe_api_client import StripeApiClient
        from payments.infrastructure.stripe_payment_gateway import StripePaymentGateway

        return StripePaymentGateway(
            StripeApiClient(_require("STRIPE_SECRET_KEY")),
            statement_descriptor=(os.environ.get("STRIPE_STATEMENT_DESCRIPTOR") or "").strip(),
        )
    if name == RAZORPAY:
        from payments.infrastructure.razorpay_api_client import RazorpayApiClient
        from payments.infrastructure.razorpay_payment_gateway import RazorpayPaymentGateway

        return RazorpayPaymentGateway(
            RazorpayApiClient(_require("RAZORPAY_KEY_ID"), _require("RAZORPAY_KEY_SECRET"))
        )
    if name == DODO:
        from payments.infrastructure.dodo_api_client import DEFAULT_BASE_URL, DodoApiClient
        from payments.infrastructure.dodo_payment_gateway import DodoPaymentGateway

        return DodoPaymentGateway(
            DodoApiClient(
                _require("DODO_API_KEY"),
                base_url=(os.environ.get("DODO_API_BASE_URL") or DEFAULT_BASE_URL).strip(),
            ),
            product_id=_require("DODO_PRODUCT_ID"),
        )
    raise PaymentConfigurationError(
        f"unknown payment provider {name!r} (AGENTD_PAYMENT_PROVIDER); expected one of "
        f"{', '.join(KNOWN)}"
    )


def build_webhook_verifier() -> WebhookVerifier:
    """The callback half. Built separately because the webhook route exists only for rails that
    have one — asking the gateway for a verifier would put a null implementation on the mock
    rail, and a no-op signature check is the one thing this endpoint must never have."""
    name = configured_provider_name()
    if name == STRIPE:
        from payments.infrastructure.stripe_webhook_verifier import StripeWebhookVerifier

        return StripeWebhookVerifier(_require("STRIPE_WEBHOOK_SECRET"))
    if name == RAZORPAY:
        from payments.infrastructure.razorpay_webhook_verifier import RazorpayWebhookVerifier

        return RazorpayWebhookVerifier(_require("RAZORPAY_WEBHOOK_SECRET"))
    if name == DODO:
        from payments.infrastructure.dodo_webhook_verifier import DodoWebhookVerifier

        return DodoWebhookVerifier(_require("DODO_WEBHOOK_SECRET"))
    raise PaymentConfigurationError(
        f"the {name} rail has no webhook; nothing calls back into this service"
    )


def has_webhook() -> bool:
    """Whether to mount the webhook route at all. A route that exists only to answer 500 is worse
    than a 404: it tells an operator the endpoint is configured when it is not."""
    return configured_provider_name() in WEBHOOK_RAILS
