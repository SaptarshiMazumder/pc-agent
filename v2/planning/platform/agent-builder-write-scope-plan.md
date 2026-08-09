# Agent Builder — write scope, and knowing what tools exist

**Status:** planned, none of 1–4 started. Written 2026-08-09.
**Why this exists:** Agent Builder can currently write anywhere on disk, and it chooses an
agent's tools from memory. Those are separate problems that surfaced from the same incident.

---

## How we got here

A ComfyUI agent built by Agent Builder shipped a private tool, `artifact-location`, that called
`subprocess.Popen(["explorer.exe", …])` to reveal a file. That works for its author and is a
hard denial for anyone who installs the agent — private tools are untrusted on a machine that
installed them, and spawning is denied outright with no host-brokered alternative.

Chasing that turned up two bigger things.

### The vendored-plugin bypass

Every check built for private tools operates on `agents/<id>/plugins/`. A SHARED plugin
(`plugins/<id>/`) is a different tier: `classify_origin`'s **first** rule is that a tool with no
`_agent_id` is `FIRST_PARTY` — never sandboxed.

A bundle can declare `[[bundle.plugins]] source = "vendored"`. The packer copies that plugin out
of `plugins_dir` into the zip; the installer unpacks it into the **buyer's** `plugins_dir`. It
lands with no `_agent_id`, so on the buyer's machine it is fully trusted.

    create_tool(agent=X, code with subprocess)  -> REFUSED
    create_tool(code with subprocess)           -> written to plugins/, unsandboxed
    declare it vendored in bundle.toml          -> ships inside the .agentpkg
    buyer installs                              -> lands in THEIR plugins/, FIRST_PARTY

The only thing standing there today is an instruction in `create_tool`'s description telling the
model to ask the user before making a shared tool. It did ask — the guard held — but it is a
guard made of prose protecting a capability boundary.

**NOT YET VERIFIED — this is ITEM 5, and it is NOT ours.** Whether anything gates vendoring at
install (consent, a manifest allowlist, publisher signing). Publishing / exe / install is the
other developer's area; the user is raising it with him. Do not investigate or change that path
here. Item 3's decision about `exec` depends on the answer.

### Choosing tools from memory

Agent Builder writes `[tools] allow` for every agent it builds. There is no list of available
tools anywhere — the catalog is built at boot by importing every plugin and letting each call
`api.register_tool(...)`, plus MCP tools at gateway connect and `create_tool` hot-adds. So it
picks names from recall.

Granting it `show_files` (done) puts that one tool in its prompt. It does not scale, and it only
teaches about tools it can CALL — a different, much smaller set than tools it can GRANT.

---

## 1. Write roots — a real block

`agent.toml` declares where an agent may write; `_resolve()` in `plugins/core_fs/fs_tools.py`
enforces it. Every fs tool funnels through that one function, which is why it is the choke point.

```toml
[tools.fs]
write_roots = ["<agents_dir>"]
deny        = ["<agents_dir>/agent-builder"]
```

`<agents_dir>` is a TOKEN, not a path — `<repo>/v2/agents/` in a checkout, `~/.agentd/agents/`
on an install. Resolved from config at check time.

A root, not a list of agent ids: a newly created agent is inside it automatically, so there is
nothing to maintain per agent.

| target | result |
| --- | --- |
| `agents/note-taker/` | allowed |
| `plugins/` | refused — closes the vendored-plugin route at the source |
| `agents/agent-builder/` | refused — it must not edit its own constraints, skill or allow-list |
| its own `workspace/` | refused — same reason |
| anywhere else | refused |

Applies to `write` and `edit`. **NOT `read`** — it must read its own skill and the SDK it
vendors into generated UIs.

**Files:** `plugins/core_fs/fs_tools.py`, the agent-spec parse, `agent-builder/agent.toml`, tests.

## 2. Refuse writes into downloaded agents

Second rule in the same check: if the target agent is in the **marketplace ledger**, refuse.

Reuses the provenance record the sandbox already uses (`installed_agent_ids`) rather than
inventing a second source of truth. Stops quietly editing an installed agent so it no longer
matches what its publisher shipped.

Fail closed: ledger unreadable -> refuse, same as `classify_origin` does.

## 3. Teach it, do not only block it

A refusal that does not say what to do instead just burns turns. Three places:

