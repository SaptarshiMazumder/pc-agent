"""Agent e2e harness — drive a real agent against a real backend, capture the trace, diagnose it.

The harness is an INSTRUMENT, not a pass/fail gate: its output is a diagnosis that says where an
agent thrashed (make it more deterministic) and where it stalled or was too strict (make it more
flexible), so instruction changes are driven by observed behaviour instead of guesses. It is
built agent-agnostic on purpose — the same five pieces are the groundwork for an Agent Builder
feature that runs e2e scenarios against any agent it builds and learns from the result.

Layers (each a module, single concern):
  scenario  — the declaration a run executes (agent, settings, user turns, checks). Pure data.
  trace     — what a run captured, and how it's read back from JSONL. Dumb facts.
  signals   — pure Trace→findings diagnosis: thrash / stall / holes / cost.
  checks    — a scenario's named pass/fail expectations, over the same trace.
  report    — render transcript + diagnosis + checks as readable text.
  runner    — drive it live (ws client to a daemon) or replay a saved trace; same analysis.
"""
