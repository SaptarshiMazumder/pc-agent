# Model calls from sandboxed plugins — plan of execution

**Status: BUILT 2026-08-08 (uncommitted). Option B, the broker.** P0–P7 done except the two items
marked open at the end. 19 tests in `tests/unit/test_sandbox_model_broker.py`; suite 1097 green.

Decisions the user made on P0: the **account operating the agent pays** ("obviously"), and the
three proposals below (attribution to the plugin, a finite per-run ceiling, and the parent — not
the plugin — choosing the model) were accepted as written.

Related: [multi-tenant-marketplace-plan.md](multi-tenant-marketplace-plan.md) Phase 2 (the sandbox
this extends), and the tool-model resolution seam (`application/tool_models.resolve_tool_model`).

---

## The problem, precisely

An untrusted plugin tool — one that shipped inside a marketplace agent's own package
(`agents/<id>/plugins/`) — runs in a child process whose `CapabilityGrant` is default-deny:

```
net_allowlist = ()     # no network
secrets       = {}     # no keys
config        = redacted projection   # no keys there either
```

A model call needs a reachable endpoint **and** a credential. It has neither, so it fails.

Concretely, `agents/game-master/plugins/game-kit`'s `narrate_scene` calls
`oneshot.text_complete(...)`, which builds an HTTPS request. With the sandbox on, that request is
refused by the child guard before it leaves the process.

**Why this matters more than it looks.** Most third-party plugins worth installing will want a
model call. If untrusted plugins cannot call models, the sandbox effectively bans the interesting
half of the marketplace — and the pressure will be to turn the sandbox off, which is the worst
possible outcome. So this is a blocker for opening third-party publishing, not a nice-to-have.

---

## Why "just allow the network" is the wrong instinct

Two reasons, and the second is the one that decides the design.

**1. Any credential in the child is a credential the plugin owns.** A plugin can read its own
environment. Hand it a proxy key and it has that key for as long as the key lives — to spend on
whatever it likes, at whatever rate it likes, not only on the call the user asked for.

**2. Host allowlisting is weakly enforced at the interpreter level.** The child guard matches
allowlisted hosts at DNS-resolution time; `socket.connect` sees an address that has already been
resolved and cannot be reliably mapped back to a name. This is documented in `child_guard.py`. The
STRONG case — the one the sandbox actually guarantees — is the *default* one: an empty allowlist
denying all network. Opening a hole for the proxy weakens the property the sandbox is sold on.

So the goal is not "let the plugin reach the model endpoint". It is **let the plugin obtain a
completion without ever holding a credential or a socket.**

---

## The options

### Option A — network allowlist + a scoped proxy key
Grant `net_allowlist = (proxy_host,)` and inject a per-account, short-lived proxy key via
`grant.secrets`. Plugin code is unchanged; `oneshot.text_complete` just works.

- **For:** smallest change. Nothing in the plugin API moves.
- **Against:** the child holds a live credential (see 1 above) and a real socket (see 2). And it
  **only works in hosted mode** — a desktop user in BYOK mode has no proxy, so the only way to
  fulfil the call would be to hand over their actual provider key. That gap is disqualifying,
  because "cloud desktop" is a first-class mode here, not an edge case.

### Option B — a broker over the existing pipe  ← **recommended**
The child never touches the network. `text_complete` inside the sandbox becomes a stub that writes
a request frame back to the parent on the pipe that already exists. The **parent** performs the
call with its own credentials, applies policy and quota, and writes the response back.

```
child:  {"t":"model_request","id":"m1","kind":"text","model":"...","prompt":"...","max_tokens":220}
parent:                  … resolves the model, checks the grant, meters, calls …
parent: {"t":"model_response","id":"m1","ok":true,"text":"..."}
```

- **For:** the grant stays `net_allowlist = ()` and `secrets = {}` — the strong property survives
  intact. No credential ever crosses. Works identically in **every** mode (hosted proxy, BYOK
  desktop, local model) because the parent decides how to fulfil it. The parent gets metering,
  per-run caps, model clamping and refusal for free. The same channel extends later to any other
  capability the child should not have directly.
