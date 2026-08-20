# `@agentd/billing`

The **one** credits-and-checkout implementation. Consumed by the agentd renderer, the agent SDK
and Agent Builder, so none of them owns a second one.

Same argument as [`@agentd/auth`](../auth/README.md): three hosts, one server, one set of rules
about money. A second copy is a second set of rounding, idempotency and refusal bugs.

## What a host must answer

```ts
new BillingClient({
  accountsUrl(),   // where the accounts service is
  accessToken(),   // a CURRENT token — the host refreshes, this never sees a stale one
  newKey()         // idempotency keys; injected because crypto.randomUUID is not universal
})
```

Those three differ per host and nothing else does:

| host | accountsUrl | accessToken |
|---|---|---|
| agentd renderer | its configured accounts URL | `lib/tokens.ts` TokenManager |
| agent window | discovered from the daemon (`/platform/status`) | SDK `identity()` |
| Agent Builder | via the SDK, same as an agent window | same |

## It buys through `/me/checkout`

A strict superset of `/me/purchase`: on a rail that settles in place it returns the completed
purchase with `checkoutUrl: ''`; on a card rail it returns a link to go and pay.

**So a caller that follows `checkoutUrl` when present and shows the balance otherwise is correct
on every rail, without ever asking which one is configured.** That is the rule the whole payments
module is built on — no code path may branch on which rail is in play.

An agent shipped today keeps working the day a real rail is switched on, with no change.

## Two rules worth not rediscovering

**The only thing a client may send is a `product_id`.** Price and credit count are read server-side
from the `products` row. A client that could name its own price mints itself a fortune with one
curl. Enforced by the server; stated here so nobody adds an `amount` parameter for convenience.

**Reads fail soft, the purchase fails loud.** A balance that cannot be fetched returns `null` and
renders as "unavailable" — honest and harmless, and better than a confident `0` shown to someone
who has credits. A failed purchase throws with the server's own words, because silently resolving
it leaves someone believing they bought something.
