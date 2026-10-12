# Per-User Runtime Plan — from soft isolation to a real boundary

> **Status:** Not started · **Goal:** every user's runtime works independently, the way hosted
> assistants actually do it · **Unlocks:** lifting `requires_local` on agent-builder, `exec` on the
> web, an enterprise tier worth selling
> **Working doc** — check boxes off as we go. Each rung is independently shippable and each one is
> worth shipping on its own.

---

## The problem in one paragraph

One daemon process on ECS serves every user. Isolation happens *inside* that process: a contextvar
says who the turn belongs to, and path comparisons keep tools inside that account's subtree. That is
a **Python-level boundary**. It holds exactly as long as every code path remembers to consult it,
and it does not hold at all against code the user can cause to run. The codebase already says so in
two places — `exec` refuses outright on a fenced run
([`plugins/shell/exec_tool.py:152`](../../plugins/shell/exec_tool.py)), and `agent-builder` carries
`requires_local = true` with the note *"Lifting this needs a runtime per user, not a config change."*

## The target shape

Not "a VM per user" — that is not what hosted assistants do and it is not affordable. Three planes:

| Plane | What it is | Shared? | Unit |
|---|---|---|---|
| **Control** | auth, routing, transcripts, metering, registry | yes, stateless, horizontally scaled | the fleet |
| **Data** | accounts, chats, memory, schedules | yes, one database | a row, scoped by account/org id |
| **Execution** | the agent loop and its tools | **no** | one box per *run*, torn down on idle |

The unit of isolation is the **session**, not the user. A user who is not typing costs nothing.

---

## Rung 0 — findings (what is already true)

Done, verified in code:

- ✅ Accounts data is in Postgres (Neon), not SQLite on EFS.
- ✅ Per-account state roots — `<state_dir>/accounts/<acct>/agents/<id>/`
  ([`user_state.py`](../../agent_runtime/infrastructure/user_state.py)).
- ✅ Per-account config overlay, allowlisted on read *and* write
  ([`account_config.py`](../../agent_runtime/infrastructure/account_config.py)).
- ✅ Tenant fs fence — `read_roots` / `write_clamp` on the `RunContext`, checked at one choke point
  ([`write_scope.py`](../../agent_runtime/application/write_scope.py)).
- ✅ Semantic memory partitioned by `<account>::<agent>`
  ([`accounts.py:239`](../../agent_runtime/infrastructure/accounts.py)).
- ✅ `SubprocessPluginSandbox` — a real process boundary per untrusted tool call, with an inverted
  model-call broker so the child never holds a credential.
- ✅ The seams for everything below already exist: `PluginSandbox` / `CapabilityResolver` protocols,
  `CapabilityGrant` (fs_paths, net_allowlist, cpu_ms, mem_mb, timeout_s, secrets, models), and
  `RunContext.run_id` carried explicitly *because* "contextvars do not survive a process boundary."

**Gaps found while writing this plan** — these are the Rung 1 work:

| Store | Key | Scoped? | Consequence |
|---|---|---|---|
| `memory.sqlite` | `<acct>::<agent>` | ✅ | — |
| `autonomy.sqlite` → `tasks` | `agent_id` + `account_id` | ✅ | — |
| `autonomy.sqlite` → `notifications` | `agent_id` only | ❌ | **cross-tenant read**: `notifications(agent_id="main")` returns every account's rows |
| `autonomy.sqlite` → `commitments` | `agent_id` only | ❌ | **cross-tenant read**, same shape |
| `autonomy.sqlite` → `goals` | `session_key` | ⚠️ | no account column; session keys are unguessable, so low risk, not zero |
| `autonomy.sqlite` → `runs` | `task_id` (uuid) | ⚠️ | reached only via a task the caller owns; low risk |
| `resources.sqlite` | `(agent_id, rel_path)` | ❌ | **mutual cache deletion** — every user's `reconcile()` deletes every other user's rows for the same agent id, and every account has `main` |

`notifications` and `commitments` are the sharp ones: user A can read user B's notification text
today, on the deployed dev daemon.

---

## Rung 1 — finish the data boundary

**Goal:** no shared store is reachable across accounts. No infra change.
**Size:** ~1 week. **Ships on its own:** yes — this is a straight bug fix.

- [ ] **1.1 `notifications` + `commitments` get `account_id`.** Follow the `tasks` precedent exactly:
      add the column via the existing migration list in
      [`tasks/sqlite_store.py`](../../agent_runtime/infrastructure/tasks/sqlite_store.py) (~line 157),
      write `accounts.account_id()` on insert, and add the `WHERE account_id=?` clause to the
      readers at lines 387 and 432. Empty string = desktop, unscoped, unchanged.
- [ ] **1.2 `goals` + `runs` get the same column** for consistency, even though the current key makes
      them hard to reach. Defence in depth costs one column each.