- **Against:** needs a genuinely bidirectional protocol (today the pipe is one-way: job in, JSONL
  out) and a shim inside the child. More work than A, and the work is in the protocol, which is
  the part that has to be right.

### Option C — exempt model-bearing plugins from the sandbox
What happens today with the sandbox off. Named only to reject it: it means the plugins most worth
scrutinising are the ones that get none.

**Recommendation: B.** A is faster and leaves the sandbox claiming a property it no longer has,
while failing outright on desktop BYOK. B costs one protocol change and buys the whole problem.

---

## Execution

### P0 — Decisions (blocking, needs the user)
- [x] Confirm Option B.
- [x] **Who pays, and is it visible?** A plugin's model call spends the account's budget. Default
      proposal: charged to the account, attributed to `plugin_id` in the ledger, visible in usage.
- [x] **Per-run ceiling.** Proposal: a token/call cap per tool invocation from config, default
      generous but finite, so a plugin in a loop cannot drain a balance silently.
- [x] **Which models may a plugin ask for?** Proposal: the plugin does not choose freely — the
      parent resolves via the existing `resolve_tool_model` chain and refuses anything outside the
      account's allowed set. Otherwise naming an expensive model is a cost attack.

### P1 — Bidirectional protocol
- [x] Keep the child's **stdin open** after the job is written (today it is closed immediately) and
      read it concurrently with the tool running.
- [x] Add frame kinds to `sandbox/protocol.py`: `model_request` / `model_response`, correlated by
      `id`, so several in-flight requests are possible.
- [x] Parent: `model_request` served on its own task so the read loop keeps draining; child: a
      request/response client keyed by id. **Built with `threading.Event`, not `asyncio.Future`**
      as written here — `text_complete` is synchronous by contract and plugins call it through
      `asyncio.to_thread`, so the waiter has to work from a worker thread as well as the loop.
      Wrapping stdin in an asyncio stream was the alternative and is not portable to Windows for a
      non-socket handle.
- [x] **Timeout interaction** — the wall clock currently kills a child at `grant.timeout_s`. A model
      call can legitimately take 60s. Either exclude broker time from the deadline or raise the
      deadline when a request is outstanding. Getting this wrong shows up as random kills under
      load, which is exactly the bug nobody can reproduce.
- [x] **Abort** must cancel an in-flight model call, not just kill the child afterwards.

### P2 — The broker (parent side)
- [x] One handler that receives a `model_request`, checks it against the grant, resolves the model,
      performs the call through the existing `oneshot` path, and replies.
- [x] Enforce: model allowed, token cap, call count per run, total per run.
- [x] Refusals come back as `{"ok":false,"error":"..."}` and surface to the plugin as an ordinary
      exception with a readable message — never a hang and never a generic failure.
- [x] Runs with the **caller's account context** so metering lands on the right ledger.

### P3 — The child shim
- [x] Before the plugin is loaded, replace `oneshot.text_complete` and `oneshot.vision_complete`
      on the module object with broker-backed versions.
- [x] Patch **the module attribute, before `_load_tool`** — a plugin doing
      `from ...oneshot import text_complete` at module top level binds the name at import time, so
      patching after load would miss it. Patching the module first covers both binding styles.
- [x] Keep the signatures identical. A plugin author should not know or care which side of the
      boundary the call is served from — that is what makes this safe to make the default.

### P4 — Grant + declaration surface
- [x] `CapabilityGrant` gains model rights (proposal: `models: tuple[str, ...]`, empty = none), so
      it stays default-deny and the resolver decides — same shape as `fs_paths` and `net_allowlist`.
- [x] A plugin **declares** it needs a model in `plugin.toml`, consistent with the
      self-describing-capabilities rule. This is also the consent surface: the Store can say "this
      agent's tools make AI calls, charged to you" at install time instead of after the bill.
- [x] `DefaultCapabilityResolver` grants model access to untrusted plugins only when declared, and
      the future approval layer can make it interactive without touching anything below.

### P5 — Metering and visibility
- [x] Attribute spend to `plugin_id` + `tool` + account in the ledger.
- [x] Metrics: `sandbox_model_request_total{outcome}`, tokens, latency, and refusals by reason.
- [x] A plugin that gets refused for quota must produce a message a user can act on.