- **the refusal message** — name the three cases: authoring an agent -> write under
  `agents/<id>/`; wanting a shared tool -> that is the user's decision, ask; wanting to change
  your own rules -> you cannot.
- **`build-agent/SKILL.md`** — the rule stated positively, so it is known before the wall.
- **`agent-builder/AGENTS.md`** — present every turn, including *do not route around this with
  `exec`*.

### The honest gap: `exec`

The block is on `write`/`edit`. `exec` runs a shell; `echo x > file` never touches them. So
enforcement does not cover it, and item 3 is prose there, not a wall.

**Decision taken (user's call):** acceptable, because publishing is the gate — junk written by
`exec` stays on the author's own disk and validation stops it shipping. Be clear-eyed that
between the write and the publish nothing stops it.

**Depends on ITEM 5** (owned by the other developer): if a bundle can vendor an arbitrary shared
plugin, `exec` still has a route out and validation would not see it.

## 4. `tools.json` — the manifest, generated

**The manifest already exists.** Every tool is a class with `name`, `label`, `description`,
`parameters`. It is mandatory and already written for every tool. Nothing needs mandating; it is
simply never surfaced to an agent that does not have the tool.

So: **at boot, after discovery, the daemon writes the live catalog to
`<state_dir>/tools.json`.**

```json
[{"name": "show_files", "plugin": "show", "summary": "Show finished deliverable files…"}]
```

Derived from the registry that actually exists, every boot. Hot-added tools append.

Why this shape:

- **cannot drift** — it is not maintained, it is generated. No "throw loudly if a tool has no
  entry" check is needed, because there is no second list to disagree with.
- **no new tool** — Agent Builder already has `read`.
- **no plugin changes** — nothing new for a plugin author to remember.
- **inside agentd**, which is where it was asked for.

The skill then says: *before choosing an agent's tools, read `<state_dir>/tools.json`.*

Cost: it has to actually read it. Unavoidable short of putting 49 descriptions in every prompt.

---

## 5. Is vendoring gated at install? — NOT OURS

Owned by the other developer (publishing / exe / install). Raised with him separately. The
question: can a bundle vendor an arbitrary shared plugin, and does anything gate that at install
time? If not, a shared plugin created locally reaches a buyer's `plugins_dir` fully trusted, and
item 3's decision to leave `exec` unenforced is unsafe.

Do not change that code path from this workstream.

## Open questions

1. **Who may set `[tools.fs]`?** If it lives in `agent.toml`, an agent that can write agent.toml
   files can widen its own roots. Closed for Agent Builder by the self-deny, but the general
   shape probably wants these coming from operator config, not the agent's own file.
2. **MCP tools in `tools.json`** — they arrive after boot, when the gateway connects. Either
   rewrite the file then, or accept that it lists native tools only and say so in it.

## Already done (do not redo)

- `*` supported in `AGENTD_SANDBOX_UNTRUSTED_AGENTS` — forces every agent down the buyer's code
  path, no id list. `classify.py`, `test_sandbox_force_untrusted.py`.
- `show_files` granted to agent-builder, with a comment recording that the allow-list doubles as
  what it KNOWS exists.
- `package_agent` refuses four shapes that cannot run for a buyer (`_SHIPS_BROKEN`):
  env read, network import, spawn, undeclared model call. Validation still only warns — correct
  locally. `package_agent_service.py`, `test_package_refuses_dead_on_arrival.py`.
- `SKILL.md` documents the spawn constraint (the one with no inverted form) + a drift test tying
  the documented rules to what `create_tool` actually refuses.
- A test forbids the skill naming a specific agent/plugin/filename — an earlier version named
  `artifact-location` and `show_files` and the model recited it back verbatim instead of
  reasoning. Pre-answering one observed case is not teaching.
- `ToolGrantRules`: allowing `exec` without `process` is a finding. `agent-builder` was itself
  missing `process`; fixed.

## The ComfyUI agent

Deliberately NOT fixed by us — it is the live test of whether Agent Builder can act on a finding.
Its `artifact-location` must be deleted (there is no sandbox-safe version), and `ui/app.js`
lines ~218-225 draw an "Open location" button that calls it. Watch whether it touches the UI;
nothing reports a stale `invokeTool` naming a tool that no longer exists.

Note: `show_files` DISPLAYS a file inline; the button REVEALED it in Explorer. Not the same
requirement. Agent Builder proposing a new tool may be correct rather than ignorant.
