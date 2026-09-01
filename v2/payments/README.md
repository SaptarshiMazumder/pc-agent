# `payments/` — the payment rail

Taking money, as its own module with its own layers. Imported in-process by the accounts service;
**not** a deployed service.

## Why it is not part of `accounts`

Accounts owns the **books** — who has credits, what was spent, what a creator is owed.
Payments owns the **rail** — asking a third party to move money, and being told, later and out of
band, whether it did.

Those fail differently and change for different reasons. A rail swap must not touch the ledger;
a pricing change must not touch Stripe.

## Why it is not a separate service

A purchase and the ledger entry it causes must commit or roll back **together**. Splitting them
across a network hop turns one transaction into a distributed one, and the failure that
introduces — money recorded as taken with no credits granted — is the exact failure this module
exists to prevent. So it shares the accounts connection, and never reads an accounts table.

## The dependency runs both ways, through interfaces only

```
accounts  ──uses──▶  PaymentGateway      declared here, implemented here
payments  ──uses──▶  PaymentsPostProcessor   declared here, implemented by accounts
```

This module never learns what a credit grant is. Accounts never imports `stripe`.

## The thing that shaped the design

The interface this replaced had one money-taking method returning a terminal yes/no,
synchronously, inside a database transaction — and the plan it came from claimed a real rail
would "replace ONE class and touch nothing else."

That was wrong, and it was the single thing blocking Stripe. **A card payment is not
synchronous.** The customer is redirected, may be challenged by their bank (3-D Secure is
mandatory in India and the EU), and the outcome arrives minutes later on a webhook. A boolean has
nowhere to put *"ask the human and come back."*

So there are two ways to take money, because there genuinely are two:

| | who is watching | can it finish in one request |
|---|---|---|
| `begin_purchase` | the customer, at a keyboard | sometimes — read the returned status |
| `charge_off_session` | nobody (a scheduled renewal) | usually, but may still need the customer |

Nothing in the system may branch on **which rail is configured**. Code branches on what the rail
**said**: an intent that already succeeded is post-processed now, one that has not is post-processed by the
webhook. If a code path only works because payments are mocked, it is not built yet.

## Layout

```
payments/
├── domain/          Money, PaymentIntent, PaymentEvent, ProcessedPayment, statuses
├── application/
│   ├── interfaces/  PaymentGateway · PaymentsPostProcessor · PaymentIntentStore · WebhookVerifier
│   └── services/    CheckoutService (start) · PaymentEventService (webhook)
├── infrastructure/  NullPaymentGateway · SqlitePaymentIntentStore
└── main/            payment_gateway_factory — the ONLY place a rail is named
```

## The flow, on a card

```
client                 accounts                  Stripe
  │  POST /me/checkout     │                        │
  ├───────────────────────▶│  create session        │
  │                        ├───────────────────────▶│
  │  {status: pending,     │◀───────────────────────┤
  │◀──  checkout_url}      │                        │
  │                                                 │
  ├──────── customer pays at the hosted page ──────▶│
  │                        │                        │
  │                        │◀── POST /payments/webhook (signed)
  │                        │    verify → claim → record → post-process
  │                        │    credits granted HERE, not above
```

Nothing is granted by `/me/checkout`. The same endpoint also works on the mock rail, where the
purchase settles inline and the response carries the completed order instead of a URL — so a
client that follows `checkout_url` when present is correct on either rail without asking which
one is configured.

## Configuration

```
AGENTD_PAYMENT_PROVIDER         null | stripe | razorpay | dodo (unset => null)
STRIPE_SECRET_KEY               sk_test_… / sk_live_…    required when stripe
STRIPE_WEBHOOK_SECRET           whsec_…                  required when stripe
STRIPE_STATEMENT_DESCRIPTOR     shown on the card statement (optional, 22 chars)
RAZORPAY_KEY_ID                 rzp_test_… / rzp_live_…  required when razorpay
RAZORPAY_KEY_SECRET             the key's secret half    required when razorpay
RAZORPAY_WEBHOOK_SECRET         set when creating the webhook in their dashboard
DODO_API_KEY                    required when dodo
DODO_PRODUCT_ID                 the one pay-what-you-want product in Dodo's catalog
DODO_WEBHOOK_SECRET             whsec_…                  required when dodo
DODO_API_BASE_URL               optional; test mode is a separate HOST, not a key prefix
AGENTD_CHECKOUT_RETURN_ORIGINS  comma-separated allowlist for success/cancel urls
```

**An unknown name raises rather than falling back.** The version this replaced fell back to the
mock rail, on the reasoning that sign-in must not break because a payment setting is wrong. That
was defensible while no real rail existed and is dangerous now: with `stipe` the fallback means
every checkout *succeeds*, grants the credits, records a sale and takes no money — and nothing
anywhere looks wrong. A service that will not start is a five-minute outage; that is
unrecoverable revenue loss found at month end, if at all.

## Status

| | |
|---|---|
| `NullPaymentGateway` | done — records intent, moves nothing, every consequence real |
| `StripePaymentGateway` — hosted Checkout | done |
| `StripeWebhookVerifier` + `POST /payments/webhook` | done |
| `RazorpayPaymentGateway` — hosted Payment Links | done — idempotency via unique `reference_id` (no header on this rail) |
| `RazorpayWebhookVerifier` | done — event id arrives in a header, not the body; replay is bounded by the claim, Razorpay signs no timestamp |
| `DodoPaymentGateway` — hosted checkout sessions (merchant of record) | done — needs ONE pay-what-you-want product in their catalog (`DODO_PRODUCT_ID`); the rail offers no idempotency, so a double-click can open two payable sessions — the grant dedupes on the ledger, a doubly-paid session surfaces in reconciliation as a refund to issue |
| `DodoWebhookVerifier` | done — Standard Webhooks (three headers, base64 secret decoded before HMAC) |
| `POST /me/checkout` on accounts | done |
| **Subscription renewals** | **not built on either real rail** — Stripe needs a card on file, Razorpay a recurring mandate. `charge_off_session` RAISES, so renewals fail loudly instead of looking like declines; `/subscriptions/renew-due` errors on the first due subscription. |
| `payout()` | declared, unimplemented — Stripe Connect / RazorpayX are their own projects |
| Terraform secrets for the rail keys | not wired |

Tests: `tests/unit/test_payments_module.py` (the rail-agnostic contract) and
`tests/unit/test_stripe_rail.py` (the request we send, the signature we trust, and an end-to-end
checkout-then-callback against the real accounts service). The pre-existing accounts contract
stays pinned by `tests/unit/test_ledger.py`, which is unchanged.
