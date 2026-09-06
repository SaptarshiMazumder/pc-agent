# Agent e2e harness

Drive a real agent against a real backend, capture everything it does, and **diagnose** it. The
output is not a green/red gate — it is a read on *where the agent thrashed* (make it more
deterministic), *where it stalled or was too strict* (make it more flexible), and **whose fault
each failure was** (agent vs environment), so instruction changes are driven by observed
behaviour instead of guesses.

It is deliberately agent-agnostic: nothing here mentions any specific agent or tool — a ComfyUI
install and a Gmail send fold into the same shape. It lives inside `agent_runtime` (not `tests/`)
because the wheel ships only this package, and because `tests/e2e` is a pytest tier (booted-daemon
smoke) with its own meaning.

## The pieces (one module, one concern)

| module | concern |
|---|---|
| `scenario.py` | the declaration a run executes — agent, settings, user turns, checks. Pure data. |
| `trace.py` | what a run captured (turns → tool calls / text / plans / cost / end reason), read from JSONL. Dumb facts. |
| `signals.py` | the diagnosis engine — pure `Trace → findings`: **thrash / stall / holes / errors / cost**, each finding tagged `agent` or `environment`. |
| `checks.py` | a scenario's named pass/fail expectations, over the same trace (plus `vocabulary()`, the authorable list). |
| `report.py` | render transcript + diagnosis + **triage** + checks as portable text. |
| `live_driver.py` | the transport-agnostic drive loop, and the ws transport. |
| `runner.py` | the CLI — drive live over ws, or replay a saved trace. |

The split that matters: **`trace` records, `signals` interprets.** A run is expensive (real
model, real GPU); once captured you re-diagnose offline from the saved `trace.jsonl` as the
signal engine evolves — never re-running the agent to change what a finding means.

## What it diagnoses

- **thrash** (determinism gap): heavy reasoning with no action; the same tool call repeated with
  identical args; `update_plan` re-emitted instead of followed; a wedged (truncated) run.
- **stall** (flexibility gap): a turn that ends on a question before any build tool ran;
  re-asking for input the user explicitly deferred; a whole run that never acted.
- **holes**: tool errors left unrecovered; give-up language ("you'll need to install…") with no
  tool attempted.
- **errors**: runs that ended in error, in the runtime's own words — the raw material of triage.
- **cost**: turns, tool calls, reasoning volume — the numbers a model-vs-model diff reads on.

**Triage** is the part an agentic fix loop must obey: every problem/warn finding carries an
origin. `agent` → fix instructions/tools, one change per iteration. `environment` (a 429, a
dropped connection, an unreachable backend, a missing credential — the majority of real live-run
failures) → retry, fix the resource, or ask the user; **never edit the agent for these**.

## Run it

**Live** (true end-to-end — needs a running daemon with a model, and `pip install websockets`):

```
python -m agent_runtime.e2e.runner \
  --scenario agents/comfy-artchitect/e2e/civitai-style-i2v.json \
  --daemon wss://<daemon-host> --token <account-session-token> \
  --model <model-id> --set COMFYUI_URL=<live-url>
```

Credentials ride `--set` so the committed scenario never holds them. The runner applies settings,
sends each user turn (reference media via `workspace.upload` first — the product's own path),
waits for each run to end, writes `<scenario>.trace.jsonl`, and prints the report. Exit code is
non-zero if any check fails, so CI or a wrapper can gate on it.

**Replay** (re-diagnose a saved trace — no daemon, no model, stdlib only):

```
python -m agent_runtime.e2e.runner --replay path/to.trace.jsonl \
  --scenario agents/comfy-artchitect/e2e/civitai-style-i2v.json
```

## How it drives the tuning

The full procedure — authoring scenarios from requirements, provisioning credentials with the
three-bucket rule, the triage discipline, judging the OUTPUT after checks pass, stop conditions —
is written once, in the Agent Builder's skill:
`agents/agent-builder/skills/build-agent/reference/testing.md`. Follow that whether the loop is
run by the builder or by hand.

## The Agent Builder path (implemented)

`plugins/e2e/` wraps this engine as three first-party tools — `e2e_run`, `e2e_replay`,
`e2e_checks` — allowed to `agent-builder` and `cloud-agent-builder`. `e2e_run` drives the same
loop through the **in-process gateway client** (`presentation/in_process_gateway_client.py`):
same `config.set` / `workspace.upload` / `chat.send` methods, no socket — which is what makes it
work on a hosted daemon, where a socket dial-back's `?act_as=` does not authorise. `scenario` /
`trace` / `signals` / `checks` / `report` are reused verbatim, exactly as this README always
promised.
