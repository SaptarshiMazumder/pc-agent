# Agent e2e harness

Drive a real agent against a real backend, capture everything it does, and **diagnose** it. The
output is not a green/red gate — it is a read on *where the agent thrashed* (make it more
deterministic) and *where it stalled or was too strict* (make it more flexible), so instruction
changes are driven by observed behaviour instead of guesses.

It is deliberately agent-agnostic. Nothing in `runner`/`trace`/`signals`/`checks`/`report`
mentions any specific agent or tool — a ComfyUI install and a Gmail send fold into the same
shape. That is the point: **this is the groundwork for an Agent Builder feature** that runs e2e
scenarios against any agent it builds and learns from the result.

## The pieces (one module, one concern)

| module | concern |
|---|---|
| `scenario.py` | the declaration a run executes — agent, settings, user turns, checks. Pure data. |
| `trace.py` | what a run captured (turns → tool calls / text / plans / cost), read from JSONL. Dumb facts. |
| `signals.py` | the diagnosis engine — pure `Trace → findings`: **thrash / stall / holes / cost**. |
| `checks.py` | a scenario's named pass/fail expectations, over the same trace. |
| `report.py` | render transcript + diagnosis + checks as portable text. |
| `runner.py` | drive it **live** (ws client to a daemon) or **replay** a saved trace; same analysis. |

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
- **cost**: turns, tool calls, reasoning volume — the numbers a model-vs-model diff reads on.

## Run it

**Live** (true end-to-end — needs a running daemon with a model, and `pip install websockets`):

```
python -m tests.e2e.runner \
  --scenario agents/comfy-artchitect/e2e/influencer-video-refs-later.json \
  --daemon wss://<daemon-host> --token <account-session-token> --model deepseek-v4-pro
```

Put the live ComfyUI URL in the scenario's `settings.COMFYUI_URL` first (the vast Copy-URL,
token and all). The runner applies settings, sends each user turn, waits for each run to end,
writes `<scenario>.trace.jsonl`, and prints the diagnosis. Exit code is non-zero if any check
fails, so CI or a wrapper can gate on it.

**Replay** (re-diagnose a saved trace — no daemon, no model, stdlib only):

```
python -m tests.e2e.runner --replay path/to.trace.jsonl \
  --scenario agents/comfy-artchitect/e2e/influencer-video-refs-later.json
```

## How it drives the tuning

1. Run a scenario → read the diagnosis.
2. Thrash findings → tighten the instructions toward a deterministic sequence.
   Stall findings → loosen them: default and proceed instead of asking.
   Holes → a missing capability or a bad fallback.
3. Change the agent, re-run, confirm the findings clear. The **flip from red to green is the
   proof** — not a vibe.
4. Diff two models on one scenario (two traces) to see *where* a weaker model diverges — the
   concrete answer to "why does this model loop and that one doesn't".

## The Agent Builder path (what this is groundwork for)

The Builder feature is a third transport behind the same analysis: author scenarios per built
agent, run them against the build's own daemon, show the author the diagnosis, and suggest the
instruction edits the findings imply. `scenario` / `trace` / `signals` / `checks` / `report` are
reused verbatim; only "which daemon, launched how" is new. Nothing here needs to change for that
— which is why it was built agent-agnostic from the first line.
