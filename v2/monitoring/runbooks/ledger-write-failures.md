# `ledger-write-failures`

> `ledger_write_total{service=model-proxy, outcome=fail}` Sum > 0 over 5 minutes.

## What broke

The proxy tried to record a model call's cost against an account and the write did not land. The
call itself succeeded — the user got their answer — so this is invisible to them.

This is **DEF-1**: these failures used to be swallowed entirely, which is why the counter exists.

## What it costs while it lasts

Money spent and not billed, plus a growing buffer of rows waiting to be replayed. Unlike
`unbilled-spend` this is usually *recoverable*: the proxy buffers a failed row and retries it, so
a short accounts outage costs nothing but delay. It becomes real loss when the buffer overflows or
the process restarts — see `ledger-buffer-backlog`, which is the alarm for that.

## What to check

Why the writes are failing:

```
filter ispresent(ledger_write_total) and outcome = "fail"
| stats count(*) as n by reason
| sort n desc
```

Whether they are coming back:

```
filter ispresent(ledger_write_total)
| stats count(*) as n by outcome, bin(5m)
```

And how deep the buffer is right now:

```
filter ispresent(ledger_buffer_depth)
| stats max(ledger_buffer_depth) as depth by bin(5m)
```

## How to fix it

1. **Accounts is down or unreachable.** `reason=http_error` with a 5xx, or `reason=unreachable`.
   Check the accounts target group's health and `accounts-unreachable`. Fixing accounts drains the
   buffer on its own.
2. **The internal key rotated on one side only.** `reason=http_error` with **401**. The proxy
   presents `ACCOUNTS_INTERNAL_KEY` from the shared secret; if accounts was redeployed with a new
   one and the proxy was not rolled, every write is rejected. Roll the proxy.
3. **The accounts database is not writable.** `reason=http_error` with 500. Hit
   `GET /health/ready` on accounts — it does a real write, so a read-only EFS mount shows up there
   and nowhere else.
4. **Schema drift.** `reason=http_error` with **422**. The proxy is sending a field accounts does
   not accept — almost always a partial deploy where one image is newer than the other. Deploy both.

## Verifying recovery

`outcome=duplicate` rows appearing as the buffer drains is CORRECT, not a problem: the replay
found rows that had actually committed before the response was lost, and the `event_id` unique
index refused the second copy. That is DEF-11 working.