- [ ] **1.3 `resources.sqlite` partitioned.** Fold the account into the key the way memory already
      does — `<account>::<agent>` — applied where `ResourceManager` hands `agent_id` to the store
      ([`resources/manager.py`](../../agent_runtime/infrastructure/resources/manager.py)). No schema
      change, no caller change. Fixes the mutual-deletion bug as a side effect.
- [ ] **1.4 A test that proves the rule.** One test per store: two accounts, same agent id, each
      sees only its own rows. These are the regression tests for every rung after this one.
- [ ] **1.5 Audit sweep.** Grep every `sqlite3.connect` and every `state_dir /` construction and put
      the result in the table above. The point is to be able to say the list is complete.

**Acceptance:** two accounts on one hosted daemon, same agent id, can read nothing of each other's
across any store. **Risk:** low — additive columns, empty-means-unscoped preserved for desktop.

---

## Rung 2 — a kernel-level filesystem boundary

**Goal:** the fs fence stops being advisory.
**Size:** ~1 week including terraform. **Ships on its own:** yes.

Today one EFS access point (`/data`, uid 1000) is mounted by every task, so every tenant's files are
on one volume behind path comparisons ([`infra/modules/data.tf:105`](../../infra/modules/data.tf)).
Any code path that forgets `check_read` is outside the fence.

- [ ] **2.1 Decide the destination: S3-per-prefix, not EFS-per-account.** An EFS access point per
      account *is* kernel-enforced (it pins a root dir and uid/gid), but AWS caps access points at
      **1,000 per file system** — so it is a bridge to ~1,000 users, not a destination. Write the
      decision down before building either.
- [ ] **2.2 Bridge (optional, only if a customer needs it before Rung 3):** access point per account,
      created on signup by the accounts service, mounted by the sandbox task. Buys a real boundary
      immediately at a known ceiling.
- [ ] **2.3 Destination: workspace in S3 under `s3://<bucket>/accounts/<acct>/…`.** Synced into the
      execution box at start, back out at end. This is what makes Rung 3's boxes disposable — a
      container that mounts nothing shared cannot leak through the mount.
- [ ] **2.4 Retire `/data` as a tenant store.** It keeps deployment artifacts (bundles, the agent
      catalogue) and stops keeping user data.

**Acceptance:** a deliberately fence-bypassing test tool cannot read another account's file, because
the file is not in its namespace at all. **Risk:** medium — a data migration; needs the same
care as the Postgres one (verify counts, keep the old copy until proven).

---

## Rung 3 — the sandbox plane (tools execute in the user's box)

**Goal:** `exec` becomes safe on the web. Tool execution leaves the daemon process.
**Size:** ~3–4 weeks. **Ships on its own:** yes — it is a new backend beside two existing ones.

This is additive by construction.
[`sandbox/backends.py`](../../agent_runtime/infrastructure/tools/sandbox/backends.py) says it
outright: *"A gVisor/Firecracker/remote backend is added here and nowhere else."*

- [ ] **3.1 `RemotePluginSandbox`** implementing the existing `PluginSandbox` protocol —
      `run_tool(plugin_id, tool_name, params, *, grant, ctx) -> ToolResult`. Same signature, same
      `CapabilityGrant`, different far side.
- [ ] **3.2 The box image.** The daemon's own image plus the plugin runtime, no credentials, no
      network except an egress proxy. `CapabilityGrant.secrets` stays `{}` by default — that default
      is the single most important one in the design and it does not change here.
- [ ] **3.3 Reuse the model broker verbatim.** The child already asks the host to make model calls
      over a JSON frame ([`model_broker.py`](../../agent_runtime/infrastructure/tools/sandbox/model_broker.py));
      the only change is that the frame now crosses a network hop instead of a pipe. Metering,
      clamping and the per-run call cap are untouched.
- [ ] **3.4 Warm pool + idle teardown.** Size to *concurrent arrival rate*, not registered users.
      Assign a warm box on first tool call in a session; reclaim after N minutes idle. Cold start is
      the whole reason for the pool — a Fargate task takes 10–30s and no user waits that long.
- [ ] **3.5 Lift `exec`'s blanket refusal** — replace "this run carries `read_roots`, so no shell"
      with "this run has a box, so the shell runs in it." Keep the refusal as the fallback for a
      fenced run with no box available.
- [ ] **3.6 Route `exec` on the EC2 capacity you already built** ([`ec2_capacity.tf`](../../infra/modules/ec2_capacity.tf))
      if Fargate task-start latency proves too slow for the pool to hide.

**Acceptance:** a hosted user runs `exec` and gets a shell that can see their files and nothing else,
proven by a test that tries to read another account's path and fails at the kernel.
**Risk:** medium-high — latency is the thing that will bite. Emit `outcome=timeout` and elapsed
timings from day one, the way the subprocess backend already does.

---

## Rung 4 — the run plane (the whole loop moves)

