# `<service>-unhealthy`

> `AWS/ApplicationELB UnHealthyHostCount` ≥ 1 over 60 seconds, one alarm per service.
> Alarms: `web-unhealthy`, `model-proxy-unhealthy`, `accounts-unhealthy`, `daemon-unhealthy`,
> `ingest-unhealthy`.

## What broke

The load balancer's health check is failing against a service's task. Unlike every other alarm
here, this one comes from **AWS's own metrics**, not ours — so it fires even when the container is
so broken it emits nothing at all. That is precisely its value: it is the alarm that works when
our telemetry does not.

## What it costs while it lasts

Depends entirely on which service, and it is worth knowing the difference before you are woken up:

| service | what stops |
|---|---|
| `accounts` | sign-in; metering fails open (see [`accounts-unreachable`](accounts-unreachable.md)) |
| `model-proxy` | **every model call, for every user** — total outage of the product |
| `daemon` | the hosted web client only; desktop users run their own daemon and are unaffected |
| `web` | the browser client will not load; desktop unaffected |
| `ingest` | telemetry from desktop and browser is dropped. No user impact at all. |

`model-proxy` is the one that is an outage. `ingest` is the one you can look at on Monday.

## What to check

```powershell
aws ecs describe-services --cluster agentd-dev --services agentd-dev-<svc> --region ap-northeast-1 `
  --query "services[0].{running:runningCount,desired:desiredCount,events:events[0:3].message}"
```

The last three service events usually name the cause outright ("unable to pull image", "task
failed container health checks", "unable to place task").

Then the container's own last words:

```powershell
aws logs tail /agentd/dev --since 15m --filter-pattern "<svc>" --region ap-northeast-1
```

## How to fix it

1. **A bad image was just deployed.** The circuit breaker (`deployment_circuit_breaker` in
   `services.tf`) should have rolled it back automatically — check whether `rolloutState` says
   `FAILED` and the previous task definition is serving. If so the site is fine and what you have
   is a broken build to fix, not an incident.
2. **The container boots too slowly.** `health_check_grace` covers app startup; the daemon gets
   120s because it loads the whole agent runtime. A newly-slow service (a big new plugin) can
   exceed it and be killed before it ever answers. Raise the grace, do not disable the check.
3. **The health path itself changed.** The ALB checks `health_path` from the services map. A
   service that renamed its endpoint is perfectly healthy and permanently unhealthy at once.
4. **It genuinely crashed.** The log tail will show it. Nothing special about this case.

## Not the same as `no-successful-logins`

This alarm sees the ALB's view. A service can be healthy to the ALB and useless to users — that is
what the application-level alarms are for. Both firing narrows it down fast; only this one firing
usually means infrastructure, only the other usually means logic.
