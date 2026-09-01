# `cost-per-hour`

> `model_cost_usd{service=model-proxy}` Sum > `cost_per_hour_alarm_usd` (dev: $5) over 1 hour.

## What broke

Possibly nothing. This is the one alarm here that is not a defect signal — it is a **spend
tripwire**.

## What it is actually for

Not budget. It catches the two things that turn a normal week into a surprise invoice:

- **A runaway loop.** An agent that re-invokes itself, a tool that returns something the model
  always responds to, a cron job that fires far more often than intended. `max_turns` caps a single
  run; it does nothing about a thousand runs.
- **Abuse.** One account discovering that credits are cheaper than the inference they buy, or a
  leaked token being used at scale.

Set the threshold from **observed normal**, not from what you are willing to spend. A budget-shaped
threshold only fires once the money is already gone.

## What to check

Where is it going?

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as usd, count(*) as calls by model
| sort usd desc
```

Who is spending it?

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as usd, count(*) as calls by account_id
| sort usd desc
| limit 20
```

One account dominating, with a call count out of proportion to the cost, is a loop. One account
dominating with a normal ratio is either a real power user or abuse.

Is it one run?

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as usd, count(*) as calls by run_id
| sort usd desc
| limit 10
```

A single `run_id` with hundreds of calls **is** the runaway loop, and the trace will show you which
tool it is bouncing off.

## How to fix it

1. **Runaway loop.** Find the `agent_id` and `trigger`. If `trigger=cron`, disable the job first
   and diagnose after. If it is interactive, `max_turns` should have stopped it — if it did not,
   the loop is across runs, not within one, and that is a delegation or heartbeat cycle.
2. **One account, sustained.** Their credits cap their spend, so the exposure is bounded by what
   they bought — unless `cap-overspend` is also firing, in which case that is the real incident.
3. **Everyone, gradually.** Not an incident: it is growth, or a model-default change that moved
   traffic to a more expensive model. Check `model_defaults` in config and raise the threshold
   deliberately.

## Note on DEF-9

There is an open defect: **a second, unattributed model call happens per message** (likely title
generation or a memory write). Every message therefore costs roughly twice what the turn count
implies. Until that is identified, factor it in before concluding the spend is anomalous.
