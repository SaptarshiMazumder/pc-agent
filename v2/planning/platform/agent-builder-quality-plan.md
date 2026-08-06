# Agent Builder — output quality plan (A–F)

**Status:** A done. C done (C1/C2/C3). **F is next** (user proposal, supersedes B). D pending.
B folded into F2. E dropped as overfitted — see below.
**Started:** 2026-08-06. Owner: the agent-builder workstream (phases 1–7 built the machinery;
this plan is about the QUALITY of what it produces).

---

## The problem

Agent Builder works — it scaffolds, validates, reloads, packages. But the agents it produces
are poor. The trigger for this plan was `agents/inbox-triage/`, built 2026-08-06 00:00–00:15.

Crucially: it was built by **`gemini/gemini-3.1-pro-preview`, zero fallbacks**. A capable model,
undegraded. So this is NOT "use a better model" — a strong model produced weak output because of
what it knew and what it could check.

### What was actually wrong with that agent

| symptom | cause |
|---|---|
| "Run triage now" button did nothing | `ui/app.js` read `payload.type`; the event type is nested at `payload.event.type`, so every branch was dead |
| listened for `message_delta` | **no such event.** Real: `message_update` with `kind: "text_delta"` |
| `agentd.resultText(payload.result)` | no `result` field on a chat.event payload |
| hourly heartbeat that asks an LLM to read a clock | should have been `cron(daily='08:00')` |
| no `AGENTS.md` at all | the skill called it "optional" |
| flat, unstyled UI | nothing tells it what good looks like |

**The model was obeying.** `build-agent/SKILL.md` literally listed `message_delta` as a valid
event (I wrote that line in phase 1 from assumption, never checked the source), never showed the
payload shape, and mentioned `heartbeat` 4× against `cron` 1×.

---

## Root causes, ranked by leverage

1. **The spec lied.** The only protocol description the builder ever reads was part guesswork,
   part stale. → **A**
2. **No feedback loop.** It writes code and never runs it. It cannot discover it was wrong. → **C**
3. **Nothing to copy.** The skill describes a UI in prose; three working ones sit in the repo
   unreferenced. → **B**
4. **No design guidance.** → **D**
5. **Thin agent-design doctrine.** → **E**

### The insight that reframed everything

Agent Builder is the same mechanism as any coding agent: LLM returns data → orchestrator calls
tools → repeat. It is not less capable by construction. **It was handed a worse toolbox.**

```
agent-builder CAN call:  edit, find, ls, read, skill_workshop, write
                         + create_agent, create_tool, validate_agent, reload_agent, package_agent

EXISTS in the runtime, NOT allowed to it:
  update_plan   ← the checklist tool (renders live in the client)
  exec          ← running anything at all
  verify_answer ← self-review
```

That allow-list was written in phase 5 and justified as *"building an agent is a filesystem job."*
That was wrong: it optimised for least privilege and removed the ability to plan and to verify —
the two things that most distinguish good agent output from bad.

Note it already has `read`/`ls`/`find`: **the true protocol has always been one `read` away**
(`clients/sdk-js/src/protocol.ts`). Nothing ever told it to look.

---

## The workstreams

### A — the spec must not lie ✅ DONE

- Rewrote the `ui/` section of `agents/agent-builder/skills/build-agent/SKILL.md`: real event
  table with payload fields, the **nested payload** stated where it can't be missed, corrected
  app-callable method list, `turn_end` vs `agent_end`, `model_fallback`.
- Canonical lists live in tagged fences — ` ```text agentd:events ` and ` ```text agentd:app-methods `.
- `tests/unit/test_build_agent_skill_is_true.py` checks them against the runtime **both ways**:
  a documented event that nobody emits fails; a new user-facing event nobody documented fails.
  Verified by reintroducing each original lie and watching it fail.

### C — close the feedback loop ✅ DONE

**C1 — give it the tools.** Add to `agents/agent-builder/agent.toml` `[tools] allow`:
`update_plan`, `exec`, `verify_answer`.

*On `exec` risk:* not a new risk class. This agent already has unsandboxed `write` (any path) and
`create_tool`, which writes Python **and hot-loads it into the running process**. It can already
execute arbitrary code; it just can't check its own work first.

**C3 — the instructions** (into agent-builder's `AGENTS.md`, since tools without instructions get
ignored):
1. Plan first — `update_plan` before writing, tick items as they complete.
2. Verify by running — `node --check` generated JS, import generated Python. Don't just look at it.
3. When unsure, read the source — `clients/sdk-js/src/protocol.ts` for event shapes, an existing
   `agents/*/ui/app.js` for a working example. Don't guess.
4. Not done until `validate_agent` is clean **and** you have run what you wrote.

**C2 — mechanical backstop.** Teach `validate_agent` to read the generated `ui/app.js`:
`payload.type` misuse, event names that don't exist, SDK methods that don't exist, daemon methods
an app connection can't call. New `agent_authoring/domain/ui_rules.py`.

