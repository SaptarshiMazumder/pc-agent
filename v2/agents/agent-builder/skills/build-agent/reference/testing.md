# Reference — end-to-end testing and the fix loop

How to prove an agent you built actually works: write scenarios from its requirements, run them
against a real daemon, read the diagnosis, and fix the agent one change at a time until it holds.
The engine lives in `agent_runtime.e2e`; you drive it with the `e2e_run` / `e2e_replay` /
`e2e_checks` tools. This page is the PROCEDURE — run it after the agent builds and before you
call it done.

The rule that shapes everything: **a run that "completes" is not a run that WORKS.** Green checks
mean the mechanics fired; they do not mean the output is good, and they do not mean a failure was
the agent's fault. Both caveats have their own step below.

---

## The loop

```
author scenarios  ->  provision inputs  ->  e2e_run  ->  read diagnosis
        ^                                                      |
        |                                                      v
   (rarely) new scenario  <---  fix ONE thing  <---  triage: agent bug? or environment?
                                      |
                                      v
                          judge the OUTPUT, not just the checks  ->  done
```

One change per iteration. Re-run. Confirm the specific finding cleared. Then the next. Never
batch fixes — a batch that half-works tells you nothing about which half.

---

## 1. Author scenarios from requirements

A scenario is data (`agents/<id>/e2e/<name>.json`): `agent_id`, a one-line `goal`, `settings`,
ordered `turns`, and `checks`. Write turns like a LAZY REAL USER — short, deferring detail,
providing input the way the app actually takes it — not like a QA script.

- **Turns** are the user's messages, in order. A turn is a string, or `{text, attachments}` for
  media the model should SEE (a "look at this" image), or `{text, reference_media}` for workflow
  INPUT that must reach a backend, not the model. Paths are relative to the scenario file.
- **Checks are the hard part — derive them from what the agent CLAIMS.** For each capability it
  advertises, a `tool_succeeded` / `produced_artifact`. For each "must not" (don't stall, don't
  hand work back to the user, don't re-ask for deferred input), the matching `no_*` check. For
  ordering it promises, `call_order`. Run `e2e_checks()` for the exact vocabulary and args —
  never invent a check name.
- **Encode the bug you're about to fix as a check.** Write it so it is RED on today's agent and
  turns GREEN only once the fix lands. That red→green flip is the proof the fix worked; a fix
  with no check behind it is a guess.

Cover the agent's real jobs and its stated limits — one scenario per distinct capability beats
one scenario that does everything and tells you nothing when it fails.

## 2. Provision the inputs — and ask when you must

Read the agent's `agent.toml` `[[settings]]` / `[[secrets]]` / `[[mcp]]`. Every declared input
the scenario needs falls into one of three buckets:

- **You can fill it** — a value you can synthesize or safely mock → pass it in
  `e2e_run(settings_overrides={...})`. That writes it to the test identity's settings for the
  run only; it never touches the user's real config.
- **Only the user has it** — a real instance URL, a paid provider key, an account login → **ASK
  THE USER.** "This agent tests against a live instance of its backend — paste its URL, or I'll
  run only the parts that don't need one." Never fabricate an external credential or endpoint.
- **Degrade** — if a needed resource is absent and the user can't supply it now, run the subset
  that doesn't need it and SAY in your summary what was skipped and why. A partial run with an
  honest gap beats a red run that looks like an agent bug.

Tests run under an ISOLATED session/identity — a test render must never land in the user's real
workspace or clutter their sessions. `e2e_run` handles the isolation; your job is to not point it
at real state.

## 3. Run, and read the diagnosis

`e2e_run(scenario_path, model?, settings_overrides?)` drives the scenario against this daemon and
returns a report: a transcript, a DIAGNOSIS (thrash / stall / holes / cost), and the scenario's
CHECKS with a verdict. `e2e_replay(trace_path)` re-diagnoses a saved trace offline — free, no
model, use it to iterate on the analysis without paying for another run.

## 4. Triage BEFORE you touch the agent — bug or environment?

This is the step that keeps the loop from destroying a working agent. Most real failures are NOT
the agent's fault. Classify every finding:

- **Environment** — a model call that 429'd, dropped, or returned a server error; an unreachable
  backend; a provider out of balance; a missing credential. **Do not edit the agent for these.**
  Retry once, or ask the user to fix the resource, or note-and-skip. Editing an agent in response
  to a flaky provider corrupts something that was working.
- **Agent** — thrash, stall, holes, or a bad output that the environment fully supported. These
  are yours to fix, in step 5.

If the report tags origin, trust the tag; if it's ambiguous, a failure with zero agent tool
activity around it is almost always environment.

## 5. Fix ONE agent-origin finding

Map the finding to the fix, change one thing, re-run, confirm THAT finding cleared:

- **thrash** (heavy reasoning with no action, plan re-emitted, same call repeated, one turn
  burning enormous reasoning) → tighten the instructions toward a deterministic sequence, and
  **bound any unbounded phase** — if the agent researches or explores without converging, add a
  stop rule ("once two sources agree, commit and act; do not gather more").
- **stall** (ended on a question before acting, re-asked for deferred input, never acted) →
  loosen: give defaults for everything the user didn't specify, and forbid blocking on input the
  user deferred.
- **holes** (a tool error it never recovered from, or it handed a step back to the user) → a
  missing tool/capability, or a bad fallback — add the tool, or fix the instruction that gave up.

Edit the AGENT (instructions, tools, `agent.toml`) — never hand-patch a workflow or output to
make a check pass. Bump `version` on every change so the daemon reinstalls it.

## 6. Judge the OUTPUT — checks green is not done

The single most important step, and the easiest to skip. A run can pass every check and still
produce garbage: `produced_artifact` is green because a file EXISTS, not because it is good. So
after the checks pass, judge the actual deliverable against the scenario's goal — look at the
image/video/document (or have a capable model look), and decide whether it is what the user
asked for. If you cannot judge it yourself, surface it to the user for the verdict. Only when the
checks are green AND the output is good is the agent done.

## Stop conditions

Stop the loop when: every agent-origin check is green and the output is judged good; or you are
blocked on user input (say exactly what you need); or the same finding survives two or three
fix attempts (surface it — it may be a design problem, not an instruction tweak).
