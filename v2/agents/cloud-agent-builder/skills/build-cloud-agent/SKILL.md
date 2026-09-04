---
name: build-cloud-agent
description: Use when the user asks to create, author, build, scaffold or extend a CLOUD AGENT — one that runs on a hosted, multi-tenant daemon reached through a browser. The authoritative format reference for everything under agents/<id>/, tuned for the web.
always: false
---

# Building a cloud agent

**An agent is a directory.** There is no registration database — you create files in the right
places and the daemon reads them. This is the format reference; follow it exactly and a by-chat
agent is byte-identical to a hand-authored one.

**What makes it a CLOUD agent** — the one thing that shapes every decision below:

- It runs on a daemon **shared by many accounts**. Every file write is fenced to the caller's own
  account subtree; there is no "the machine's disk", only "this account's space".
- It has **no shell and no custom Python**. A subprocess cannot be confined to one tenant's files,
  so `exec` is refused on the web, and you cannot write code tools (`create_tool`). If a task
  seems to need one, it needs a different shape — an HTTP call, a declared MCP server, a skill.
- It reaches the world by **declaring** what it uses (tools, capabilities, MCP servers, settings),
  never by shipping code. That declaration is what lets it travel to the cloud unchanged.
- It is marked **`[delivery] web = true`** and must pass the validator's portability checks. Those
  checks are the difference between "works for me" and "works for whoever signs in next".

Author files with the `write` tool. Paths may be absolute — `create_agent` gives you the agent's
directory.

## Order of work

**Read this, then follow it. Everything after is reference — look things up as each step needs it.**

0. **Ask what window it should have.** A product decision, always the user's:

   ```
   What should its window be?
     1. None       — no window; runs on a schedule or is called by another agent
     2. Chat       — a conversation window of its own
     3. Dashboard  — numbers/charts on screen the moment it opens
     4. Workbench   — a working surface with the agent beside the content
   ```
   Recommend one with a reason; use their answer.

1. **Get the shape, not twenty answers.** What does it do, who/what does it talk to, what does it
   need access to. Then write a first version and show it — reacting to a real agent beats
   answering questions about a hypothetical one.

2. **`create_agent`** — writes the skeleton: `agent.toml`, `IDENTITY.md`, and (if it has a window)
   a complete built app. It auto-validates and hands back problems in the same result.

3. **Fill in the agent.toml** — model, description, the tools and capabilities it needs, any MCP
   servers or settings. See the reference below.

4. **Give it what it connects to.** Web APIs → `web_fetch` in its tool list plus an
   `IDENTITY.md`/skill that names the endpoints. A service with an MCP server → an `[[mcp]]` block
   plus any `[[settings]]` for its credential. Never paste a real key into the file.

5. **`build_app`** if it has a window and you changed the source, then **`verify_app`** to prove the
   screen actually renders.

6. **`validate_agent` until clean.** Never say it is ready before it comes back clean — the
   portability findings (below) are what keep it working on the web.

   **And FINISH THE APP before you call it done — your job, not the user's.** If it has a window,
   every `@placeholder` must already be adopted or deleted (see below): run
   `grep -rn "@placeholder" app/src`, see nothing, and confirm `validate_agent` reports no
   `UI_PLACEHOLDER_SHIPPED`. Handing over an agent that is still partly the starter template is the
   worst failure here — the user cannot tell an unfinished app from a broken one, and they find out
   only when shipping refuses, in words written for a validator rather than for a person. If you
   ran out of room, say so plainly and name what is left; never call it done.

7. **`reload_agent`** to activate it live, then tell the user what you built: name the files and
   what each does.

---


## Placeholders are a LOOK, not a plan

A window template ships example widgets so a new agent has something worth looking at before it
has a single tool of its own. Every one of them carries `@placeholder` in its header.

**Find them in any agent, from any template:**

```
grep -rn "@placeholder" app/src
```

That is the whole contract — a marker, not a folder and not a list of filenames. Templates come
and go and each ships different examples; the tag is what they have in common, so this procedure
never needs to know which template an agent was made from.

**They exist to show you three things and nothing else:** what this template's screens look like,
what shape its pieces are (a number tile, a table, a chart, a row of activity), and how one is
wired to a tool. They are sample data behind a real component.

**The failure they invite, and the one rule that prevents it: DECIDE WHAT THE AGENT NEEDS FIRST,
then look at what the template happened to draw.** An agent with two numbers gets two tiles, not
the four the template shipped. An agent with nothing to chart loses the chart. Building around a
placeholder — keeping the donut because a donut was there — produces an agent shaped like a
template instead of shaped like its job, and it is the single most common way a generated window
ends up impressive and useless.

**Each one ends in exactly one of two states:**

