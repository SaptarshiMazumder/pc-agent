"""Agent e2e harness — drive a real agent against a real backend, capture the trace, diagnose it.

The harness is an INSTRUMENT, not a pass/fail gate: its output is a diagnosis that says where an
agent thrashed (make it more deterministic) and where it stalled or was too strict (make it more
flexible), so instruction changes are driven by observed behaviour instead of guesses. It is
agent-agnostic on purpose — the same pieces serve a CLI run from a checkout, the Agent Builder's
own `e2e_run` tool (plugins/e2e), and offline re-analysis of a saved trace.

It ships INSIDE `agent_runtime` (not tests/) for two reasons: the wheel stages only this package,
so anything under tests/ cannot reach an install; and `tests/e2e` is a pytest TIER with its own
documented meaning (booted-daemon smoke) that this harness is not part of.

Layers (each a module, single concern):
  scenario     — the declaration a run executes (agent, settings, user turns, checks). Pure data.
  trace        — what a run captured, and how it's read back from JSONL. Dumb facts.
  signals      — pure Trace→findings diagnosis: thrash / stall / holes / errors / cost, each
                 finding tagged with its likely ORIGIN (agent vs environment).
  checks       — a scenario's named pass/fail expectations, over the same trace.
  report       — render transcript + diagnosis + triage + checks as readable text.
  live_driver  — the transport-agnostic drive loop, plus the ws transport. A transport is
                 anything with call/send/events; the in-process one lives in presentation.
  runner       — the CLI: drive live over ws, or replay a saved trace.
"""
