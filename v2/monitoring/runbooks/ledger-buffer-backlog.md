# `ledger-buffer-backlog`

> `ledger_buffer_depth{service=model-proxy}` Maximum above threshold over 5 minutes.

## What broke

The proxy's in-memory queue of billing rows that failed to write and are waiting to be retried is
not draining. `ledger-write-failures` says writes are failing; this says the backlog is winning.

## What it costs while it lasts

**The buffer is in memory.** Everything in it is lost if the proxy restarts, is rolled by a
deploy, or is replaced by ECS for any reason — including the deploy you might be about to do to
fix the underlying problem. Depth is therefore a direct measure of money at risk right now.

## What to check

Depth over time — is it growing, flat, or draining?

```
filter ispresent(ledger_buffer_depth)
| stats max(ledger_buffer_depth) as depth by bin(1m)
| sort @timestamp desc
```

Then go to [`ledger-write-failures`](ledger-write-failures.md) — this alarm is a symptom, and its
cause is always there.

## How to fix it

**Fix the write path, do not restart the proxy.** That is the whole point of this runbook. The
instinct when a service looks stuck is to roll it; here that instinct destroys the data.

Order of operations:

1. Diagnose via `ledger-write-failures`. Do not deploy anything yet.
2. Fix accounts (or the key, or the mount).
3. Watch depth fall. It drains on its own — no action needed.
4. Only once depth is at zero, resume normal deploys.

If the underlying fix genuinely requires rolling the proxy, accept the loss knowingly and record
how much: `sum(model_cost_usd)` over the window tells you roughly what went unbilled.

## Why the buffer is not on disk

It was a deliberate trade: the proxy is a stateless container with no volume, and giving it one
to protect against a failure mode that only exists while accounts is already down was judged not
worth the operational weight. That trade is only defensible *because this alarm exists*. If it
starts firing regularly, the trade has stopped being correct — that is the signal to move the
buffer to a durable queue, not to raise the threshold.
