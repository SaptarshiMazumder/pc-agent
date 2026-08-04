# CloudWatch queries — the operational questions, and the exact query for each

Where to run these: **CloudWatch → Logs → Logs Insights**, select log group **`/agentd/dev`**,
set the time range top-right, paste, Run query.

Two things to know before anything else.

**The metric name is the field key.** A cost line looks like `{"model_cost_usd": 0.0031, ...}`.
There is no `metric_name` column to filter on — you filter on `ispresent(model_cost_usd)`.

**Dimensions are graphable, properties are only searchable.** `service`, `outcome`, `reason`,
`direction`, `credential` are dimensions. `run_id`, `account_id`, `agent_id`, `model`, `tool`
are properties. That is why per-user cost can be *searched* here but not *graphed* in the
Metrics console — dimensions are billed per unique combination, and one per user would be
ruinous. Per-user graphs come from the accounts database instead.

---

## What CloudWatch can and cannot tell you (desktop Cloud mode)

| Question | Answer lives |
|---|---|
| money spent per request / per user | **CloudWatch** (model-proxy, accounts) |
| refused for no credits, or model above plan tier | **CloudWatch** |
| auth failures, accounts outages, billing write failures | **CloudWatch** |
| true end-to-end latency (click to answer) | the **user's PC** (`run_duration_ms`) |
| which tools ran, how long each took, tool errors | the **user's PC** |
| run aborted or crashed mid-way | the **user's PC** |

In desktop Cloud mode the daemon runs on the user's machine, so its lines go to
`~/.agentd/logs/daemon.log` and nothing uploads them. In hosted web mode the daemon runs on
Fargate and all of it lands here. Use `monitoring/trace.ps1` to stitch both halves for one
request on your own machine.

---

## 1. What did ONE request cost?

```
filter run_id = "PASTE_RUN_ID"
| stats sum(model_cost_usd)        as cost_usd,
        sum(credits_charged_total) as credits_charged,
        sum(model_call_total)      as model_calls
```

`model_calls` is often **higher than the number of turns the user saw** — background work
(title generation, memory writes) rides the same run_id. That gap is the difference between
what a message appears to cost and what it actually costs.

Token split for the same request:

```
filter run_id = "PASTE_RUN_ID" and ispresent(tokens_total)
| stats sum(tokens_total) as tokens by direction
```

`direction=cached` is the lever on cost of goods: cache reads bill at roughly a tenth of a
normal input token, and agents carry large stable system prompts.

## 2. What HAPPENED in one request?

```
fields @timestamp, service, outcome, reason, model, model_cost_usd,
       credits_charged_total, run_refused_total, ledger_row_total
| filter run_id = "PASTE_RUN_ID"
| sort @timestamp asc
| limit 200
```

Read it as a story, oldest first. Blank cells are normal — each line only carries the fields
its own metric has. `credential=session` is an auth event; a lone `outcome=ok` is a funding
check or a ledger write.

How long the AWS side took:

```
filter run_id = "PASTE_RUN_ID"
| stats min(@timestamp) as first_seen, max(@timestamp) as last_seen, count(*) as lines
```

**This is not end-to-end latency.** It excludes the user's typing-to-first-byte, the daemon's
prompt assembly, tool execution on their machine, and rendering. For the real number, read
`run_duration_ms` from that user's daemon — which today means asking them, or item 5.1.

## 3. Everything ONE USER did, request by request

The single most useful query for a support ticket:

```
filter account_id = "PASTE_ACCOUNT_ID"
| stats min(@timestamp)            as started,
        sum(model_cost_usd)        as cost_usd,
        sum(credits_charged_total) as credits,
        sum(model_call_total)      as calls
  by run_id
| sort started desc
| limit 50
```

One row per request, newest first, with what each cost.

Their spend over time:

```
filter account_id = "PASTE_ACCOUNT_ID" and ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, sum(credits_charged_total) as credits by bin(1h)
```

Which models they are actually using:

```
filter account_id = "PASTE_ACCOUNT_ID" and ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, count(*) as calls by model
| sort cost_usd desc
```

## 4. Did a user get refused? (budget or plan tier)

```
filter ispresent(run_refused_total) and account_id = "PASTE_ACCOUNT_ID"
| fields @timestamp, reason, model, model_tier
| sort @timestamp desc
```

`reason` values:

| reason | HTTP | Means |
|---|---|---|
| `no_credits` | 402 | hard stop, out of credits. No overdraft — that is deliberate. |
| `model_tier` | 403 | the agent asked for a model above this grant's ceiling |

Both are the **gate working**, not faults. Across all users, to see whether refusals are
normal or a spike:

```
filter ispresent(run_refused_total)
| stats count(*) as n by reason, bin(1h)
```

## 5. Did something actually go WRONG?

