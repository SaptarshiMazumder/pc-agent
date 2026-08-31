# Operating rules

**These hold at every step, in every job.** The PROCEDURE — what to do, in what order, for a new
agent and for a change to an existing one — is the `build-agent` skill. Read it before you build
anything, and follow it; it is the only order of work. This file is what is true regardless of
which step you are on.

## Non-negotiables, on every turn

The skill carries these in their place in the procedure. They are repeated here — briefly, and
only these — because AGENTS.md is present on EVERY turn while the skill is read on demand, and
each of these fails **silently** when it is skipped: the files all look right, and the user is
the one who finds out.

- **Plan before you build.** `update_plan` with the steps, before writing anything. The user
  watches it; it is how they know you have not lost the thread.
- **Never hand-write a window.** `create_agent(window=true)` — or `scaffold_react_app` for an
  agent that already exists — hands you a complete, working one. Starting from a blank file is
  how every broken UI so far got built.
- **`app/` is source; `ui/` is what the daemon serves.** Call `build_app` after every change to
  `app/`, or the user reloads and sees the old screen with nothing to explain why.
- **Run what you write.** `exec` is there for it: generated JS gets `node --check`, a generated
  Python plugin gets imported. A syntax error you never looked for is a broken agent you handed
  over.
- **You may only write inside the agent you are building.** Enforced by the daemon, and `exec`
  is not a way around it.
- **Finished means verified** — `validate_agent` clean and you have run the agent. Not when the
  files exist.

## Your environment

Stated so you never have to discover it by trial and error:

- **`exec` runs a real shell** in the agent's workspace by default. On Windows that is `cmd`, so
  `where`, `findstr` and PowerShell are all available.
- **Node and npm ship with the product and are on PATH.** `AGENTD_NODE_DIR` names the directory
  exactly. Never ask the user to install a toolchain to build a window.
- **`uv` and `uvx` ship too**, under the runtime's `Scripts/` directory. The daemon resolves a
  declared `[[mcp]]` launcher to its absolute path itself, so `command = ["uvx", …]` works
  without you finding it first.
- **Paths may be absolute** in every fs tool. Prefer them.
- **`find` matches file NAMES; `grep` searches CONTENT.** Reach for `grep` for "where is this
  defined" and "who calls this".

If something is genuinely missing, say so in one line — do not spend turns probing for it.

## Never guess at an interface. Read it.

These are the ONLY things guaranteed to exist on every install:

| when you are unsure about | read |
| --- | --- |
| an event name, its payload shape | `build-agent`'s `reference/app-connection.md` (kept true by a test) |
| how a working agent UI is really built | **your own** `app/src/` — you were given a complete one |
| what the SDK actually offers | **your own** `ui/vendor/agentd-client.js` |
| what a tool takes | its `plugin.toml` and module |

A plausible-sounding event name that does not exist produces a UI where **every branch is dead**:
the socket connects, the console logs, and the screen never changes. This has already happened.
Guessing is the single most expensive shortcut available to you.

**Do NOT go looking at other agents for examples.** This machine has whatever its user built and
nothing else, and some older agents contain a dead event branch — copying one would spread a bug.
Your own `app/src/` ships with you, is checked by `validate_agent`, and is the reference.

**Never write an app UI from a blank file — and you never have to.** An agent created with
`window=true` already has a complete one: the shell, the chat, the run-event handling, and the
four shared screens (sign-in, credits, settings, organizations) wired and working. Read
`app/src/App.tsx` and change it. Writing one from nothing is how every broken UI so far got built,
and it fails silently.

## Where you may write

**Inside the agent you are building, and nowhere else.** Enforced, not advised — `write`, `edit`,
`create_agent` and `create_tool` all refuse anything else.

- Not the shared `plugins/` directory. A tool there is never sandboxed for whoever installs it,
  so adding one is a change to the whole machine and is the operator's decision, made by hand.
  `create_tool` requires an owning `agent` and always writes a private tool. To give an agent a
  capability the shared catalog ALREADY has, name that existing tool in its `[tools] allow`.
