# `no-successful-logins`

> `login_total{service=accounts, outcome=ok}` Sum < 1 over `login_absence_window_minutes` (30).
> **`treat_missing_data = "breaching"`** — one of only two alarms here where absence is the signal.
> **Ships DISABLED** (`enable_login_absence_alarm = false`).

## What broke

Nobody has signed in during the window. Not "sign-ins failed" — [`login-rejections`](login-rejections.md)
covers that. This is *silence*, which is the failure mode no ordinary alarm can see.

## Why it needs its own alarm at all

Every other alarm here uses `treat_missing_data = "notBreaching"`, because for a money alarm no
data is health. That choice has one blind spot: a total outage produces no data either. A dead
front door and a healthy quiet night look identical to every other alarm in the stack.

This is the one that tells them apart, by inverting the assumption.

## Why it is off in dev

A dev environment is legitimately silent for hours. Turned on, it would page every night and be
muted within a week, which is worse than not having it. **Enable it in production**, where a
half-hour with zero sign-ins genuinely means something is wrong:

```hcl
enable_login_absence_alarm   = true
login_absence_window_minutes = 30
```

Pick the window from real traffic: it must be comfortably longer than the quietest legitimate gap.
Get that wrong and you have built an alarm that trains people to ignore alarms.

## What to check

First, is anything else reaching the service?

```
filter ispresent(auth_total)
| stats count(*) as n by outcome, bin(5m)
```

If `/resolve` traffic is also zero, nothing is reaching accounts at all — go to
[`accounts-unreachable`](accounts-unreachable.md) and `accounts-unhealthy`.

If `/resolve` is healthy but sign-ins are zero, the service is up and the sign-in path
specifically is broken, or the CLIENT cannot reach it:

```powershell
Invoke-RestMethod "http://<alb>:4100/health"
Invoke-RestMethod -Method Post "http://<alb>:4100/login" -Body '{"email":"probe@x","password":"x"}' -ContentType 'application/json'
```

A `401` from that probe is a **good** sign — the endpoint works and rejected a bad password. A
timeout, a 500, or a connection refusal is the fault.

## How to fix it

1. **The whole environment is down.** Other alarms will be firing; this is not the one to work from.
2. **The clients cannot reach accounts even though we can.** The desktop app reads `accounts_url`
   from its baked `distribution.toml`, and the web build bakes `VITE_AGENTD_ACCOUNTS_URL` at image
   build time. **A replaced ALB changes the DNS name and orphans every shipped client**, with the
   service perfectly healthy the whole time. This has already happened once. Compare the flavors'
   URL against `terraform output accounts_url`.
3. **CORS.** Browser-only, and invisible server-side: the request never arrives, so the logs show
   nothing rather than showing an error. Check `ACCOUNTS_CORS_ORIGINS` against the web origin.
4. **It really is quiet.** Nobody used the product for half an hour. Widen the window.
