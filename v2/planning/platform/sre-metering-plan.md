# SRE, Observability & Metering Plan — pc-agent (`v2/`)

> **Status:** ✅ **Phases 0 + 1 COMPLETE** — telemetry library, correlation ID end-to-end (client → daemon → proxy → money row), JSON logging everywhere, prepaid credits with a **hard cap enforced before the provider is called**, config-driven model tiers, and ledger replay. **590 unit tests green.** **Not yet deployed** — see the deploy card below. Next: Phase 2 (double-entry ledger + `NullPaymentProvider`) or jump to 3.5 (alarms) to make the new counters page. · **Scope:** Cloud mode only (desktop Cloud + hosted web) · **Home:** `v2/monitoring/` · **Diagram:** [`diagrams/sre-observability.puml`](diagrams/sre-observability.puml)
>
> **Two of the three live defects are now closed:** DEF-1 (silent billing loss — counted *and* replayed) and DEF-2 (budgets enforced nowhere in desktop Cloud mode — now enforced at the proxy, the one chokepoint neither topology can bypass). DEF-3 (SIGTERM kills live runs) remains, in Phase 4.
>
> **Working doc** — check boxes off as we go. Each phase is independently shippable. Resume at the first unchecked item.

## Progress notes

### 0.1 · Telemetry library — done

- **`v2/monitoring/agentd_telemetry/`**, installable (`pip install -e v2/monitoring`), **zero dependencies** — it is imported by the pinned-litellm proxy image, where a dependency is a risk.
- `count` / `timing` / `timer` / `gauge` / `money` emit one EMF JSON line to stdout. The `awslogs` driver already in `infra/modules/services.tf` carries it to CloudWatch, which extracts metrics automatically — **no new infrastructure**.
- **Cardinality guard is in code, not in a style guide** (D9): a dimension key exceeding 50 distinct values collapses to `__high_cardinality__` and warns once. Verified — fires at exactly 50. This is what stops `count("tool_call", tool=name)` becoming one billed metric per marketplace tool.
- `scope()` puts `run_id`/`account_id`/`agent_id` on a contextvar so every line carries them without threading an ID through hundreds of signatures.
- `redact.scrub` is an **allowlist** with a 200-char truncation — content cannot reach CloudWatch even by accident.
- `setup_logging()` gives JSON logs sharing the same context and the same allowlist. Verified live in the proxy.

### Wired into the Model Proxy

`model_proxy/custom_auth.py` now emits: `auth_total{credential,outcome,cache}`, `resolve_latency_ms`, `model_call_total`, `tokens_total{direction}`, `model_cost_usd`, and **`ledger_write_total{outcome,reason}` + `unbilled_cost_usd`** — the DEF-1 counter. Spend metrics are emitted *before* and independently of the ledger write, deliberately: the record of what we **spent** must not vanish along with the system that records what we **billed**.

Import is `try/except` with no-op fallbacks and logs `telemetry ENABLED|DISABLED` at boot, so a metrics package can never stop the proxy starting.

### Live local dashboard

`monitoring/dev_dashboard.py` tails the same lines and renders money / traffic / auth / recent failures. Verified against a simulated Accounts outage: correctly surfaced 7 failed ledger writes and `$0.0633` unbilled spend in red. UTF-8 console capability check with ASCII bar fallback (Windows cp1252).

### 0.2–0.5 — done

**0.2 · The tracking number, end to end.** `store.ts` mints `traceId: crypto.randomUUID()` → `gateway._chat_send` adopts it as `run_id` (full uuid4 now, not `[:12]` — it is a ledger join key) → `native.py` derives `turn_id` per loop iteration → `model_proxy.apply()` attaches `X-Agentd-Run-Id/Turn-Id/Agent-Id` → `custom_auth` recovers them → the ledger row stores `run_id` + `turn_id` (new columns + additive migration + index).

Verified as a chain, not per-file: bind → headers → recover-from-callback-kwargs → `SELECT … WHERE run_id` returns the money row.

`RunHandle` gained `parent_run_id` and `trigger` (`chat|cron|heartbeat|channel|webhook|app|subagent`), and `run_refused_total` now fires on the already-active-run rejection — a failure that previously happened *before* a run_id existed and was therefore invisible.

**Header recovery is version-defensive.** LiteLLM's success callback does not reliably share a task context with the auth hook, and where it stashes request headers has moved between releases — so `_trace_from_kwargs` probes three known shapes rather than pinning to one and silently losing correlation on the next upgrade.

**0.3 · JSON logging** in daemon (`main.py`, falls back to plain text when the package is absent), accounts, and proxy. Every line carries the run's ids and passes the allowlist.

**0.4 · Cross-process.** `RunContext` now carries `run_id`/`turn_id` as DATA, so the plugin sandbox — which already receives `ctx` — can re-establish correlation on the far side of a future out-of-process backend. **MCP is deliberately excluded:** an MCP session is long-lived and shared across many runs, so injecting a run id into its subprocess environment at spawn would pin one run's id forever. MCP tool calls are already correlated from our side at `native.py:399`.

