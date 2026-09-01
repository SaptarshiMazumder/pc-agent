# `resolve-latency-p99`

> `resolve_latency_ms{service=accounts}` p99 > `resolve_latency_p99_ms` (dev: 1000ms) over 5 minutes.

## What broke

Turning a session token into an account is taking too long.

## Why this is the platform's latency floor

`/resolve` runs **before every uncached model call, for every user**. Whatever it costs is added to
every message anyone sends. A p99 of one second means the slowest 1% of messages start a full
second late before a model has been asked anything — and because it is the same single accounts
task for everyone, one user's slow query is everyone's slow message.

It is also the leading indicator for [`accounts-unreachable`](accounts-unreachable.md). Accounts
rarely dies outright; it gets slow, then it times out, then it is "down".

## What to check

Both sides measure this — start by finding out which one is lying:

```
filter ispresent(resolve_latency_ms)
| stats count(*) as n, avg(resolve_latency_ms) as avg, pct(resolve_latency_ms,50) as p50,
        pct(resolve_latency_ms,99) as p99 by service, bin(5m)
```

`service=accounts` is the work; `service=model-proxy` is work + network. A gap between them is the
network or the ALB, not the database.

Is the cache doing its job?

```
filter ispresent(auth_total)
| stats count(*) as n by cache, outcome
```

`cache=hit` means the proxy answered without calling accounts at all. A collapsing hit rate turns
every message into a database query and is the most common cause of this alarm.

## How to fix it

1. **SQLite write-lock contention.** The most likely cause and the structural one. Accounts is one
   task with one SQLite file, and only one writer at a time. Every `/usage` and `/debit` write
   competes with reads. Look for the alarm coinciding with traffic bursts or with a scheduled job
   — see DEF-12 (fixed: renewals now commit per subscription rather than per batch) and DEF-6 (the
   root: this needs Postgres, not tuning).
2. **PBKDF2 on the sign-in path.** 200k rounds is deliberately slow. It should not affect
   `/resolve`, but a burst of sign-ins saturating the single task's CPU will. Check `login_ms`.
3. **The auth cache was disabled or its TTL shortened.** Check the proxy's config.
4. **The task is CPU-starved.** 256 CPU units is a quarter vCPU. If the container is pegged, the
   answer is a bigger task, and that is a legitimate fix rather than a workaround.

## The real fix

This alarm exists to tell you when the SQLite trade has stopped being viable. Every mitigation
above buys time; Postgres is the answer, and it comes as one unit of work with teaching accounts to
speak it (see the note at the top of `infra/modules/data.tf`).