### P6 — Tests
- [x] `narrate_scene` works with the sandbox ON — the end-to-end case this whole plan exists for.
- [x] A plugin **without** the declaration is refused.
- [x] A plugin asking for a disallowed model is refused.
- [x] Token/call caps are enforced; the refusal is legible.
- [x] The child still has **no** direct network: a plugin that tries `socket.connect` to the proxy
      host directly is still denied. The broker must not become a hole in the thing it protects.
- [x] No credential appears anywhere in the child's environment or in the job payload.
- [x] Abort during an in-flight model call kills cleanly, no orphan process.

### P7 — Docs
- [x] `docs/PROTOCOL.md`: the broker frames.
- [x] Plugin-author guide: "your tool calls `text_complete` exactly as it always did".
- [x] Update the sandbox module headers, which currently state flatly that model-bearing untrusted
      tools cannot work.

---

## Risks

**The broker is a new trust boundary.** It parses input written by untrusted code. Frames must be
size-capped and strictly validated; a prompt is attacker-controlled text and must never be
interpolated into anything but a model call.

**Prompt content is an exfiltration channel** — a plugin can put whatever it can read into a
prompt. Under the sandbox that is its own workspace and its params, which the user already handed
it, so the marginal risk is small; it is not zero and should be stated, not assumed away.

**Cost is the realistic failure mode**, not compromise. A buggy plugin in a retry loop spends real
money. The caps in P2 are the mitigation and should land with the feature, not after it.

**Latency.** Every plugin model call now crosses two process hops. Negligible next to model
latency, but it means the deadline handling in P1 is load-bearing.

---

## Size

P1–P3 are the substance (the protocol, the broker, the shim); P4–P6 are mechanical once those are
right. Roughly **M–L**, and it is the last thing standing between the sandbox and being able to
leave it on by default for third-party plugins.

---

## Progress log

**2026-08-08 — BUILT (Option B).** Files: `sandbox/model_broker.py` (host side),
`sandbox/child_models.py` (the shim + the stdin reader), plus changes to `protocol.py`, `worker.py`,
`subprocess_backend.py`, `capabilities.py`, `domain/sandbox.py`, the `CapabilityResolver` port and
`config.py`.

Four things worth knowing that the plan did not anticipate:

**The declaration already existed.** P4 proposed a new `plugin.toml` field. `Tool.needs_model` is
already set by every model-bearing tool so the catalog can offer a model picker — so the resolver
reads that instead. One place a tool says "I call a model" beats two that can disagree, and the
interesting question when they disagree is which one the sandbox believes.

**The grant lists model IDS, and that list is the clamp.** `CapabilityGrant.models` is derived per
tool from the normal resolution chain — what the tool would have used anyway — so the sandbox
neither widens nor narrows the ordinary choice. Every step of the chain is included, not just its
first hit, because the tool re-resolves inside the sandbox against a projected config and a grant
holding only the host's answer would refuse a legitimate call. `config.sandbox_models` pins it
outright if an operator wants that.

**The stub goes in `sys.modules`, not on the module.** Pre-seeding means the real `oneshot` is
never imported in the child — so it cannot be reached around by importing it again, AND litellm's
import cost is never paid, which matters when it is per tool call.

**The deadline had to learn to pause.** A 60s model call would otherwise eat a 120s tool budget
that was never meant to cover it, producing kills that depend on how busy the provider is — close
to unreproducible. `_Deadline` stops the tool's clock for exactly the interval the child is blocked
on us, reentrant by depth so several in-flight requests do not restart it early. There is a test
that a tool hanging on its OWN time is still killed, so the pause is not an opt-out.

Tests worth naming: a sandboxed tool obtains a completion **and is still refused a socket** (the
broker is not a hole in the thing it serves); a plugin-supplied `api_key` is ignored; vision cannot
be used as a file-read oracle for a path outside the grant; and the spend is billed to the account
seen at metering time, which is the only way to catch attribution silently breaking — the call
would still succeed and the bill would just go somewhere else.

**Still open:** streaming (the broker is request/response, so a plugin cannot stream tokens) and
embeddings (only `text` and `vision` kinds are served). Both are additive — a kind plus a handler.