```
filter outcome in ["fail","error","rejected","unavailable","exception","insufficient","overspend"]
| stats count(*) as n by service, outcome, reason
| sort n desc
```

Nothing returned = nothing failing. The ones that matter, in order:

| Signal | Means | Severity |
|---|---|---|
| `unbilled_cost_usd` > 0 | money paid to a provider that could not be billed to anyone | **worst** — no other symptom exists |
| `ledger_write_total{outcome=fail}` | a billing row did not land | high |
| `ledger_buffer_depth` > 0 | rows queued in the proxy's MEMORY because accounts is unreachable; lost if the task is replaced, which every deploy does | high |
| `debit_applied_total{outcome=overspend}` | the call ran but the balance could not cover it — measures how leaky the cap is | medium |
| `auth_total{outcome=unavailable}` | accounts unreachable; the proxy fails OPEN so users keep working unmetered | medium |
| `funding_lookup_total{outcome=unavailable}` | same, on the gate side — nobody is being capped right now | medium |

Unattributed spend, in dollars:

```
filter ispresent(unbilled_cost_usd)
| stats sum(unbilled_cost_usd) as unbilled_usd by model
```

## 6. Platform-wide money

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, sum(credits_charged_total) as credits by bin(1h)
```

Top spenders:

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, sum(model_call_total) as calls by account_id
| sort cost_usd desc
| limit 20
```

Cost per model, which is where margin is decided:

```
filter ispresent(model_cost_usd)
| stats sum(model_cost_usd) as cost_usd, sum(credits_charged_total) as credits, count(*) as calls
  by model
| sort cost_usd desc
```

`credits` divided by `cost_usd` should be near the configured credits-per-dollar rate. Drift
means the conversion and reality have separated.

## 7. Sign-in health

```
filter ispresent(login_total)
| stats count(*) as n by outcome, bin(1h)
```

If nobody can sign in, nothing else about the platform matters.

```
filter ispresent(auth_total)
| stats count(*) as n by credential, outcome, cache
```

`cache=hit` means the proxy answered without calling accounts. A collapsing hit rate makes
accounts a bottleneck on the hot path.

---

## 8. Did the scheduled jobs run, and what did they do?

These live in a **different log group** — `/aws/lambda/agentd-dev-scheduled-jobs`, not
`/agentd/dev` — because they run in a Lambda rather than in a container. Pick it in the Logs
Insights dropdown, or prefix the query with
`SOURCE '/aws/lambda/agentd-dev-scheduled-jobs'`.

```
fields @timestamp, job, path, outcome, status
| filter ispresent(outcome)
| sort @timestamp desc
```

One line per run. `outcome` is `ok`, `http_error` or `unreachable`.

```
fields @timestamp, job, result.renewed, result.already_charged, result.failed, result.grants_closed
| filter outcome = "ok"
| sort @timestamp desc
```

What each run actually did. `renewed` is new revenue; `already_charged` is a retried run that
correctly charged nobody twice; `failed` is a declined card left due for the next hour.

```
fields @timestamp, job, status, detail
| filter outcome != "ok"
| sort @timestamp desc
```

Why a run failed. `401` means the internal key in the Lambda's environment no longer matches
Secrets Manager; `unreachable` means the accounts task is down or the security-group rule from
the Lambda's group is gone; a `500` body is the app's own error.

**The failure this query cannot show you is silence** — a deleted or disabled schedule produces
no lines at all. For that, check the clock still exists:

```powershell
aws scheduler list-schedules --name-prefix agentd-dev
./scheduler_check.ps1              # or this, which also invokes each job and reports the result
```

---

## When to use the database instead

CloudWatch Logs are for **operations** — what is happening now, why a request behaved oddly.
They expire with the log group's retention.

The `usage` table in accounts is the **permanent** money record: one row per model call with
`run_id`, `turn_id`, `account_id`, `agent_id`, `model`, `model_tier`, tokens, `cached_tokens`,
`credits`, `funding_source`. Billing questions, invoices, creator payouts and anything
month-over-month come from there, never from here.

Live balance for one account:

```powershell
Invoke-RestMethod "http://$alb`:4100/funding?account_id=ACCT" -Headers @{ "X-Internal-Key" = $key }
```

## Scripted shortcuts

```powershell
./cloud_check.ps1                        # money, failures, ledger, auth in one pass
./cloud_check.ps1 -AccountId acct_x      # adds their live credit balance
./trace.ps1                              # last request, all five hops, both machines
./trace.ps1 -List                        # recent run_ids to choose from
./alarm_check.ps1                        # replay every alarm's query — can it actually fire?
./scheduler_check.ps1                    # run all three scheduled jobs, report what they did
./scheduler_check.ps1 -ListOnly          # just the cron table, touch nothing
```