- Not your own definition or workspace — an agent that can rewrite its own rules has none.
- Not an agent someone installed from a package; it would stop matching what its publisher
  shipped.

Reading is unrestricted. **Do not use `exec` to write where `write` refused** — that is defeating
a boundary, not solving a problem.

**When unsure, ATTEMPT the write.** The daemon is the authority and it fails closed: a write you
may not make comes back refused, naming the reason, having changed nothing. A refusal you can act
on beats a turn spent reasoning yourself out of a change the user asked for.

## WHOSE an agent is, is DATA — never its path

An agent's `.agentd-meta.json` states it, and you may always read it:

| `origin` | what it means | may you edit it |
| --- | --- | --- |
| `authored` | this user made it (here, or through you) | **yes** |
| `installed` | it arrived in someone else's `.agentpkg` | no — the daemon refuses |
| `curated` | it belongs to the platform | no — the daemon refuses |

**Do not infer trust from a directory name.** An agent's definition legitimately lives in either
the shared catalogue (`agents/<id>/`) OR the signed-in user's own layer
(`…/accounts/<acct>/agents/<id>/`) — one folder per agent, holding its definition, its
`workspace/` and its `sessions/` together. Both are ordinary places for an agent to live, and an
`authored` one is the user's own work wherever it sits. Reading the record takes one `read`;
guessing from the path is how a user gets told their own agent is untouchable.

## Hard rules

1. **Never create, overwrite, or edit the agent `main`.** It is the default agent.
2. **Never author `presentation.json`.** The daemon generates it. Display fields (`tagline`,
   `color`, `suggestions`) are TOP-LEVEL keys in `agent.toml`.
3. **Every top-level key must appear before the first `[table]`.** TOML scopes a key written
   after `[app]` *into* `[app]`, where nothing reads it and the value is silently ignored.
4. **Do not use `skill_workshop` to give another agent a skill.** It always writes into the
   calling agent's own skills dir — yours. Author the target agent's playbooks with `write` at
   `agents/<id>/skills/<name>/SKILL.md`.
5. **Always set `version`**, and bump it on every change you ship. Bundle installs supersede an
   older copy by version; an agent without one cannot be updated — and publishing REFUSES a
   version-less agent.
6. **An agent meant to ship writes only inside itself.** Never give a built agent `[tools.fs]
   write_roots` beyond `<agent_dir>`: packaging and publishing refuse it, and the runtime clamps
   installed copies to their own folder regardless. Wide write scope is for local authoring
   agents like this one.
7. **An agent delivered to the web cannot use a shell.** Every hosted run refuses `exec`. If the
   design needs one, it is `requires_local = true` — which also means it cannot be `[delivery]
   web = true`. Design web agents around read/write/edit/ls/find + plugin tools, ship runtime
   data in definition dirs (never `workspace/` — each web user's workspace starts empty), and
   assume reads are fenced to the agent's own definition + the user's own files.

## Honesty

8. **Do not confuse describing with doing.** Never end a turn announcing an action you have not
   taken. If you say you will write a file, write it in that same turn. Before declaring
   finished, use `verify_answer` — it exists to catch an answer that only promises.
9. Do not say an agent is finished until `validate_agent` returns clean **and you have run it**.
   If a finding is a warning you are deliberately leaving, name it and say why.
10. If the user asks for something the platform cannot do, say so in a sentence and offer the
    closest thing that works. Do not scaffold a tool that cannot function.
11. An agent's private tools (`agents/<id>/plugins/`) are treated as **untrusted** code — the
    same tier as a plugin that rode in inside a downloaded agent package. If a private tool needs
    the network, host files outside the workspace, or secrets, tell the user that up front rather
    than shipping something that will be denied at runtime.
12. **A private tool calls a model through `oneshot.text_complete` / `vision_complete`, never
    through a provider's HTTP API and never with a key from the environment.** The sandbox
    inverts the call — the tool asks, the host performs it — so that route is the only one that
    still works after someone installs the agent. `create_tool` enforces this: it refuses code
    that reads env vars or imports a network client, and it sets `needs_model` for you. Author a
    plugin by hand and it is yours to get right; `validate_agent` reports what you missed.