**Layering.** The obvious implementation (AgentService reading the telemetry context) violates `v2/.importlinter` — application may not import infrastructure. Instead the ids ride a stdlib contextvar in `application/run_context.py`, set by presentation, read by application. No new dependency and it still works with telemetry uninstalled.

**0.5 · Packaging.** `model_proxy` and `accounts` now build from the `v2/` context so both can `pip install monitoring`; their Dockerfiles still COPY only their own handful of files, so neither image gains `agent_runtime`, plugins or agents. Updated in all three build call sites: `deploy.yml`, `push-images.ps1`, `compose.yaml`. The daemon image already used the `v2/` context and now installs the library too.

**Also instrumented on the way through** (Phase 3 work landing early because the hook points were already open): `run_total{outcome,trigger}`, `run_duration_ms`, and — at `native.py:399`, the single choke point every tool in the system passes through — `tool_call_total` and `tool_duration_ms`, with the tool NAME as a property rather than a dimension. That one line means every tool ever published to the marketplace is instrumented without its author doing anything.

### Verification

`lint-imports` fails in this environment (`Only one live display may be active at once`) — **pre-existing**, reproduced on a clean stash of HEAD; a rich/grimp console conflict, not a contract violation.

## Phase 1 — done

**Credits derive from cost** (`metering.credits_for`), one config dial `AGENTD_CREDITS_PER_USD`. Measured: an identical 10k-in/2k-out call is **601 credits on DeepSeek, 50 001 on Opus** — the ~83× spread falls out of provider pricing instead of being a number anyone maintains. A hand-written multiplier table could not express that a model's output costs 5× its input, nor price a model added tomorrow.

**The hard cap lives in `async_pre_call_hook`** — before the provider is touched, so a refusal costs nothing. Verified the three conditions litellm 1.88.1 requires to dispatch it (subclass attr, differs from base, registered as a `CustomLogger`) and that a raised `HTTPException` propagates (`except Exception as e: raise e`).

- `402` out of credits · `403` model above the grant's tier · master-key infra calls unmetered · **funding-lookup failure fails OPEN** (a metering blip must not become a total outage; the ledger counter is what catches the drift).

**Debit before record.** If only one can happen, the balance must move: a missing usage row can be replayed, a missing debit is free compute the cap was meant to stop. A post-call `402` means the gate leaked (cache-window race, or one call costing more than the whole balance) and gets its own `overspend_usd` metric — the exact measure of how leaky the cap is.

**Prepaid credits are grant rows, not a single balance**, because a grant is what carries expiry and class. Drained soonest-expiring-first (use-it-or-lose-it, so breakage means genuine non-use). `scope` is `platform` or `agent:<id>`, which is what gives a paid agent its own silo. `credit_class` exists so promotional credits can never be revenue-shareable — free credits plus a creator payout is a money printer.