**Goal:** what "every user's runtime works independently" actually means. Unlocks Cloud Agent Builder
reaching parity with the desktop one.
**Size:** ~6–8 weeks. **Ships on its own:** yes, but behind a flag, per-agent first.

**Why Rung 3 is not enough:** `create_tool` writes Python and **hot-loads it into the running
process**. Under Rung 3 that process is still the shared daemon, so `create_tool` stays denied and
Cloud Agent Builder stays crippled relative to Agent Builder. Only moving the *loop* fixes it.

- [ ] **4.1 Split the gateway.** [`gateway.py`](../../agent_runtime/presentation/gateway.py) is 7,271
      lines and holds both roles. Separate *router* (authenticate, resolve account, persist
      transcript, meter) from *runtime* (build the RunContext, run the loop, execute tools). Do this
      as a refactor **with no behaviour change** and ship it before anything crosses a process.
- [ ] **4.2 A run protocol.** The router hands a box `{account, agent, session, message}` and streams
      events back. `RunContext.run_id` already exists for exactly this — correlation survives the
      process boundary.
- [ ] **4.3 Session affinity.** A conversation's turns go to the same box while it is warm, so the
      workspace does not resync every turn.
- [ ] **4.4 Per-agent opt-in.** `hosted_agents_allow = ["agent-builder"]` — the escape hatch
      `agent-builder/agent.toml` already names. Turn it on for one agent, prove it, widen.
- [ ] **4.5 Lift `requires_local`** and let Cloud Agent Builder have `create_tool`, `exec` and
      `process`. At that point the two builders differ by deployment, not by capability — and the
      "one destination, two implementations" note in both agent.tomls becomes literally true.

**Acceptance:** Cloud Agent Builder builds an agent with a private Python tool, end to end, on the
hosted daemon. **Risk:** high — this is the real refactor. Mitigation is 4.1 shipping alone, first,
with nothing else changing.

---

## Rung 5 — the enterprise tier

**Goal:** match what Claude Enterprise actually guarantees, then sell above it.
**Size:** ~3 weeks for the shared tier; dedicated is nearly free once it exists.

Claude Enterprise does *not* get its own deployment. It gets a stronger data boundary and policy
controls on shared infrastructure. Match that first — it is the affordable 95%.

- [ ] **5.1 Org id on every row** and an authorization check on every read. Rung 1 is the account
      half of this; this is the org half. Ties into the open items in
      [`enterprise-tenancy-plan.md`](enterprise-tenancy-plan.md).
- [ ] **5.2 Audit log** — who ran what, when, as which account, against which org's budget.
- [ ] **5.3 Retention policy per org** — how long transcripts live, and a real delete.
- [ ] **5.4 SSO / SCIM.** Also closes the open "unverified email + domain" org-join hole.
- [ ] **5.5 BYO key per org**, reusing the existing BYOK funnel.
- [ ] **5.6 Dedicated SKU** — own ECS service, own database, own bucket. This is a terraform
      workspace, because "one binary, two knobs" already holds. Price it accordingly.

---

## Cost model

A Fargate task at 0.5 vCPU / 1 GB is roughly **$0.025/hour**. With idle teardown:

| Scenario | Cost |
|---|---|
| One user chatting 20 min/day | ~$0.25 / month |
| 10,000 registered, 50 concurrent | ~60 warm boxes ≈ **$1,100 / month** |
| Same, without idle teardown | 10,000 boxes ≈ $180,000 / month — the reason the unit is the *session* |

Warm-pool size tracks concurrent arrival rate. Idle teardown is not an optimisation; it is the thing
that makes the architecture affordable at all.

---

## Sequencing

```
Rung 1 (data)  ──┬──> Rung 2 (fs) ──> Rung 3 (tools) ──> Rung 4 (loop) ──> lift requires_local
                 └──> Rung 5 (enterprise data boundary)
```

Rung 1 is a prerequisite for everything — a per-run box does not help if two users still share a
notifications table. Rung 5's data half can run in parallel with 2/3 by a second pair of hands.

**Stop-and-ship points:** after Rung 1 (leak closed), after Rung 3 (`exec` on the web), after
Rung 4 (builder parity). Each is a thing you can tell a customer about.

---

## Decisions needed before starting

1. **Rung 2 destination — S3 or EFS access points?** Recommendation: S3, with access points only if a
   customer needs a boundary before Rung 3 lands.
2. **Rung 3 compute — Fargate or the existing EC2 capacity?** Recommendation: measure Fargate's task
   start behind the warm pool first; fall back to EC2 only if the pool cannot hide it.
3. **How far does Rung 4 go — every agent, or opted-in agents only?** Recommendation: opt-in, with
   `agent-builder` first, so the blast radius of the refactor is one agent.
4. **Does Rung 5's dedicated SKU get sold before it is built?** It is cheap to build but it is a
   support commitment, and that is a business call.
