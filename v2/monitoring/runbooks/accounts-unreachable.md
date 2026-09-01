# `accounts-unreachable`

> `SUM([FILL(auth_unavailable,0), FILL(funding_unavailable,0)])` > 0 over 5 minutes.
> The only metric-math alarm in the stack — the two metrics mean the same thing operationally and
> would always fire together, so they are one alarm rather than two pages.

## What broke

The Model Proxy could not reach the accounts service, either to resolve a session token
(`auth_unavailable`) or to check a balance (`funding_unavailable`).

## What it costs while it lasts

**This is the platform's single point of failure — DEF-6.** Accounts sits in the hot path of every
model call, is one task, and uses SQLite (so it cannot be scaled to two without corruption). When
it is unreachable:

- Model calls **fail open**, so users keep working. Good for them.
- Spend during the outage is therefore **unattributed** — expect `unbilled-spend` to fire too.
- Sign-in fails outright for anyone not already signed in.

Failing open is deliberate: blocking every user during an accounts blip is worse than a bounded
amount of unbilled spend. It does mean the money cost grows with the outage's length, so this is
urgent even though nobody is complaining.

## What to check

Is it down, or just slow?

```
filter ispresent(resolve_latency_ms)
| stats count(*) as n, avg(resolve_latency_ms) as avg_ms, pct(resolve_latency_ms, 99) as p99 by bin(1m)
```

Is the task actually running and healthy?

```powershell
aws ecs describe-services --cluster agentd-dev --services agentd-dev-accounts --region ap-northeast-1 `
  --query "services[0].{running:runningCount,desired:desiredCount,status:status}"
aws elbv2 describe-target-health --target-group-arn <accounts-tg-arn> --region ap-northeast-1
```

Can it do its job, as opposed to merely answering?

```powershell
Invoke-RestMethod "http://<alb>:4100/health/ready"     # does a real WRITE
```

## How to fix it

1. **The task is gone or crash-looping.** `runningCount: 0`. Check the accounts log stream for the
   boot error. The deployment circuit breaker should already have rolled back a bad image — if
   `status` shows a failed deployment, the previous task definition is what is running.
2. **The EFS mount went read-only or vanished.** `/health` returns 200 (liveness does not touch
   the DB, on purpose) while `/health/ready` returns 503. This is exactly the case readiness was
   split out to catch. Check the EFS mount targets and the `agentd-dev-efs` security group.
3. **It is up but slow enough to time out.** `resolve-latency-p99` will be firing too. Usually
   SQLite write-lock contention — see [`resolve-latency-p99`](resolve-latency-p99.md).
4. **The security group lost its rule.** The proxy reaches accounts via
   `accounts.agentd.local:4100`, permitted by the service SG's self-rule. A Terraform change that
   dropped it produces a clean, total, instant failure with a healthy-looking task.

## After it recovers

Check the ledger buffer drained (`ledger-buffer-backlog`) and reconcile what was spent
unattributed (`unbilled-spend`). Neither clears itself just because accounts came back.