- **Adopted** — you changed it to this agent's real data. Delete the `@placeholder` line, and
  rename the file so the imports stop saying `Placeholder`. It is the agent's component now, and
  the name should say so.
- **Deleted** — the agent has no use for it. Remove the file *and* every import of it. A widget
  nothing renders is dead weight that the next person has to read before they can ignore it.

There is no third state. **By the time the agent is packaged, `grep -rn "@placeholder" app/src`
must find nothing** — `validate_agent` reports `UI_PLACEHOLDER_SHIPPED` while any remain, and it
closes the pack and publish gates. It stays a warning while you build, because the widgets are
supposed to be there on day one; it only becomes fatal when the work leaves this machine.

Sample data goes with them: a template's `SAMPLE_*` constants exist so the window renders before
a `fetch` does. A panel that quietly falls back to sample data is worse than an empty panel — it
shows a number that was never measured.

# Reference

## agent.toml

The one required file. Minimum:

```toml
name = "Invoice Watcher"
version = "1.0.0"
description = "Watches a webhook for new invoices and summarises each one."
model = "anthropic/claude-sonnet-5"   # omit to inherit the daemon default
```

### Making it a cloud agent

```toml
[delivery]
web = true          # this agent is delivered to the web. The validator now HOLDS it to that:
                    # it refuses a shell grant, flags reliance on machine-local paths, and checks
                    # every declared capability is one a hosted run can honour.
```

### Tools

```toml
[tools]
allow = ["read", "write", "web_fetch", "web_search", "update_plan", "verify_answer"]
```

- `allow` absent → the agent gets **every** shared tool. On the web, always set an explicit
  `allow` — least privilege is the difference between a tidy agent and one holding tools it never
  uses.
- **Never grant `exec` or `process`** to a web agent. They are refused at runtime anyway; granting
  them makes the validator flag the agent as broken-on-arrival for the web users it is for.
- Filesystem tools (`read`/`write`) are fine — they are fenced to the account's own space. If the
  agent should not touch files at all, leave them out.

Reads and writes are automatically confined to the running account's own subtree. Do **not** write
absolute machine paths into the agent's logic; write relative to its workspace.

### Capabilities

```toml
[capabilities]
# Turn on only what the agent needs. On the web, leave `mcp_workshop` OFF — an agent does not spin
# up arbitrary MCP servers live in a shared daemon; it DECLARES the one it uses (below).
```

### Connecting to a service (MCP)

When a service has an MCP server, declare it rather than writing code against it:

```toml
[[mcp]]
name = "github"
command = "npx"
args = ["-y", "@modelcontextprotocol/server-github"]
# the server's credential comes from a declared setting, never inlined:
env = { GITHUB_TOKEN = "${GITHUB_TOKEN}" }

[[settings]]
key = "GITHUB_TOKEN"
label = "GitHub token"
secret = true
```

`[[settings]]` blocks become fields on the agent's own settings page; the value is stored per
account, never in the file. `${NAME}` in an MCP `env` is substituted from that setting at run time.

### A window

```toml
[app]
title = "Invoice Watcher"
mode = "window"        # or "dashboard" / "workbench"
entry = "ui/index.html"
public = false         # true only for an agent meant to be used by anonymous visitors
```

`create_agent(window=true)` builds a complete, working window. `app/` is the React source; `ui/` is
what the daemon serves. An edit to `app/` is not visible until `build_app` compiles it into `ui/`.

## IDENTITY.md

The agent's brief, in the second person: what it does, how it talks, what it must never claim. This
is where you put the endpoints it calls, the shape of its answers, the lines it will not cross.

## Skills

An agent's own how-to lives in `agents/<id>/skills/<name>/SKILL.md`, with front-matter:

```markdown
---
name: summarise-invoice
description: Use when a new invoice arrives — how to read it and what fields to pull.
always: false
---
```

Reach for a skill when the agent needs a repeatable procedure that would bloat IDENTITY.md.

## What the validator refuses (the cloud-specific findings)

- **A shell on a web agent** — `exec`/`process` granted with `[delivery] web = true`. Every hosted
  run refuses the shell, so the web users the agent is FOR get one that does nothing. Drop the
  grant, or (rare) mark it `requires_local` and stop calling it a cloud agent.
- **Undeclared credentials** — an `[[mcp]]` server whose token was never declared as a `[[settings]]`
  block, or a real key pasted into the file. Invisible until someone else installs it and it fails.
- **A window built from stale source** — `app/` edited but never `build_app`-compiled, so `ui/`
  (what ships) is behind. Looks finished, serves the old screen.

When something a user asks for cannot be a cloud agent — it genuinely needs a shell, a desktop
`.exe`, or hot-loaded code — say so plainly, and build the cloud-native version of what they
actually want instead.
