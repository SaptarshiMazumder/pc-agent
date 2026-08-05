# `model-proxy-5xx`

> `AWS/ApplicationELB HTTPCode_Target_5XX_Count` for the model-proxy target group,
> Sum > `proxy_5xx_threshold` (dev: 5) over 5 minutes.

## What broke

The Model Proxy is returning server errors. Users are seeing failed messages right now.

## Why an absolute count and not an error RATE

A percentage is the textbook answer and it is wrong at this traffic level: with three requests in
a window, one error is a 33% error rate and pages you for nothing. The threshold is a count, and it
must be raised as real volume arrives — a fixed count that was right at ten users is noise at ten
thousand. Revisit it when `model_call_total` has an order of magnitude more traffic.

## What is NOT in this metric

**402 and 403 are not errors.** Out of credits (402) and above your model tier / not entitled (403)
are the gate working exactly as designed, and they are 4xx, so they never reach this alarm. They
appear on the service-health dashboard under *Refusals*. If someone reports "it stopped working"
and this alarm is quiet, check refusals first — that is the far more common cause.

## What to check

Ours or the provider's?

```
filter ispresent(model_call_total) and outcome != "ok"
| stats count(*) as n by outcome, reason, model
| sort n desc
```

If it is concentrated on one `model`, it is upstream. If it is spread across all of them, it is us.

Then a single failing request end to end:

```powershell
./trace.ps1 -List          # recent run ids
./trace.ps1 -RunId <id>    # all five hops
```

## How to fix it

1. **A provider is down or rate-limiting us.** Errors on one `model` or one `provider`. Nothing to
   fix on our side; `model_fallbacks` is the mitigation and is config, not code. Check the
   provider's status page before doing anything else.
2. **A provider key is invalid or out of quota.** 401/403 from upstream surfacing as our 500.
   Check Secrets Manager and the provider's billing page. A key that expired produces a total,
   instant failure for that provider only.
3. **The accounts callback is failing and taking requests with it.** Cross-check
   [`ledger-write-failures`](ledger-write-failures.md) — the ledger write is supposed to be
   after-the-fact and non-blocking, so if 5xx correlates with write failures, something in that
   path has become load-bearing that should not be.
4. **LiteLLM itself.** The proxy runs a **pinned** litellm (1.88.1) because the custom-auth hook
   contract changes between versions. If someone unpinned it, this is where that shows up.

## Escalation

model-proxy down = total product outage. There is no degraded mode: every model call in both
desktop Cloud mode and hosted web goes through it. Roll back first, diagnose second.
