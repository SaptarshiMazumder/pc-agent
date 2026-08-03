# `v2/monitoring/` — observability and metering

Plan: [`../planning/platform/sre-metering-plan.md`](../planning/platform/sre-metering-plan.md) ·
Diagrams: [`../planning/platform/diagrams/sre-observability.puml`](../planning/platform/diagrams/sre-observability.puml)

## The contract

Three layers, and only one of them is a running service:

| Layer | What | Deployed? |
|---|---|---|
| **Instrumentation** | `agentd_telemetry/` — a library the services import | ❌ no |
| **Collection** | the `awslogs` driver, already configured in `infra/modules/services.tf` | already running |
| **Storage / graphs / alarms** | CloudWatch | AWS runs it |

**Never build a monitoring service that applications call.** It puts a network hop in the
request path and loses data exactly when things break — the thing that reports the outage ends
up on the far side of it. Services `print()`; printing cannot fail.

The one exception is [`ingest/`](#not-built-yet) (plan 5.2): the desktop daemon runs on the
user's machine, so its output lands on their disk where we can never read it. That needs a
mailbox. Everything running on AWS just prints.

## How it works

`count()` / `timing()` / `money()` write one JSON line to stdout in CloudWatch's Embedded
Metric Format. CloudWatch parses those lines and extracts real metrics — so this is a complete
pipeline with **no new infrastructure**.

```python
from agentd_telemetry import count, timer, money, scope, setup_logging

setup_logging("model-proxy")

with scope(run_id=rid, account_id=acct):     # rides a contextvar; auto-attached to everything
    with timer("model_call_ms", outcome="ok"):
        ...
    money("model_cost_usd", 0.0043, _props={"model": "deepseek/deepseek-chat"})
    count("ledger_write_total", outcome="ok")
```

### The one rule that costs money if you break it

Keyword arguments become **dimensions** — indexed by CloudWatch and **billed per unique
combination**. Bounded vocabularies only (`outcome=ok|fail`, `direction=in|out`).

Unbounded names — tool names, agent IDs, model names — go in `_props`, which are plain JSON
properties: **free**, and still queryable with Logs Insights.

```python
count("tool_call_total", source="plugin", _props={"tool": name})   # correct
count("tool_call_total", tool=name)                                # a bill
```

With a public marketplace, the second line is one billed custom metric per tool ever published.
The guard is in code, not in this README: once a dimension key exceeds 50 distinct values it
collapses to `__high_cardinality__` and warns once. The graph degrades; the bill does not.

### Redaction

`redact.py` is an **allowlist**. A field nobody explicitly permitted never leaves the process.
Message text, tool arguments, tool output, file paths, and keys can't reach CloudWatch even by
accident. Extend `ALLOWED` deliberately, one field at a time.

## Watching it live, locally

The services print the same lines locally that CloudWatch reads in production. Point them at a
file and tail it with the dev dashboard:

```powershell
# terminal 1 — dev.py copies its environment into every child, so this is all it takes
$env:AGENTD_TELEMETRY_FILE = "$PWD\v2\.telemetry.jsonl"
python v2/deploy/dev.py --model-proxy

# terminal 2
python v2/monitoring/dev_dashboard.py
```

```
agentd — live metrics    last 5 min · 263 lines

MONEY
  spend                 $  0.4558
  UNBILLED SPEND        $  0.0633   <-- money out, nothing recorded
  ledger writes         33 ok  7 fail   ████████████████████████

TRAFFIC
  model calls           40
  tokens                94,819 in   20,666 out

AUTH
  outcomes              40 ok  2 refused
  resolve latency       p50 61ms   p95 85ms   (blocks every model call)
```

`--model-proxy` matters: the proxy is where the instrumented code lives, and it is off by
default in `dev.py`.

## Layout

```
monitoring/
├── agentd_telemetry/     the library — imported, never deployed
│   ├── emf.py            the only place a line leaves the process (stdout)
│   ├── metrics.py        count / timing / timer / gauge / money + cardinality guard
│   ├── context.py        run_id etc. on a contextvar, not in 200 signatures
│   ├── redact.py         allowlist — the gate content cannot pass
│   └── logs.py           JSON logging with the same context and the same allowlist
├── dev_dashboard.py      local live view (dev tool, not deployed, no dependencies)
└── pyproject.toml        installable: `pip install -e v2/monitoring`
```

### Not built yet

`terraform/` (alarms + dashboards) · `synthetics/` (the robot customer) · `ingest/` (desktop and
browser reports) · `runbooks/`. See the plan.

## Install

```powershell
& .venv\Scripts\python.exe -m pip install -e v2\monitoring
```

Zero dependencies, deliberately — this is imported by every service including the
pinned-litellm `model_proxy` image, where an extra dependency is an extra risk.

## Known gap: the Docker image

`model_proxy/Dockerfile` has an isolated build context (`v2/model_proxy/`), so it cannot
`COPY ../monitoring`. Until that's resolved, `custom_auth.py` imports the library through a
`try/except` and degrades to no-ops, logging `telemetry DISABLED` at boot. **Local dev is fully
instrumented; the container is not yet.** Fix is either widening the compose context to `v2/`
or vendoring a built wheel — a deliberate decision, not an oversight.
