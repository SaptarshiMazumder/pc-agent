# `scheduled-jobs-failing`

> `AWS/Lambda Errors{FunctionName=agentd-<env>-scheduled-jobs}` Sum > 0 over 5 minutes.

## What broke

One of the three scheduled accounts jobs raised. The handler re-raises on every failure mode —
non-2xx, DNS failure, timeout — specifically so that all of them land on this one vendor metric
(which is free, unlike a custom one).

## What it costs while it lasts

Entirely depends on which job, and they are not close:

| job | schedule | what stops |
|---|---|---|
| `subscription-renewals` | hourly | **subscribers are not being charged.** Revenue stops, silently. |
| `close-expired-credits` | daily 00:05 UTC | expired credits stay on the books as a liability; revenue is understated |
| `ledger-snapshot` | daily 00:20 UTC | the business dashboard's balance-sheet rows go flat |

Renewals is the one that costs money. The other two are accounting lag and cosmetics.

Note that **none of them lose data by failing** — every one is idempotent and simply runs again on
its next tick. A single failure is not urgent; a persistent one is.

## What to check

The jobs log to their **own** group — `/aws/lambda/agentd-dev-scheduled-jobs`, not `/agentd/dev`:

```
fields @timestamp, job, path, outcome, status, detail
| filter outcome != "ok"
| sort @timestamp desc
```

Reproduce it by hand with the exact payload the schedule sends:

```powershell
cd v2\monitoring
.\scheduler_check.ps1              # runs all three, reports what each returned
```

## How to fix it

1. **`http_error` with 401.** The internal key in the Lambda's environment no longer matches
   Secrets Manager. Happens when accounts is redeployed with a rotated key and the Lambda is not
   re-applied. Run `terraform apply` — the key comes from `random_password.accounts_internal_key`,
   so a plan will show the drift.
2. **`unreachable`.** Either the accounts task is down (check `accounts-unhealthy`) or the service
   security group lost its ingress rule from `agentd-<env>-scheduled-jobs`. The Lambda is in the
   VPC precisely so the key never crosses the public internet, which means it is also entirely
   dependent on that rule.
3. **`http_error` with 500.** The app's own error; the `detail` field carries the reason
   verbatim. `renew-due` is the one with real logic in it.
4. **`http_error` with 404.** The endpoint does not exist — the accounts image predates the job.
   Deploy accounts.
5. **Timeout.** `renew-due` with a large batch. It commits per subscription (DEF-12), so a partial
   run is safe and the next tick picks up the rest; raise `scheduled_job_timeout_seconds` if it
   keeps happening.

## The failure this alarm cannot see

A job that does not RUN produces no errors. That is
[`scheduled-jobs-not-running`](scheduled-jobs-not-running.md) — and it ships disabled, so check
that it has been enabled before assuming silence means health.