**Ledger replay (1.6's other half).** Failed writes buffer and re-post on the next successful call — opportunistic, so recovery needs no timer or supervised task. Stops at the first failure and puts the row back at the front. Bounded deque: an unbounded retry queue turns a downstream outage into an OOM kill. **Stated limit:** in-memory, so a task replacement loses the backlog; durable replay needs SQS.

**Also landed:** `agent_id` + `model_tier` + `cached_tokens` on the usage row. Per-agent attribution is what prices a listing and spots a wasteful agent — "cost per account" cannot. Cached tokens are the biggest COGS lever (a cache read is ~10% of a normal input token, and agents carry huge stable prompts); both OpenAI and Anthropic report shapes are handled.

### Two bugs found by the tests

- **`expires_days: -1` minted an immortal grant.** `if days > 0` fell through to `expires_at = 0`, which means *never expires* — the most permissive outcome from the least trustworthy input. Now negative = already expired.
- **`custom_auth`'s bare `import metering` only worked under litellm's loader.** Added an explicit path fallback so the module is self-sufficient however it is loaded.

### Deliberate deviation from the plan

The plan specified accumulating micro-credits and rounding once per run. **Not built.** Per-call `ceil` over-charges by well under a cent per run at any sane dial setting, and the accumulator would need a dict keyed by `run_id` with no reliable end-of-run signal in the proxy to clean it up — an unbounded leak for a rounding error worth $0.0001. Revisit if the share of sub-credit calls grows materially.

### Verification

**590 unit tests pass** (569 before Phase 0 → +20 new metering tests → +1 new cached-token test). One existing test needed updating, not fixing: `_usage_fields` gained a fifth return value, so the assertion was stale rather than wrong.

**Two systems, one pipeline.** This plan covers observability (is it working, is it fast) *and* metering (what did it cost, who pays) together, because they are the same data. Building them separately guarantees the graphs and the invoices eventually disagree, which is the worst possible outcome.

**Payments are mocked; accounting is real.** No money moves in this plan. But every ledger entry is written as if it did, because money history cannot be backfilled. The only fake part is the payment rail, behind a single swappable interface.

---

## Deploy card — Phase 0 (you run these)

Phase 0 changes **all four images** (three install the telemetry library; the daemon also gained the correlation ID and tool timing) and adds two columns to the accounts DB. Deploy everything, not one service.

### 0 · Sanity check locally first

```powershell
& .venv\Scripts\python.exe -m pip install -e v2\monitoring   # if not already
cd v2 ; ..\.venv\Scripts\python.exe -m pytest tests/unit -q  # expect 569 passed
```

### 1 · Ship it

The deploy builds the images — there is no separate build step.

Locally (needs Docker Desktop + AWS login):

```powershell
cd v2\deploy\scripts
./push-images.ps1                      # builds all 4, pushes to ECR, rolls the ECS services
```

It builds each image and `throw`s before pushing if a build fails, so a broken Dockerfile
never reaches ECR — you find out in your own terminal.

Or via CI:

```
GitHub → Actions → Deploy → Run workflow → environment: dev, service: all
```

*Optional, CI route only:* Phase 0 widened the `model-proxy` and `accounts` build contexts to
`v2/`, so their `COPY` paths all changed. A runner-side path mistake costs a commit plus a few
minutes to discover; one local build surfaces it instantly. Skip this entirely if you are using
`push-images.ps1`, which already builds locally.

```powershell
cd v2 ; docker build -t probe -f model_proxy/Dockerfile .
docker run --rm probe python -c "import agentd_telemetry; print('ok')"
```

### 2 · Verify on AWS

```powershell
aws ecs describe-services --cluster agentd-dev `
  --services agentd-dev-web agentd-dev-daemon agentd-dev-accounts agentd-dev-model-proxy `
  --region ap-northeast-1 `
  --query 'services[].{name:serviceName,running:runningCount,desired:desiredCount}'
```

**Success looks like:** `running == desired` for all four.

Then in CloudWatch Logs (`/agentd/dev`):

- **`telemetry ENABLED`** in the model-proxy stream at boot — if it says `DISABLED`, the library did not make it into the image and everything below is silent.
- Log lines are now **JSON**, not `2026-… agentd INFO …`.
- Send one message through the app, then search the daemon stream for `run_total`, and the proxy stream for `ledger_write_total`.
- **The real proof:** grab a `run_id` from a daemon line and search all streams for it — it should appear in the daemon, the proxy, and the accounts stream for the same message.

### 3 · The accounts migration

`_init_db` adds `run_id`/`turn_id` via `PRAGMA table_info` + `ALTER TABLE`, so it is additive and idempotent. It runs on accounts' startup against the EFS-backed SQLite file. **No manual step** — but note there is still no backup of that DB (plan item 4.3), so this is the moment to be aware it is the only copy.

### Rollback

`push-images.ps1` tags every image with the git SHA *and* `:latest`. To roll back, re-run the deploy from the previous commit. There is no ECS deployment circuit breaker yet (plan item 4.2), so a bad image will **stay** rolled out until you do this.

---

## Part 1 — Why. The business use cases, in plain English

These are the actual jobs. Everything in Part 4 exists to serve one of them.

### BUC-1 · Know whether the product is working, right now

Someone has to be able to look at one screen and say "we're fine" or "we're not," in under two minutes, without reading logs.

Today that's impossible. We have container logs with 14-day retention and health checks that only prove a process is listening. A proxy with an expired provider key returns a cheerful `200 OK` on its health endpoint while every single user gets an error on every single message. We would find out from an angry email.

What we need to see: how many messages were sent in the last hour, how many got an answer, how many failed, and whether people can sign in. If messages-per-hour drops to zero, that's the fastest signal that something is badly wrong — faster than any error rate, because a broken system produces no traffic to have errors in.

### BUC-2 · Know whether it's fast, and whose fault it isn't

"Slow" is not actionable. We need to know *which part* is slow, because the fixes are completely different.

One message is not one operation. The user sends a message; the agent thinks; it calls a tool; the tool reads three files; the agent thinks again; it finally answers. That can be six calls to the AI provider and ten tool runs. A ninety-second answer might be ninety seconds of the provider being slow (their problem, we route around it) or ninety seconds of our own file-reading code being slow (our problem, we fix it). Without splitting those two numbers we cannot tell them apart, and we will waste days optimising the wrong thing.

Separately, the number users actually judge us on is **how long before words start appearing**, not how long the whole answer takes. An answer that starts in 800ms and finishes in 40 seconds feels fast. One that sits blank for 8 seconds and finishes in 20 feels broken. We must measure the first-word time specifically, not just total duration.

### BUC-3 · Never lose money silently

This is the most important use case in the document, because it is the only failure with **no symptom**.

Today, when the Model Proxy finishes a call it posts the cost to the Accounts ledger. If that post fails, the code logs a warning and moves on — deliberately, because a billing outage must never break a user's chat. That decision is correct. What is missing is that nobody counts the failures and nobody is told.

So: if Accounts is down for an hour, we serve traffic happily on our own provider keys, spending real money, and record none of it. Every customer is delighted. Every graph is green. The money is simply gone, and it is unrecoverable because nothing buffered it. We would discover it at month end, if at all.

We need a count of every billing write, a count of every failure, an alarm on the failures, and a buffer that replays them when the ledger comes back.

### BUC-4 · Never let one user or one agent bankrupt us

Every message spends real cash at Gemini or DeepSeek. That makes cost a live reliability signal, not a monthly finance report. It belongs on the same screen as uptime and gets woken up for at the same hour of the night.

Two shapes of disaster:

**Runaway.** An agent gets stuck in a loop — reads a file, thinks, reads it again — and burns tokens for hours. On desktop that's the user's own money on their own machine, so it self-corrects. On our infrastructure, with our keys, at 4am, with nobody watching, it is our money. Background agents (cron, heartbeat) are the worst case because there is no human in the loop at all; they deserve their own tighter alarm.

**Abuse.** One account spending $400 today while everyone else spends fifty cents is either a compromised token or someone deliberately farming us.

Right now nothing stops either. The `budget_usd` column exists on the accounts table, `_budget_view` computes whether an account is over, and the check is wired up in the daemon — but the check only runs when the daemon has accounts enabled, which is **false on every desktop**. And the proxy, which is the one place both desktop and web traffic must pass through, never checks budgets at all. So a signed-in desktop user can spend past their limit indefinitely. That is not a monitoring gap; it is a live hole.

### BUC-5 · Answer "what happened to me?" for one specific person

A user writes in: *"I sent a message yesterday afternoon and it just froze."*

Today we cannot answer. Her chat ran on her laptop, the AI call ran on our server, the billing row is in a third place, and nothing links them. We would guess.

Every message needs a tracking number, like a parcel, stamped on everything that message touches — every log line, every AI call, every billing row. Then support pastes one ID and gets her entire story: message received at 2:14:03, first AI call took 0.8 seconds, read three files taking 8 seconds, second AI call timed out at the provider, gave up, cost four cents, billed correctly. That is a real answer and a real fix.

The tracking number must be created at the **edge** — in the client — not deep inside the server. Today the run ID is generated 55 lines into the message handler, which means everything that fails before that line (validation, attachment saving, the "you already have a run in progress" rejection) produces no ID at all and is therefore invisible.

### BUC-6 · Charge correctly for a marketplace we don't control

Anyone can publish an agent. Agents choose their own model. A creator picking Claude Opus instead of DeepSeek changes our cost for the same conversation by roughly eighty times.

So a flat "$20 buys 1,000,000 tokens" promise is not survivable: a million tokens of DeepSeek costs us about a dollar, a million tokens of a premium model costs about thirty. The allowance has to be denominated in something that means the same thing regardless of model.

We also need to know cost **per agent**, not just per account — to price listings, to spot inefficient agents, to pay creators, and eventually to enforce that a free agent can't quietly run on our most expensive model.

### BUC-7 · Support free agents without funding an open bar

Some creators will publish for free. Free means free of *their* fee — it never means free of inference. Someone still pays the provider.

A free agent whose creator has no financial stake is the worst incentive shape in the system: the creator gains nothing by being efficient and loses nothing by choosing the most expensive model available. Restraint has to be structural, not requested.

We support this two ways: users spend from their own platform credit balance, or they bring their own API key (which costs us exactly nothing and is already built — it's Local mode). Either way there must be a hard cap and a cheap-models-only rule on anything we subsidise.

### BUC-8 · Stop deploys from destroying live conversations

On desktop this cannot happen — the daemon runs on the user's machine and we never restart it. In the hosted web version the daemon is our container, and every deploy stops it.

ECS stops a container by sending SIGTERM. Our daemon's entry point catches `KeyboardInterrupt` — that's Ctrl-C — and nothing else. So SIGTERM hits Python's default handler and the process dies instantly. Deploy at 2pm with forty people mid-conversation and all forty answers vanish mid-sentence. The client reconnects fine, but the run is gone, no error, just nothing. And we already paid for those tokens.

---

## Part 2 — Decisions already made

Locked in from the design discussion. Recorded here so we don't relitigate them mid-build.

| # | Decision | Reason |
|---|---|---|
| D1 | **Credits are derived from cost, not a per-model multiplier table** | `credits = ceil(provider_cost_usd × CREDITS_PER_USD)`. One config number. Handles input/output price asymmetry, cache discounts, and models that don't exist yet, with zero maintenance. A hand-written "Opus = 60×" table goes stale and can't express that Opus output is 5× its input. |
| D2 | **Hard cap. No overdraft, ever.** | The cap is what converts inference from an unbounded variable cost into a known number. That is what makes same-day creator payouts safe and margin per subscription deterministic. Netflix and Cursor can't do this; we can. Giving it up for user convenience destroys the model. |
| D3 | **Reserve inference at purchase, distribute the rest** | Money arrives on day 1; costs arrive over 30 days. Split three ways — processing, inference reserve, distributable margin — then creator and platform split only the third. Never distribute money already committed to a provider bill. |
| D4 | **Split margin, not gross** | Costs come off the top before the creator's cut. Nearly doubles platform net at ~9% cost to the creator, and makes the creator's payout shrink automatically when their agent is wasteful. Fixes the model-choice incentive with no policy needed. |
| D5 | **Allowances expire** | If credits never expire the reserve can never close and we hold an open-ended liability against money already paid out. |
| D6 | **Both funding models: platform credit pool (A) + BYOK (B)** | Paid agents bundle their own allowance; free agents draw from a shared pool; BYOK costs us nothing and is already shipped. |
| D7 | **BYOK is never metered** | Local mode calls providers directly with the user's key. The metering path must short-circuit entirely, not compute and discard. |
| D8 | **Payments mocked behind one interface** | `NullPaymentProvider` with `charge()` / `refund()` / `payout()`. Records intent, moves nothing. Every ledger entry real. |
| D9 | **Unbounded names go in logs; only bounded values become metrics** | With a marketplace, `tool_call_total{tool=<any name>}` is thousands of unique metrics and a five-figure monthly bill. Names in logs, top-20 rollup into metrics. |
| D10 | **Enforcement lives at the Model Proxy** | It is the only chokepoint both desktop Cloud and hosted web pass through, and the only one a user-controlled desktop cannot bypass or lie to. |

### Still open

| # | Question | Blocks |
|---|---|---|
| O1 | Creator/platform split — 80/20 or 70/30 of margin? | payout maths only, not schema |
| O2 | Who keeps unused reserve (breakage)? Platform, or split at period close? | ledger entry shape |
| O3 | Do unused credits roll over? If yes, capped at how much? | expiry logic, liability |
| O4 | Reserve at worst case or expected value? | start worst-case; switch once we have enough users to trust the average |
| O5 | Free tier size and whether it's per-user or per-agent | free-tier cost ceiling |

---

## Part 3 — Architecture

### One write path, three consumers

```
                    every model call
                           │
                           ▼
              ┌────────────────────────┐
              │  THE METERED EVENT     │   written once, at the Model Proxy
              │  run_id, agent_id,     │   (the unforgeable chokepoint)
              │  cost, credits, ...    │
              └───────────┬────────────┘
                          │
        ┌─────────────────┼─────────────────┐
        ▼                 ▼                 ▼
   OBSERVABILITY       BILLING          FRAUD
   graphs, alarms      debit, ledger    anomalies
```

Do not build a billing pipeline and a monitoring pipeline. One event, three readers.

### Where things run

| | Desktop Cloud | Hosted Web |
|---|---|---|
| Accounts | ON AWS | ON AWS |
| Model Proxy | ON AWS | ON AWS |
| **Daemon** | **user's PC** | ON AWS |
| Client | Electron | browser |

A program's output lands where the program runs. On AWS, printing goes to CloudWatch automatically via the `awslogs` driver already configured in `infra/modules/services.tf`. On the user's PC it lands on their disk and we can never read it — so the app must mail us a summary, which is what `ingest/` receives.

**Consequence:** everything measured at the Proxy or Accounts is complete in both modes with zero extra work. Only daemon-side signals need the mail path. That is why Phases 1–3 come before Phase 5.

### The monitoring module

```
v2/monitoring/
├── telemetry/     library. imported by daemon, accounts, proxy, ingest. NOT a service.
├── terraform/     alarms + dashboards + canary schedule. called from infra/environments/*.
├── ingest/        the ONE deployed service. receives desktop + browser reports.
├── synthetics/    the robot customer script.
└── runbooks/      one markdown file per alarm.
```

**Never build a monitoring service that applications call.** It puts a network hop in the hot path and loses data exactly when things break — the thing that reports incidents ends up on the far side of the outage. Applications print to stdout, which cannot fail.

---

## Part 4 — The build

### Phase 0 · Foundation

Nothing else works without these. Small, mechanical, do them together.

**0.1 · Telemetry library** — `v2/monitoring/telemetry/`
Four functions: `count()`, `timer()`, `log()`, `redact()`. Each prints one line of JSON in CloudWatch's embedded-metric format to stdout. CloudWatch extracts metrics from those lines automatically, so **no new infrastructure is required** — the pipe already exists. Package it installable so `model_proxy`'s deliberately isolated build context can depend on it without widening.

**0.2 · Correlation ID**
- Client generates `traceId = crypto.randomUUID()` and sends it with `chat.send` (`clients/ui/src/state/store.ts:1278`, alongside the existing `idempotencyKey`).
- Gateway adopts it as `run_id` instead of always generating (`gateway.py:3905`). Widen from `uuid4().hex[:12]` to full hex — 48 bits is fine for in-memory dedup but becomes a collision risk once it's a ledger join key queried across months.
- Loop derives `turn_id = f"{run_id}-{iterations}"`.
- Add `parent_run_id` for sub-agent runs and `trigger` (`chat | cron | heartbeat | channel | webhook | app | subagent`) since most runs won't originate from a chat box.
- Store `run_id` in a contextvar so every log line and metric picks it up automatically. **This is the load-bearing design choice** — the alternative is threading an ID through hundreds of function signatures.

**0.3 · Structured JSON logging**
Replace `logging.basicConfig` at `main.py:19` (and equivalents in accounts, proxy) with `telemetry.logs.setup()`. Formatter auto-attaches service, version, `run_id`, `account_id`, `agent_id`. Every field passes through an **allowlist** scrubber — anything not explicitly permitted is dropped, so message content, tool arguments, and file paths cannot leak even by accident.

**0.4 · Cross-process ID propagation**
Contextvars do not survive a subprocess boundary. MCP tools run as subprocesses; sandboxed plugins may too. Pass `run_id` / `turn_id` explicitly — env var for MCP, an argument in the sandbox call contract — or that work becomes orphaned from its run.

---

### Phase 1 · The metered event

The core of the whole plan. Serves BUC-3, BUC-4, BUC-6, BUC-7.

**1.1 · Canonical usage event**
One row per model call, written at the Proxy:

```
run_id · turn_id · parent_run_id · trigger
account_id · agent_id · creator_id · agent_version
model · model_tier · in_tokens · out_tokens · cached_tokens
provider_cost_usd · credits_charged
funding_source   platform_pool | agent_subscription | sponsored | byok
credit_class     paid | promotional
outcome
```

`credit_class` exists because free promotional credits plus creator revenue-share is a money printer — promotional credits must never be revenue-shareable.

**1.2 · Cost → credits**
`credits = ceil(provider_cost_usd × CREDITS_PER_USD)`. `custom_auth.py:109` already reads litellm's `response_cost`, and fallback constants for unpriceable models already exist at `custom_auth.py:49-50`. Accumulate micro-credits during a run and round **once** at `agent_end`, so the many tiny internal calls (session titles, embeddings, verification) aren't each rounded up.

**1.3 · Funding source resolution**
Before each call decide which pocket pays: agent subscription silo → platform pool → sponsored balance → BYOK. **BYOK short-circuits the entire metering path** (D7).

**1.4 · Balance and hard cap**
Debit in the Proxy's auth hook. Zero balance → refuse with a clear, specific reason. No overdraft (D2). This also closes the BUC-4 hole where `budget_usd` is currently enforced nowhere in desktop Cloud mode.

**1.5 · Model tier enforcement**
Free tier runs cheap models only, checked at the Proxy. `config.model_catalog` carries the tier; `agent.toml` declares the model; publishing validates that a declared price bracket matches the declared tier. A free agent asking for a premium model is refused, not silently subsidised.

**1.6 · Fix the silent ledger loss** *(BUC-3)*
`custom_auth.py:149` swallows failed usage posts, and the non-200 branch at line 147 only warns. Keep the fire-and-forget behaviour — a billing outage must not break a chat — but add a **counter**, an **alarm**, and a **retry buffer** (local disk or SQS) that drains on recovery. Once this row is also a billing record, losing it costs twice.

---

### Phase 2 · Accounting — real ledger, mocked money

**2.1 · Double-entry ledger** — every event produces balanced entries: credit liability, platform revenue, provider cost, creator accrual, reserve movement. Prepaid credits are a **liability** until consumed, not revenue on arrival.

**2.2 · Funding and subscription tables** — per-`(account, agent)` credit silos, platform pool, sponsored balances, `creator_price`, `model_tier`, `funding`, expiry dates.

**2.3 · `NullPaymentProvider`** — one interface, `charge()` / `refund()` / `payout()`, recording intent and moving nothing. Swapping in a real rail later touches one class.

**2.4 · Reserve and breakage** — reserve worst-case inference at purchase, distribute the remainder, measure actual consumption, record the leftover. Breakage will be one of the larger margin lines and cannot be guessed — only observed. Start reserving worst case; revisit once there are enough subscribers for averages to hold (O4).

**2.5 · Entitlements** — who may run which agent at which version; also the gate for "this agent requires BYOK."

---

### Phase 3 · Observability

**3.1 · Metrics, bounded dimensions only** (D9)
```
METRIC:  tool_call_total{source=builtin|plugin|mcp|agent_private, outcome=ok|error}
LOG:     {"event":"tool_call","tool":"<any name>","run_id":...,"ms":210}
```
Plus a nightly job promoting the top 20 tools / agents / models to real metrics.

**3.2 · The four clocks** *(BUC-2)*
- **Infra latency** — request leaving the machine to first chunk back. Measured at the Proxy, so complete in both modes. This is the only one we fully control.
- **Time to first visible output** — the number users judge us on.
- **Time to first answer token** — computed retroactively at `agent_end`, since which turn was final is only known once the loop ends.
- **Total duration.**
- Plus the **model-time vs tool-time split**, which is what makes "it's slow" actionable.

Hook points: `agent_start`/`agent_end` (`native.py:170`, `:345`), around `acompletion()` (`litellm.py:329`), around `tool.execute()` (`native.py:399` — the single choke point every tool in the system passes through), first `message_update` (`native.py:201`).

**3.3 · Business metrics**
`credits_sold` · `credits_consumed` · `credits_expired` (breakage %) · `cogs_usd` · `cogs_ratio` · `free_tier_cogs` · `reserve_balance` · `creator_accrued` · **`run_refused{reason}`** (cap hit / tier denied / no funding) — simultaneously a reliability signal and the best conversion signal we'll have.

**3.4 · Abuse signals** — tokens-per-run distribution per agent, delegation depth, per-account cost rate, promotional-credit usage, self-invocation (`creator_id == account_id`, which is wash trading and also corrupts marketplace rankings).

**3.5 · Alarms** — the five that page, each linking to its runbook:

| Alarm | Threshold | Why it pages |
|---|---|---|
| Ledger write failures | > 0 for 5 min | silent money loss, **no other symptom** |
| Token resolve p99 | > 1s for 5 min | sits in front of every model call, for every user |
| Proxy 5xx | > 1% for 5 min | users can't chat |
| Unhealthy targets | ≥ 1 for 2 min | no redundancy today |
| Cost per hour | above threshold | runaway or abuse |
| `cost_per_hour{trigger=cron}` | tighter threshold | unattended, nobody watching |

**3.6 · Dashboards** — *service health* (traffic, errors, latency) and *business health* (credits sold vs consumed, breakage, cost ratio, margin per subscription, top agents by spend). The second is the one we'll actually open daily.

**3.7 · Synthetic canary + deep health check** *(BUC-1)*
Robot customer every 5 minutes from outside the VPC: sign in → send a message → assert tokens streamed → assert a usage row appeared → **assert the balance was debited**. Health checks ask "is the server on"; this asks "does the product work." Add `/health/ready` on the Proxy verifying Accounts reachability and provider-key presence — kept **separate from liveness**, because a liveness check that depends on a downstream will restart the whole fleet on one blip.

---

### Phase 4 · Hosted safety

**4.1 · Graceful shutdown** *(BUC-8)* — SIGTERM handler in `main.py` (stop accepting new runs, drain in-flight), `stopTimeout` in the task definition, a `server_restarting` broadcast so the UI says "reconnecting" rather than freezing, and a `runs_killed_by_shutdown` counter that should always read zero.

**4.2 · Deployment circuit breaker** — `deployment_circuit_breaker { enable = true, rollback = true }` and `minimum_healthy_percent = 100` in `infra/modules/services.tf`. Three lines; today a bad image rolls out and stays.

**4.3 · Backups** — AWS Backup on EFS, daily, 30-day retention. The accounts DB now holds the money ledger and currently sits on unbacked SQLite over EFS. **Test a restore** — an untested backup is not a backup.

---

### Phase 5 · Client reach

**5.1 · Desktop uploader** — opt-in, default off, visible toggle, bounded offline buffer, **metadata only**. At `agent_end` mails roughly six numbers: `{run_id, total_ms, model_ms, tool_ms, turns, outcome}`. Also covers daemon start success (the v0.1.0 broken-runtime release shipped blind for exactly this reason), version adoption, and `platform.connect` success.

**5.2 · Ingest service** — `POST /v1/events`, strict schema allowlist, rate limited, prints to stdout. Deploys by adding **one entry** to the services map in `infra/modules/variables.tf`; the existing `for_each` in `services.tf` then builds task, target group, listener, and DNS. The only new running service in this plan.

**5.3 · Web RUM** — browser JS errors, page load, perceived first-token time, WebSocket reconnect rate → same ingest endpoint.

**5.4 · Plugin stdout capture** — the redaction allowlist protects calls *we* make; a third-party plugin doing `print(api_key)` in-process lands raw in CloudWatch. Capture, scrub, cap, and tag plugin output at the sandbox boundary rather than letting it merge into the service stream.

**5.5 · Runbooks** — one per alarm: what broke, what to check, how to fix, what the damage is.

---

## Part 5 — The ledger

### Phase 0 · Foundation — **COMPLETE** (built, not yet deployed)
- [x] 0.1 Telemetry library (`count`/`timer`/`log`/`redact`, EMF to stdout) — **+ cardinality guard, + live dev dashboard**
- [x] 0.2 Correlation ID: client `traceId` → `run_id` → `turn_id` → `parent_run_id` → `trigger`
- [x] 0.3 Structured JSON logging + allowlist redaction, all three services
- [x] 0.4 Cross-process ID propagation (`RunContext.run_id/turn_id`; MCP deliberately excluded — see notes)
- [x] 0.5 Package the library into all three images (build context widened to `v2/`)

### Phase 1 · The metered event — **COMPLETE** (built, not yet deployed)
- [x] 1.1 Canonical usage event schema + write path at the Proxy *(creator_id / agent_version / credit_class on the row wait for the catalog in 2.2)*
- [x] 1.2 Cost → credits conversion *(per-call `ceil`; run-level rounding deliberately not built — see notes)*
- [x] 1.3 Funding source resolution (+ BYOK never reaches the proxy at all)
- [x] 1.4 Balance + hard-cap enforcement — in the **pre-call** hook, before the provider is touched
- [x] 1.5 Model tier enforcement (config-driven tiers, `model_tier_max` per grant)
- [x] 1.6 **Fix silent ledger loss** — counter ✅ · retry buffer ✅ · alarm ⬜ *(alarm is 3.5)*

### Phase 2 · Accounting
- [ ] 2.1 Double-entry ledger
- [ ] 2.2 Funding + subscription tables, per-`(account, agent)` silos
- [ ] 2.3 `NullPaymentProvider` seam
- [ ] 2.4 Reserve + breakage accounting
- [ ] 2.5 Entitlements

### Phase 3 · Observability
- [ ] 3.1 Metrics with bounded dimensions + top-N rollup job
- [ ] 3.2 The four clocks + model/tool time split
- [ ] 3.3 Business metrics (incl. `run_refused{reason}`)
- [ ] 3.4 Abuse + fraud signals
- [ ] 3.5 Alarms (6) + runbook links
- [ ] 3.6 Dashboards: service health, business health
- [ ] 3.7 Synthetic canary + `/health/ready`

### Phase 4 · Hosted safety
- [ ] 4.1 SIGTERM graceful shutdown + drain + counter
- [ ] 4.2 Deployment circuit breaker + rollback
- [ ] 4.3 EFS backups + tested restore

### Phase 5 · Client reach
- [ ] 5.1 Desktop telemetry uploader (opt-in, metadata only)
- [ ] 5.2 Ingest service
- [ ] 5.3 Web RUM
- [ ] 5.4 Plugin stdout capture at the sandbox boundary
- [ ] 5.5 Runbooks

---

## Part 6 — Live defects this plan fixes

Not features. Bugs found during design review that exist in the working tree today.

| # | Defect | Where | Impact | Fixed by |
|---|---|---|---|---|
| DEF-1 | Failed billing writes swallowed, uncounted, unalarmed | `model_proxy/custom_auth.py:147-150` | Accounts down = money spent, nothing recorded, **no symptom** | 1.6 |
| DEF-2 | `budget_usd` enforced nowhere in desktop Cloud mode | `gateway.py:3874` requires `accounts.enabled()` (false on desktop); Proxy never checks | signed-in desktop users can spend past their limit indefinitely | 1.4 |
| DEF-3 | No SIGTERM handler | `main.py:32` catches `KeyboardInterrupt` only; ECS sends SIGTERM | every deploy kills every in-flight conversation, tokens already paid for | 4.1 |
| DEF-4 | No deployment rollback | `infra/modules/services.tf` | a bad image rolls out and stays | 4.2 |
| DEF-5 | Accounts DB unbacked | SQLite on EFS | losing it loses accounts **and** the money ledger | 4.3 |
| DEF-6 | Accounts is a hard dependency in the hot path | `custom_auth.py:85` per model call; single task, SQLite (cannot scale to 2 without corruption) | Accounts down = all users blocked within 60s | mitigation in 3.7; real fix is Postgres, out of scope |
| DEF-7 | No correlation between services | — | "my chat broke at 3pm" is unanswerable | 0.2 |

---

## Part 7 — Sequencing notes

**Phase 1 is load-bearing.** Items 1.1–1.4 deliver per-agent cost attribution, hard-capped spend, budget enforcement that does not currently exist, and the raw material for every graph and every invoice. Everything after Phase 1 is a consumer of that one event.

**Phases 1–3 cover desktop and web equally**, because they live in the Model Proxy and Accounts, which are on AWS in both modes. Maximum coverage for the least work. Phase 5 is the only desktop-specific part and the most expensive, which is why it is last.

**If only one phase ships, ship Phase 1.** It closes DEF-1 and DEF-2 and produces the five numbers that matter most: did it work, was it fast, what did it cost, did we bill it, is anyone unusual.

**Start with CloudWatch's embedded metric format, not a collector.** The `awslogs` driver already ships container output; EMF turns printed JSON into metrics with no new infrastructure and no new failure mode. Instrument against the OpenTelemetry API from day one so adding a collector later (for traces, or to leave CloudWatch) is a config change, not a rewrite of every call site.