*Done in that order.* Outcome: agent-builder went 11 -> 14 tools; `validate_agent` now reads
`ui/*.js` and caught both original defects in `inbox-triage` — and found the SAME dead branch
(`message_delta`) in the repo's own hand-written `expense-summarizer` and `figure-creator` UIs,
which matters for B: those were going to be the reference examples.

Scoping the checks took two passes. Flagging every `.type === '...'` fired on working code
(content blocks, DOM nodes); the rule now only follows identifiers that genuinely hold an event.
Matching only inline arrow handlers then missed the named-callback form both hand-written agents
use. A rule that cries wolf gets switched off, so silence on working code is a requirement, not
a nicety.

### B — a reference implementation (FOLDED INTO F2)

**Constraint discovered while doing C3, and it reshapes B entirely.** Only `agents/main/skills`
and `agents/agent-builder` ship (`scripts/hatch_build.py`). On a user's machine there is no
`weather`, no `figure-creator`, and no `clients/sdk-js` either. C3's first draft pointed the
builder at both — it would have read a missing file, got "not found", and guessed anyway: the
exact behaviour the rule exists to prevent. Fixed, with a test asserting every path it is told
to read actually ships.

So a reference CANNOT be "go look at another agent". It must be something that ships with
agent-builder, is ours, and is kept correct. Two candidates, both already true:

- **agent-builder's own `ui/`** — ships, validates clean, written against the real protocol.
  Already what C3 points at.
- **a purpose-built template** under `skills/build-agent/reference/`, if a stripped-down
  starting point beats a full app as a thing to copy.

B is therefore: decide between those, and make the skill's `ui/` section point at it as the
worked example rather than describing one in prose.

The dev-checkout agents (`expense-summarizer`, `figure-creator`) still carry the `message_delta`
dead branch. Worth fixing so the repo is honest and C2 goes green everywhere, but they are NOT
the reference and never were.

### D — design guidance

Layout, spacing, states (empty / loading / error). Stops default-blue-button output.

### E — DROPPED as originally specified

Originally: a rulebook of agent-design decisions (cron vs heartbeat, always write AGENTS.md,
state/dedupe, notify-on-signal).

**Rejected by the user, correctly**: it was derived from a sample of ONE artifact. Encoding fixes
for the mistakes we happened to observe produces a builder that is excellent at email-triage
agents and mediocre elsewhere. It is pre-answering, not teaching.

**The salvageable general core:** the builder should *discover* the runtime's capabilities rather
than be told a hand-written list — `capabilities.list` and `plugins.catalog` already exist and it
never consults them. That generalises; "use cron for 8am" does not. Reconsider after C.

### F — establish the SUBJECT of the conversation, and start from a template (user's proposal)

Two halves, proposed 2026-08-06 after test-driving C.

**F1 — a new chat begins by choosing what it is about.** Today every conversation starts from
nothing and the agent under discussion is inferred from prose, which is how "build me a
linkedin job finder" three times produced one agent and a clobbering argument. Instead, a new
chat asks up front:

- **work on an existing agent** (offered only when the user has any), or
- **create a new one**

Picking an existing agent scopes the session to it: the inspector switches to that agent, and
the chat is seeded with the fact that `agents/<id>/` is the subject — the way opening a folder
as a VS Code workspace gives a coding agent its context. The model then reads and contextualises
itself with the tools it already has (`read`/`ls`/`find`); the difference is that it is pointed
at the right directory instead of guessing.

*Most of the machinery exists*: the inspector already has an agent picker, `CROSS_AGENT_READS`
already lets this window read other agents, and the model already has the file tools. What is
missing is the onboarding step and the seeded context.

**F2 — start a UI from a template, not from a blank file.** The user picks from a small set of
templates; begin with ONE and add more once the shape is proven. This SUPERSEDES B as
originally written ("point the builder at an example to read") — a pickable starting artifact
is a stronger form of the same idea, so B folds into F2.

**Relationship to D:** D is visual craft (layout, spacing, hierarchy, the empty/loading/error
states everyone forgets). F2 is the starting point. A template embodies D's rules rather than
describing them, which makes it the stronger mechanism by the ranking below — but D still
matters for everything the template does not cover, and for judging when to depart from it.

**Note:** nothing currently covers FEATURE design (what an agent should do, how it should be
shaped). That was E's territory and E was dropped for overfitting. If it comes back, it should
be discovery-driven — see E's salvageable core.

---

## Principles established (apply to all remaining work)

- **Errors must surface.** No defensive code that hides failure behind a fallback. A genuine
  alternate path is fine; the caller must still be told it was taken.
- **Never overfit to one observed artifact.** If a rule came from a single example, it is probably
  pre-answering rather than teaching.
- **Prose in a skill is the weakest mechanism.** Ranked: validator rule (absolute) > instruction in
  the generated agent's own AGENTS.md, present every turn (strong) > prose in the builder's skill,
  read once at build time (weak).
- **Generate the spec from the source.** Anything hand-written about the protocol will drift; A's
  drift test is the pattern to copy.

## How to verify progress

Build an agent from a SHORT prompt (users don't write specs) and check:
UI actually renders on click · `validate_agent` clean · `AGENTS.md` present · scheduling mechanism
appropriate · plan visible while building · it ran what it wrote before declaring done.
