# `unbilled-spend`

> `unbilled_cost_usd{service=model-proxy}` Sum > 0 over 5 minutes.

## What broke

A model call reached a provider, we paid for it, and no account could be charged. The proxy
emits this metric only when that happens, so its mere existence is the alarm — there is no
threshold to tune.

## What it costs while it lasts

Real money, at whatever rate traffic is flowing, **and it is unrecoverable**. Every other failure
in this system leaves something to reconstruct from: a failed ledger write can be replayed, a
declined renewal retried. This one cannot. The provider has been paid and there is no record of
who to bill, so the amount is gone the moment it is spent. Treat it as page-now regardless of the
hour.

There is also no user-visible symptom. The chat SUCCEEDED — that is the point. Nobody will report
this.

## What to check

Amount and which models, first:

```
filter ispresent(unbilled_cost_usd)
| stats sum(unbilled_cost_usd) as usd, count(*) as calls by model
| sort usd desc
```

Then how the request got in without an account:

```
filter ispresent(auth_total)
| stats count(*) as n by credential, outcome
```

## How to fix it

Most likely first.

1. **The master key is being used for user traffic.** `credential=master` on requests that should
   be `credential=session` means something is calling the proxy with `LITELLM_MASTER_KEY`
   directly — a misconfigured daemon, a test script left running, or a desktop that never
   completed `platform.connect`. The master key is intended for the cloud daemon's own internal
   calls; those are genuinely unattributable and are supposed to be a trickle, not a stream.
2. **Accounts `/resolve` is failing open.** `custom_auth.py` deliberately allows a call through
   when accounts is unreachable, because blocking every user during an accounts blip is worse
   than a small unattributed spend. Check `accounts-unreachable` — if it is also firing, fix that
   and this stops with it.
3. **A token resolved to an account that no longer exists.** Rare. Look for `outcome=error` on
   `auth_total` with a `reason`.

## Stopping the bleeding

If it is (1) and you cannot find the caller quickly, rotating `LITELLM_MASTER_KEY` in Secrets
Manager and rolling the proxy stops the unattributed traffic immediately. **It also stops the
cloud daemon**, so only do this if the spend rate justifies an outage.
