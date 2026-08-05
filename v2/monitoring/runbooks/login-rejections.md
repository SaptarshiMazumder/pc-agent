# `login-rejections`

> `login_total{service=accounts, outcome=rejected}` Sum > `login_rejection_threshold` (dev: 20)
> over 5 minutes.

## What broke

Sign-ins are being refused at an unusual rate. Two completely different situations produce this,
and they need opposite responses:

- **Credential stuffing** — someone is trying a list of passwords against us.
- **Our password path is broken** — everyone's correct password is being rejected.

Telling them apart is the entire job of this runbook, and it takes one query.

## What to check

```
filter ispresent(login_total)
| stats count(*) as n by outcome, bin(5m)
```

**If `outcome=ok` is still healthy alongside the rejections**, sign-in works and the rejections are
attempts against accounts that do not exist or wrong passwords. That is an attack.

**If `outcome=ok` has gone to zero**, we are broken. Nobody can sign in. This is a full outage of
the front door, and it is far more urgent than an attack would be.

Then, for the attack case:

```
filter ispresent(login_total) and outcome = "rejected"
| stats count(*) as n by bin(1m)
```

A flat, machine-regular rate is automated. A ragged one is humans mistyping.

## How to fix it

### If sign-in is broken (`outcome=ok` at zero)

1. **A deploy changed the hashing parameters.** PBKDF2 rounds or the salt handling — every stored
   hash instantly stops matching. Roll back immediately; do not try to fix forward.
2. **The database is readable but sessions cannot be written.** Check
   `GET /health/ready` on accounts. A read-only mount lets the password check pass and the session
   insert fail, which surfaces as a rejection.

### If it is an attack

1. Rate limiting is already applied per IP (`ACCOUNTS_RATE_LIMIT`, default `10/60`), and it covers
   sign-up, sign-in **and** `/me/purchase`. A distributed attempt spreads across IPs and defeats
   it; a single source does not.
2. Tighten the window if the source is concentrated: set `ACCOUNTS_RATE_LIMIT` lower in the
   services map and redeploy accounts.
3. Check whether anything actually succeeded:

```
filter ispresent(login_total) and outcome = "ok"
| stats count(*) as n by account_id
| sort n desc
```

An account with an unusual number of successful sign-ins from an attack window is a compromise, not
a coincidence.

## What is missing

There is no per-account lockout and no CAPTCHA. Both are deliberate omissions at this stage — a
lockout is itself a denial-of-service vector against a known email — but if this alarm becomes
routine, per-account throttling is the next thing to build, not a higher threshold.
