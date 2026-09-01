# Accounts service

Identity, sessions, per-account budgets and the spend ledger — the **State plane**. One
accounts store is shared by the whole daemon fleet, and it is deliberately separate from both
the daemon and the Model Proxy: LiteLLM meters model calls, but *we* own accounts, budgets and
the ledger, so billing truth never becomes LiteLLM's internal DB.

`app.py` imports only the stdlib plus FastAPI — **no `agent_runtime` import**. That
independence is the point: the daemon only ever sees the HTTP contract below, so swapping
SQLite for Postgres (Stage 2) never touches the daemon.

## Contract

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/signup` | `{email, password, budget_usd?}` → `{account_id, email, budget_usd}` |
| `POST` | `/login` | `{email, password}` → `{token, account_id, email}` |
| `GET` | `/resolve` | `Authorization: Bearer <token>` → `{account_id, email, budget_usd, spent_usd, over}` |
| `GET` | `/budget/{account_id}` | → `{account_id, budget_usd, spent_usd, remaining_usd, over, period}` |
| `POST` | `/usage` | `{account_id, model, in_tokens, out_tokens, cost_usd}` → `{ok, spent_usd, over, remaining_usd}` |
| `GET` | `/health` | → `{ok: true, service: "accounts"}` |

`budget_usd: null` means unlimited (`remaining_usd` is then `null` and `over` is always false).
Budgets are per calendar month (UTC), bucketed by `period`.

A session token is the **browser's** credential, not a model key. The daemon resolves the token
to an account and meters that account's spend; the model key never leaves the server side.

## Run locally

```powershell
python accounts/run-local.py          # SQLite on :4100
```

Then point the daemon at it (accounts stays OFF unless this is set):

```
AGENTD_ACCOUNTS_URL=http://127.0.0.1:4100
```

The DB lands at `accounts/data/accounts.db` (override with `AGENTD_ACCOUNTS_DB`).

## Build the image

Isolated build context, exactly like `model_proxy/`:

```powershell
docker build -t agentd-accounts ./accounts
```

## Hardening knobs

All env-driven; unset means today's open local-dev behaviour.

| Var | Effect |
| --- | --- |
| `ACCOUNTS_INTERNAL_KEY` | when set, `/usage` requires `X-Internal-Key` (only trusted infra — the model proxy's callback — writes the ledger), and `/budget/{id}` requires the key **or** that account's own access token |
| `ACCOUNTS_CORS_ORIGINS` | comma-separated allowed origins (default `*`) |
| `ACCOUNTS_RATE_LIMIT` | per-IP fixed window `count/seconds` on `/signup` + `/login` + `/auth/*` (default `10/60`; `0/0` disables) |

## Identity

Sign-in itself lives in **`v2/identity/`** and is composed here — this service owns what an
account HAS (budgets, credits, the ledger); that module owns who someone IS. See
`identity/__init__.py` for why the line is drawn there.

| Var | Effect |
| --- | --- |
| `AGENTD_AUTH_ISSUER` | **required to sign in.** Stamped into every token as `iss` and checked by the daemon and the model proxy, so a token from another environment is refused by name. Unset ⇒ `/auth/*` reports 501 and `/login` reports 503 |
| `AGENTD_AUTH_ACCESS_TTL_S` | access-token life (default 600) |
| `AGENTD_AUTH_REFRESH_TTL_DAYS` / `_FAMILY_DAYS` | refresh sliding / absolute life (30 / 90) |
| `AGENTD_AUTH_ALG` | `EdDSA` (default) or `RS256` |
| `AGENTD_IDENTITY_KEK` | wraps signing keys at rest; unset ⇒ stored in clear with a warning |
| `AGENTD_IDENTITY_PROVIDER` | `local` (default) or `oidc` |
| `AGENTD_OIDC_PROVIDERS` | comma-separated external providers, each with `AGENTD_OIDC_<NAME>_DISCOVERY` / `_CLIENT_ID` / `_CLIENT_SECRET` / `_REDIRECT_URI` |

The opaque `sess_` session and its `ACCOUNTS_SESSION_TTL_DAYS` knob are **gone**: a credential is
now a signed access token that carries its own expiry, plus a rotating refresh token. Databases
created before this still contain an unused `sessions` table; it is never read or written.

## Tests

`tests/unit/test_accounts_service.py` — contract tests over the table above, in-process via
`fastapi.testclient`, no network and no daemon.

```powershell
pytest tests/unit/test_accounts_service.py -q
```
