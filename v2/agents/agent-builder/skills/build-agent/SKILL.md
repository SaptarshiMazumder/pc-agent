---
name: build-agent
description: Use when the user asks to create, author, build, scaffold or extend an AGENT — including giving an agent its own tools, skills, app UI, or model wiring. The authoritative file-format reference for everything under agents/<id>/.
always: false
---

# Building an agent

**An agent is a directory.** There is no registration database and no build step — you
create files in the right places and the daemon reads them. This document is the format
reference; follow it exactly and a by-chat agent is byte-identical to a hand-authored one.

Author files with the `write` tool. Paths may be absolute.

**READ THE ORDER OF WORK BELOW FIRST, THEN FOLLOW IT.** Everything after it is reference —
look things up there as each step needs them. The reference is long because the file format
is exact; the procedure is short because it is what you actually do.

## Order of work

0. **ASK THE USER WHAT WINDOW IT SHOULD HAVE. Every time, before you build.**

   ```
   What should its window be?
     1. None       — no window; reached from JARVIS chat or on a schedule
     2. Chat       — a conversation window of its own
     3. Dashboard  — numbers/charts on screen the moment it opens
     4. Workbench  — drop files in, watch each one process
   ```

   This is a PRODUCT decision and it is theirs, not yours. Recommend one and say why in a single
   line — a monitor wants a dashboard, an ingester wants a workbench — then use what they choose.
   A default picked silently is how an agent that should have had a screen ends up as another
   chat box, and rebuilding it later means re-authoring `[app]`, `ui/` and the tool wiring.

   **Write down what they asked for, as a checklist** (`update_plan`), before you build anything.
   Every requirement they stated, one line each. You will verify against it in step 8, and you
   cannot verify against a memory of a conversation forty tool calls ago — that is how an agent
   delivers four of five requested things and reports success, having genuinely forgotten the
   fifth.

   Three more decisions, which ARE yours — answer them from the sections above, do not ask:
   - Does it **run on its own**? A monitor/tracker/reporter does: heartbeat + workspace
     snapshots + a skill for the routine. See "Design it as a MECHANISM".
   - Does it reach a **third-party service**? Then `[[mcp]]`, not your own tools — and
     `web_search` the service first rather than guessing what its API or MCP server offers.
   - What must the **user supply** — keys, URLs, a sign-in? Then `[[settings]]` / `[[oauth]]`.
1. `agents_list` — check the id is free and learn the agents directory path.
2. `create_agent` — scaffold `agent.toml` + `IDENTITY.md` and register it LIVE. Do this
   first; the agent is resolvable from this moment.
3. **Give it a window** — two ways, and the choice is about how much the window has to do.

   **`scaffold_ui`** copies a complete vanilla app (`chat-app` / `dashboard-app` /
   `workbench-app`); you then EDIT what it wrote. No build step, no Node. Right when the window
   IS one of those three things.

   **`scaffold_react_app`** writes a buildable project and **no source at all** — for a window
   that needs more than one view, or an artifact beside the chat, or panels driven by direct
   tool calls. You write `src/` yourself, **after reading `agents/samples/`** (step 3a).

   Either way: never hand-write `ui/` from nothing. The run-event payload is nested and streamed
   text is `message_update/text_delta`; a page that gets either wrong connects, logs nothing, and
   never updates the screen. And remember `[app]` in `agent.toml` — these tools write files, not
   configuration.

3a. **READ THE SAMPLES BEFORE YOU WRITE AN APP.** `agents/samples/` holds complete, working
   agents. Read **more than one**, always.

   They overlap where the platform has a right answer and differ where the product does, and the
   differences are the part worth thinking about — one may keep an artifact beside the
   conversation, another may put a queue in front of it. Neither is "the way". Say which you took
   from and why, in one line.

   **They are references, not a mould.** Take the mechanism, not the layout. Build what THIS
   agent needs, including things no sample has — that is the job, and a window that is a
   recoloured copy of a sample is a failure of it, not adherence to it.

   What to look for, because each of these is invisible until a user hits it:

   - **a turn is an ORDERED list of blocks.** Text, reasoning and tool calls interleave in the
     order they happened. Two parallel fields (`text` + `tools[]`) throw that order away and can
     only render "every tool, then every word" — a wall of tool names with unrelated sentences
     fused underneath.
   - **stream the reasoning.** `message_update` also carries `kind: 'thinking_delta'`. Without it
     a long research phase is a motionless list of tool names, which reads as a hang.
   - **`tool_progress` on the running row**, so a slow tool is distinguishable from a stuck one.
   - **wait for the socket before the first request.** A request sent while it is still opening
     rejects immediately with "not connected", and nothing retries — the panel renders empty and
     looks like a permission problem.
   - **render `agent_end.error` and `model_fallback`.** The provider's own words are the only
     thing that separates a dead key from a rate limit from an empty balance. Drop them and every
     failure looks like the same shrug.
   - **never disable the composer while the agent works.** Offer Stop. The moment someone most
     needs to speak is mid-run.
   - **attachments**: drop, paste and pick. A pasted screenshot has no filename — name it from
     its mime type or it is stored with no extension and never reaches a vision model as an image.
   - **persist the session key**, restore history on load, and offer fork/rename/delete.
   - **a settings page generated from `[[settings]]`**, secrets write-only, and a test button that
     saves first.
   - **direct `invokeTool` panels** for anything that is a lookup rather than a question.
4. `write` — everything else: `AGENTS.md`, `skills/`, `plugins/`, data files.
   For a private tool prefer `create_tool` with `agent="<id>"` — it compile-checks the code
   and writes the plugin in the right shape for you.
5. **`validate_agent`** — always. It reports three classes of problem the daemon will not:
   things being silently ignored, things that only break at installer-build time, and tools
   that will not survive the sandbox. Fix every `[x]`, then call it again until clean.
6. **`reload_agent`** — after creating an agent, editing `agent.toml`, or adding a private
   plugin. NOT needed for skills or `ui/`: a SKILL.md is re-read every turn and `ui/` is
   served straight off disk, so both are live the moment you save.
7. **RUN IT. Then read what actually happened, fix, and run it again.**

   ```
   run_agent(agent_id='<id>', message='<something a real user would say>')
   ```

   One message on a fresh session. It returns the reply, **which tools the agent called**, and
   how the run ended.

   Use the TOOL, not a shell command. `agentd` is a console script that exists only where the
   wheel was pip-installed — in a source checkout, which is where agents are authored, there is
   nothing on PATH by that name, and hunting for one has cost a real build eleven `exec` calls.

   The tools line is the point. These two look identical in prose and are completely different:

   ```
   tools called: get_cost_snapshot, compare_thresholds     <- it did the work
   tools called: NONE                                      <- it described the work
   ```

   Ask it two or three things a real user would ask. Then LOOK at the answer:
   - Did it call the tools that fetch data, or just talk about them?
   - Is the answer real, or a plausible-sounding placeholder?
   - When it fails, the reason comes back in the agent's own words — a missing key, an
     unconnected server, a crashing tool. Fix it and run again.

   **Do not skip this and do not declare an agent finished without it.** Everything before this
   step checks that the agent is well-FORMED; this is the only step that checks it WORKS.
   `validate_agent` cannot tell you an agent is useless, and an agent that has never run once is
   exactly the agent that turns out to be empty when the user opens it.

7b. **OPEN THE WINDOW. `verify_app` — every time the agent has a `ui/`.**

   ```
   verify_app(agent_id='<id>')
   ```

   Step 7 proved the agent's BRAIN works. This is the only step that looks at its SCREEN, and a
   screen is the part that can be perfectly built, perfectly served, and blank. Every failure it
   catches looks like success from your side: the build printed no errors and the files are on
   disk.

   It reports assets that 404, a crash on mount, console errors, a page that rendered nothing, a
   socket that never opened, a layout that overflows — and it REFUSES if `app/src` is newer than
   `ui/`, because otherwise you are checking the previous build.

   **Then drive what you actually built.** The generic checks cannot know what THIS agent is for:

   ```
   verify_app(agent_id='<id>', steps=[{action: 'click', target: 'Refresh'}])
   verify_app(agent_id='<id>', steps=[{action: 'type', target: 'Ask anything', text: 'hello'},
                                      {action: 'press', target: 'Enter'}])
   ```

   Target the VISIBLE TEXT of a control, not a selector. Most windows are fine until you touch
   them — the handler that throws only throws on click — so a verification that never interacts
   reports a healthy page with a dead button.

   **LOOK AT THE SCREENSHOTS it returns.** Passing every check and being unusable are entirely
   compatible: overlapping text, a panel off screen, a control with no label. The image is the
   only thing that shows the difference, and you can see it.

   Fix, verify, repeat. **Do not tell the user an agent with a window is finished while this
   still reports errors.**

8. **Show the user what you built, against what they ASKED for.**

   Walk the checklist you wrote in step 0 — every requirement they gave, and whether it is done.
   Not a summary of what you built: a comparison. An agent that does four of the five things
   asked for reads as finished unless somebody checks the fifth, and you are the only one who
   can, because you are the only one who saw all five.

   Then name the two or three decisions you took that they might disagree with — the shape of
   the window, what it stores, what it does on a schedule — and ask whether that is what they
   wanted, BEFORE calling it done. You had to guess at something; say which thing.
9. **`package_agent`** — only when they want to SHARE it. Produces the `.agentpkg`. It
   re-validates first and refuses on errors, so a broken agent never reaches anyone else.

## Rules that always apply

These hold at every step above.

- **Never invent a config key.** If a knob is not in this document, read an existing
  agent's `agent.toml` and copy the shape, or ask.
- One concern per file. Identity in IDENTITY.md, rules in AGENTS.md, procedures in skills.
- Prefer a **skill** (markdown, no code) over a tool. Reach for a private plugin only when
  a genuinely new capability is needed.
- Keep `[tools] allow` tight when the agent's job is narrow — it reduces mistakes and cost.
- After creating an agent, state exactly which files you wrote and where.

---

## Where you may write

**Inside the agent you are authoring, and nowhere else.** This is enforced — `write`, `edit`,
`create_agent`, `create_tool` and `scaffold_ui` all refuse a path outside it, so this is not a
guideline you weigh against convenience.

| | |
| --- | --- |
| `agents/<id>/` for an agent you are building | yes — this is the job |
| the shared `plugins/` directory | **no** |
| your own definition, skill, `agent.toml`, or workspace | **no** |
| an agent that was INSTALLED from a package | **no** |
| anywhere else on the disk | **no** |

Reading: on a desktop daemon you may read anything you need. On a HOSTED daemon every run is
fenced to a positive read grant — its own workspace, the serving agent's definition, and the
shared catalog/plugin dirs; other users' files simply do not exist for it. You author on
desktop, but the agents you BUILD must live inside that fence — see "Design for hosted"
below.

Three of those deserve their reason, because each looks like an obstacle until you know it:

**The shared `plugins/` directory.** A tool written there becomes part of the product for every
agent on the machine, and — unlike an agent's own tools — it is never sandboxed on a machine that
installs it. That makes it the one place a capability refused to a private tool could be
reintroduced. Creating one may be exactly right, but it is the USER's call: describe what you
want and why, and let them decide.

**Your own files.** An agent that can rewrite its own rules is an agent with no rules. If a
constraint here is wrong, say so — do not edit it.

**An installed agent.** Editing it leaves it no longer matching what its publisher shipped while
still carrying their name. Build your own instead.

**Do not look for a way around this.** `exec` runs a shell and is not covered by the same check;
using it to write where `write` refused is defeating a deliberate boundary, not solving a
problem. If you genuinely need a path outside your scope, that is a conversation with the user.

## Where agents live

The agents directory is `config.agents_dir`:

| mode            | path                |
| --------------- | ------------------- |
| repo checkout   | `<repo>/v2/agents/` |
| installed build | `~/.agentd/agents/` |

If you are unsure which applies, call `agents_list` — or read an existing agent's path
from `agents.detail`. **Never hardcode one of these.** Derive it from an existing agent.

## Directory layout

```
agents/<id>/
├── agent.toml            REQUIRED  identity + configuration
├── IDENTITY.md           REQUIRED  who the agent is
├── AGENTS.md             optional  operating rules / red lines
├── HEARTBEAT.md          optional  checklist for autonomous ticks
├── presentation.json     DO NOT AUTHOR — the daemon generates it
├── skills/<name>/SKILL.md          playbooks this agent alone sees
├── plugins/<pid>/                  private tools (plugin.toml + module)
├── templates/ (or any dir)         plain data files the agent reads
├── ui/                             its own app UI (needs [app])
└── workspace/            runtime user files — NEVER packaged
```

`<id>` is kebab-case (`note-taker`, `support-bot`). It is the agent's permanent
identity: the session key, the app URL, and the installer name all derive from it.

## agent.toml

**TOML rule: every top-level key MUST appear before the first `[table]`.** Emitting
`name` after `[app]` silently puts it inside that table and the agent loads wrong.

```toml
# top-level keys FIRST
name = "Note Taker"                # display name (required)
version = "1.0.0"                  # bump on every change you ship — installs supersede by version
description = "One line: what this agent is for."   # shown to orchestrators choosing a delegate
model = "gemini/gemini-2.5-pro"    # optional per-agent brain override
heartbeat = "30m"                  # optional; autonomous self-wake interval
audience = "external"              # optional; applies the safe-to-send privacy gate to replies
google_account = "me@example.com"  # optional; the ONE Google account this agent acts as
requires_local = false             # optional; true = a HOSTED daemon never offers this agent

# DISPLAY fields are TOP-LEVEL, not inside [app]. The registry reads them via
# data.get(...), falling back to the generated presentation.json sidecar. Putting
# them under [app] means they are SILENTLY IGNORED.
tagline = "notes · summaries"      # short picker line
color = "#2E7D32"                  # avatar/dot colour (hex)
suggestions = [                    # max 3 kept; shown on an empty chat
  "Summarize my spending this month",
  "Top 5 merchants by spend",
]

# ---- its own app window (omit entirely for a chat-only agent) ----
# This table accepts EXACTLY these five keys — nothing else is read.
[app]
title = "Note Taker"               # defaults to `name`, then the id
mode = "window"                    # "window" = chromeless product window | "browser" = a tab
entry = "ui/index.html"            # optional; default shown
public = false                     # hosted daemons only: allow tokenless visitors
public_tools = ["lookup_entry"]    # what those visitors may invoke (never chat)
# icon = "icon.ico"                # read by the INSTALLER build only, not the daemon

# ---- how this agent reaches people once published (omit for the default: exe only) ----
# ASK the user which they want before publishing an app agent — this is a product decision.
[delivery]
web = false                        # true = the marketplace card gets "Open in browser": the
                                   # hosted platform serves the app at /apps/<id>/, nothing to
                                   # install. Requires [app]. Opt-in, never assumed.
exe = true                         # false = skip the standalone Windows installer

# ---- tool scope (omit BOTH keys to grant the full catalog) ----
[tools]
allow = ["read", "write", "exec", "process", "report_outcome"]
deny  = ["computer_use"]           # deny always wins
# `exec` and `process` are a PAIR — see "Long-running work" below. validate_agent reports it
# if you grant one without the other.

# ---- write scope (omit for almost every agent — the default is right) ----
# Where this agent's fs tools may WRITE, beyond the platform's own boundaries. Tokens:
# <agent_dir> = this agent's own folder; <agents_dir> = every agents root (one entry per
# root). An unknown/misspelled token DROPS the entry — a typo narrows, never widens.
# deny beats allow; the platform's rules (tenant clamp, installed-agent protection) always
# apply on top and cannot be widened from here.
#
# DO NOT grant beyond <agent_dir> in an agent you intend to ship: validate_agent flags it
# (WIDE_WRITE_ROOTS), packaging and publishing refuse it, and on any machine that installs
# the agent the runtime clamps the scope to its own folder anyway. Wide roots are for
# LOCAL authoring agents (Agent Builder itself writes every agent root and denies its own).
# [tools.fs]
# write_roots = ["<agent_dir>"]
# deny = ["<agent_dir>/agent.toml"]

# ---- skill scope (omit to inherit every global skill) ----
# skills = ["monthly-report"]      # top-level key, not a table — keep it above [app]

# ---- per-agent toggles ----
[capabilities]
autonomy = true                    # may schedule cron + wake on a heartbeat
notify   = true                    # may push notifications to the user
channels = false                   # may be reached over a messaging channel

# ---- delegation scope ----
[subagents]
allow = ["check-*", "researcher"]  # ids/globs this agent may delegate to

# ---- PER-AGENT PLUGIN/TOOL WIRING — the most powerful section ----
# Shape: [plugins.<plugin-id>.tools.<tool-name>], then any knob that tool reads.
# This is how an agent tunes shared tools WITHOUT touching global config.
# agent.toml ALWAYS wins over agentd.config.json.
[plugins.<plugin-id>.tools.<tool-name>]
provider = "gemini"
model = "gemini/gemini-3-pro-image"
resolution = "1K"                  # whatever knobs THAT tool documents

[plugins.<other-plugin>.tools.<other-tool>]
model = "gemini/gemini-2.5-flash"

# ---- what this agent needs from whoever RUNS it (omit if it needs nothing) ----
# The declaration ships. The values never do — see "[[settings]]" below.
[[settings]]
key      = "ACME_API_KEY"
label    = "Acme API key"
kind     = "secret"                # secret | text | url
required = true
help     = "Read-only key from Settings → API in Acme."

# ---- MCP servers this agent brings with it (omit if it needs none) ----
# Declared HERE, not with add_mcp: agent.toml travels in the package, agentd.config.json does not.
# See the connect-mcp skill.
[[mcp]]
name    = "acme"                                 # tool namespace -> acme__*
command = ["uvx", "acme-mcp-server@latest"]      # stdio; OR url = "https://api.acme.com/mcp"
env     = { ACME_API_KEY = "${ACME_API_KEY}" }   # ${…} names a [[settings]] key above
```

An agent's private tools (in `plugins/` below) are **implicitly allowed** and never need
naming in `[tools] allow`.

### `[[settings]]` — what the agent needs from whoever runs it

An API key, a database URL, an endpoint. Anything that differs per person and that **you must not
know**. Declare it and the daemon does the rest: the agent's settings page grows a field for it,
and whatever the user types lands in the `.env` on **their** machine.

```toml
[[settings]]
key      = "COINBASE_API_KEY"      # the ENV VAR NAME — this is what your code reads
label    = "Coinbase API key"      # what the settings page calls it; defaults to `key`
kind     = "secret"                # secret | text | url. Unknown kinds fall back to text.
required = true                    # the page says "required — not set yet" until it is filled
help     = "Read-only key from Settings → API in Coinbase."

[[settings]]
key   = "TRADING_DB_URL"
label = "Database URL"
kind  = "url"
```

**The declaration ships; the value never does.** `agent.toml` travels inside the package, so every
installer sees the field. `.env` is excluded from packaging and always was. A downloader opens the
settings page, sees empty fields, and fills in their own.

**Each agent's values are its own.** On disk they are stored as `<agent-id>__<KEY>`, so two agents
that both declare `AWS_ACCESS_KEY_ID` hold two different accounts instead of overwriting each
other. You never write the prefix and never read it: you declare `AWS_ACCESS_KEY_ID`, your code
reads `${AWS_ACCESS_KEY_ID}`, and the daemon maps between them. It is only worth knowing so the
line in `.env` doesn't surprise you. Provider keys (`ANTHROPIC_API_KEY` and friends) are NOT
prefixed — those are one machine-wide credential shared by every agent, by design.

So: **never put a key IN `agent.toml`.** That file is the packaged one. Writing
`api_key = "sk-live-…"` there ships your credential to everyone who installs the agent.

**A declaration is also a permission.** `config.set` will write the provider keys and the names
this agent declared — nothing else. An undeclared name is refused with an error naming it. That is
why a field that does not work is almost always a field you forgot to declare.

**`kind = "secret"` is write-only in the UI.** The user sees "•••• saved" and can replace it, never
read it back — the same rule the provider keys already follow inside an installed agent, for the
same reason. `text` and `url` values ARE shown, so a typo is fixable.

Reading the value in a private tool: it is an ordinary environment variable, so
`[sandbox] secrets = ["COINBASE_API_KEY"]` in that plugin's `plugin.toml` and the
`${COINBASE_API_KEY}` placeholder — see "Calling an external API from a private tool".

**Custom settings make an agent local-only for now.** The values live in the daemon's `.env`, and a
hosted daemon has one `.env` shared by every account — one user's key would become everyone's.
Until per-account secrets exist, an agent with `[[settings]]` is for a local install. Say so when
you build one.

### `[[mcp]]` — servers the agent brings with it

A whole toolset somebody else wrote. Declare it in `agent.toml` so it survives publishing, wire
its credentials to `[[settings]]` keys, and **read the `connect-mcp` skill before you do** — it
covers public vs private servers, why `add_mcp` is not the answer, and how to verify a connection
instead of assuming one. The short version:

- `${NAME}` in an `[[mcp]]` block must be a `[[settings]]` key of the same agent. The daemon
  refuses to connect a server whose referenced settings are empty, so an agent can never silently
  run on the daemon's own credentials.
- Never inline a real credential — `agent.toml` ships.
- A `command` server launches a process on the user's machine, so they approve the exact command
  once on the settings page. A `url` server needs no approval.
- Check `mcp.status` and name the tools back. A server that connects and exposes nothing looks
  identical to one that worked.

### `[[oauth]]` — services the user signs in to

Most services worth connecting to have no API key to paste: you sign in, and they hand back a
token that expires. Declare that too.

```toml
[[oauth]]
name   = "myhealth"
server = "https://api.myhealth.app"     # endpoints DISCOVERED from here (RFC 8414)
scopes = ["read:records"]
# a classic provider (Google, Notion, Coinbase) needs an app registered up front, so instead:
# authorize_url = "https://accounts.example.com/o/oauth2/auth"
# token_url     = "https://oauth2.example.com/token"
# client_id     = "${MYHEALTH_CLIENT_ID}"       <- a [[settings]] key, so the INSTALLER
# client_secret = "${MYHEALTH_CLIENT_SECRET}"      registers their own app
```

Then either wire it to an MCP server — `auth = "oauth:myhealth"` in the `[[mcp]]` block, instead
of a `headers` line — or use it from a private tool:
`headers={"Authorization": "Bearer ${oauth:myhealth}"}`. Both read the same connection, so the
user signs in once.

**Never hard-code a `client_id`/`client_secret`.** They identify YOUR registered app; shipping
them means every installer's sign-ins run through your app. Declare them as `[[settings]]`.

The tokens are per agent and per machine, stored under `<state_dir>/oauth/`. They are never
packaged, and — say this plainly if a user asks — they are stored unencrypted, the same as every
other credential the daemon holds.

### Read the tool catalog before you choose

**`<state_dir>/tools.json`** lists every tool this machine has — name, one line, where it came
from. Read it before filling in `[tools] allow`, and before writing a private tool.

It is generated by the daemon on boot and on every change, so it is what actually exists here,
not what existed when someone wrote a list down. Find `state_dir` from `config.get` or from an
existing agent's paths; do not hardcode it.

Two reasons this matters more than it sounds:

- **You cannot see these tools yourself.** Your own `[tools] allow` is what YOU may call. It is
  a much smaller set than what you may GRANT, and nothing else shows you the difference.
- **A tool that already exists beats one you write.** A private tool is untrusted on the machine
  that installs the agent and carries every restriction in this document. A shared tool has none
  of that. Check the catalog first; write a private tool only for something genuinely absent.

### `requires_local` — when an agent must not run on a shared server

The same daemon runs on a desktop (one owner, their own machine) and hosted (many strangers, one
container). An agent that runs a shell, writes outside its workspace, or loads code into the
process is the owner exercising their own computer in the first case and a visitor reaching into
everyone else's files in the second.

Set `requires_local = true` when the agent needs any of that. A hosted daemon then does not offer
it **at all** — absent from the roster, unresolvable, no app served, its private tools never
discovered. Nothing to bypass, because there is nothing there.

Use it for an agent that needs `exec`, unsandboxed `write`, or its own code-loading tools. Do NOT
use it as a general precaution: an agent that only chats, reads its workspace, and calls models is
fine hosted, and marking it local-only just hides it from half your users. Agent Builder itself
declares it, for exactly the reason above.

### Design for hosted — the four facts that shape a shippable agent

An agent published with `[delivery] web = true` (and any agent someone installs on a shared
server) runs behind the tenant fence. Four facts, each enforced by the runtime, decide what
you may design around:

1. **Reads are a positive grant.** A hosted run sees: its own per-user workspace, THIS
   agent's definition dirs (`templates/`, `skills/`, `plugins/`, `ui/`, data dirs), and the
   shared catalog. Nothing else — no other agents' folders, no absolute machine paths, no
   other users' files, never any `sessions/`. Design every file access inside that set.
2. **There is no shell.** Every hosted run refuses `exec` (a subprocess cannot be confined
   to one tenant's files). Everything must be expressible with read/write/edit/ls/find +
   plugin tools. If the job truly needs a shell, it is a `requires_local` agent — and then
   `web = true` is a contradiction (validate_agent flags both).
3. **`workspace/` is per-user and starts EMPTY.** Every signed-in user gets their own,
   blank. Anything the agent NEEDS at runtime ships in a definition dir and is read from
   there in place; seed a user's workspace by copying on first use, in-turn, if you must.
4. **Writes are clamped to the caller's own space.** The platform clamps every hosted write
   to the user's own account subtree; an installed agent's declared `write_roots` are
   additionally clamped to its own folder. An agent that "organises the user's disk" is a
   desktop product, not a web one.

None of this needs a mode check in anything you author — the same agent runs on desktop
with no fence at all. Design within the fence and the agent behaves identically everywhere.

### Long-running work — grant `exec` and `process` together

An agent has no timer and does not run between turns. Nothing can wake it up. So there are
exactly two ways to handle something slow — a download, a render, a training run:

```
BAD    exec("sleep 90; check")     blocks the whole turn, shows no output for 90s,
                                    and dies at the exec timeout
GOOD   exec(command=..., background=true)   -> returns a session id, immediately
       process(action="poll", session_id=...) -> "[running] …new output" | "[exited(0)]"
```

`exec`'s own description points the model at `process`. **If you grant `exec`, grant
`process`** — otherwise `background=true` hands back a session id that nothing can read, and
the agent's only remaining option is to block a turn on a sleep.

This is not theoretical. An agent built here was given `exec` without `process`, then had to
babysit a 20GB download: it ran `Start-Sleep -Seconds 90; ssh …` over and over, showed the
user nothing for 90 seconds at a time, and tripped the runtime's "you are repeating yourself"
nudge. It was reasoning correctly about a toolbox missing half a pair.

**And write the rule into the agent's own `AGENTS.md`**, not just here. This skill is read
while you BUILD; the agent's AGENTS.md is present on every turn it ever takes. Something like:

> Long jobs: start them with `exec(background=true)` and poll with `process`. Never `sleep`
> inside a foreground `exec` — it blocks the turn and shows the user nothing.

**Polling still costs a turn.** Nothing arrives on its own, so between polls the agent should
do useful work, not spin. If a job runs for hours, the right shape is usually a `cron` or
`heartbeat` run that checks and reports, not one conversation held open.

## The markdown files

Three files, three distinct jobs. Do not merge them.

- **IDENTITY.md** — _who it is._ Role, voice, boundaries. Injected every turn. Keep it short.
- **AGENTS.md** — _how it operates._ Numbered hard rules, data locations, output format,
  red lines. This is where behaviour actually gets specified; be concrete and testable.
- **HEARTBEAT.md** — _what to check on an autonomous tick._ Only injected on heartbeat runs.
  Requires `heartbeat` + `[capabilities] autonomy = true`.

Never author `presentation.json` — the daemon fills in tagline/suggestions itself.

## Design it as a MECHANISM, not a chat box with tools

The most common bad agent is one that owns a subject and does nothing until asked. It waits, then
works out from scratch how to fetch what it needs, then answers, then forgets. Everything it
learned is gone by the next message, and the same reasoning happens again.

A real agent has **standing machinery**: it runs on its own, keeps what it found, and compares
today against yesterday. Three questions decide the whole design:

**Does it run on its own?** Anything called a monitor, tracker, watcher, digest or report does.
Give it `heartbeat` + `[capabilities] autonomy = true`, and a `HEARTBEAT.md` that says exactly
what one tick does. A cost monitor with no heartbeat is not a cost monitor; it is a chat box that
knows about billing.

**Does it need to remember?** Anything that reports change does — "up 20% on last week" is
impossible without last week. Write each run's result to its own `workspace/` as a dated file or
a small JSON, and read the previous one back. Thresholds, baselines and last-seen markers all
live there. An agent that stores nothing can only ever describe NOW.

**Does it repeat a procedure?** Then write it as a `skills/<name>/SKILL.md` — the fetch, the
shape of the data, the comparison, what counts as worth reporting. A procedure left implicit is
re-derived every turn, differently each time. That is also why two runs of the same agent can
disagree about the same numbers.

```
monitor / tracker / watcher   heartbeat + workspace snapshots + a skill for the routine
assistant / helper            tools + skills, on demand — no heartbeat
ingester                      a workbench UI + a skill for the per-item procedure
reporter                      heartbeat + snapshots + a dashboard reading THOSE snapshots
```

The dashboard point matters: a dashboard should render **stored state**, not fire a live fetch
every time someone opens the window. That is what makes it instant, and what lets it show a
trend at all.

**Research before you design.** Use `web_search` / `web_fetch` to read the actual API or MCP
server you are about to integrate — what it exposes, what it needs, what its rate limits are.
Do not build against a remembered API shape: name the tools you found, and if you could not find
them, say so rather than guessing a package name.

## When you are blocked, say so in one line and ask

Check anything you CAN check — your own files, your tools, your workspace — and answer from that.
But when the cause is somewhere you cannot see (daemon state, a tool that simply is not there,
another agent's setup), name what is missing in one sentence and ask the user.

**Do not diagnose a system you cannot inspect.** An explanation you have no way to verify reads
like an answer and is not one — and it is worse than silence when it sends the user to fix
something that was never broken. Every agent you build should follow this rule too; put it in its
`AGENTS.md` in your own words.

## skills/ — playbooks

`agents/<id>/skills/<skill-name>/SKILL.md`. An agent sees the global library
(`agents/main/skills/`) **plus** its own; a same-named own skill overrides the global one.

```markdown
---
name: monthly-report
description: Use when the user asks for a monthly spending summary or chart.
always: false
requires_bins: ffmpeg # optional gates — skill hidden unless satisfied
requires_env: SOME_API_KEY
requires_config: memory_enabled
---

# Monthly report

1. Read every CSV under bank/ and cards/.
2. Dedupe on (date, amount, merchant).
   ...
```

`always: true` inlines the full body **every turn** — use only for short routing rules.
Everything else stays `false` and is read on demand. Write the description as a
_trigger condition_ ("Use when…"), because that line is all the model sees before choosing.

**Skills are re-read every turn.** A new SKILL.md takes effect on the next message — no
reload, no restart.

## plugins/ — the agent's own tools

### FIRST: does this tool already exist as an MCP server?

**Before writing a tool that calls a third-party service — AWS, GitHub, Notion, Slack, Stripe,
a database, anything with an API — check whether an MCP server already exists for it.** Nearly
all of them do, written and maintained by that service or its community, and one `[[mcp]]` block
gets you the whole toolset.

| the tool would… | do this |
|---|---|
| call a third-party API | **`[[mcp]]`** — see the `connect-mcp` skill |
| run local logic: parse, compute, transform, read the workspace | write it here |
| drive something only this machine has | write it here |

Writing your own wrapper around a public API is the slow, brittle path AND it usually does not
work: a private tool is SANDBOXED once someone installs the agent, so it has no network and no
credentials unless you declare them, and an SDK like `boto3` cannot function inside that box at
all. If you find yourself writing placeholder tools and explaining the sandbox to the user, stop
— that is this decision arriving too late.

`[[mcp]]` also travels with the agent; a private tool you wrote for a public API is a maintenance
burden you now own on every machine that installs it.

### The format

`agents/<id>/plugins/<plugin-id>/`. Same format as a global plugin, but visible only to
this agent. Two files minimum:

**`plugin.toml`**

```toml
id = "example-kit"
name = "Example Kit"
kind = "native"                  # "native" | "mcp"
entry = "example_kit:register"   # "<module>:<callable>" — module is in THIS folder
# description = "..."            # optional one-liner
# scripts = ["helper.py"]        # optional declared helper files
# data    = ["table.json"]       # optional declared data files
# [requires]                     # gate: plugin is SKIPPED unless satisfied
# bins = ["ffmpeg"]              # binaries that must be on PATH
# env  = ["SOME_API_KEY"]        # env vars that must be set
```

**`example_kit.py`** — the plugin folder is added to `sys.path`, so import siblings by
bare module name.

```python
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class LookupEntryTool(Tool):
    name = "lookup_entry"                 # what the model calls
    label = "Lookup Entry"                # UI label
    default_retryable = True              # False for anything side-effecting
    description = "Look one entry up by name. Use when asked about a stored item."
    parameters = {
        "type": "object",
        "required": ["city"],
        "properties": {"city": {"type": "string", "description": "City name"}},
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        city = params.get("city", "")
        try:
            ...
            return ToolResult.text(f"{city}: 21C, clear")
        except Exception as e:  # never let a tool crash the loop
            return ToolResult.text(f"lookup_entry failed: {e}", is_error=True)


def register(api, ctx):
    api.register_tool(LookupEntryTool())
```

### A tool NEVER works out where the workspace is — it asks

```python
from agent_runtime.application.run_context import current_workspace

folder = Path(current_workspace(".")) / "outputs"
```

**There is no fixed path to an agent's workspace, and the agent directory is not it.** The
runtime picks it per run: a signed-in user gets their own, a chat inside a project gets the
project's shared one, and a hosted deployment gives every account a separate one. Two runs of the
same agent can have two different workspaces, both correct.

So a path built from the agent's own directory — or from `__file__`, or from anything the model
saw in an `ls` — points at a folder that is real, writable, and **not the one anything else
reads**. The failure is silent and completely convincing: the write succeeds, the file is on
disk, and every tool and every panel that lists the workspace reports nothing there. Observed:
an agent wrote its output into its own definition folder, then spent three tool calls and a page
of reasoning working out why its own listing tool could not see a file it had just written.

The same rule applies to the app: `workspace.list` reads the run's workspace, so a file written
outside it is invisible in the window too.

**And when a tool finds nothing, it says WHERE it looked.**

```python
return ToolResult.text(f"No workflows in {folder}")     # ends the question
return ToolResult.text("No workflows yet")              # starts an investigation
```

An empty result that names its folder turns exactly this class of bug into one line of output.
It costs nothing and it is the difference between the agent noticing a path mismatch and the
agent theorising about one.

### An agent's own tools become UNTRUSTED once someone installs the agent

Trust is decided by **provenance**: an agent's private tools are classified
`THIRD_PARTY_BUNDLE` when the marketplace ledger says that agent arrived in a `.agentpkg`.
Locally — an agent you just authored, or one that shipped with the product — the tools are
trusted. Owning tools is not itself suspicious.

The catch is what happens on **someone else's machine**. Installing your agent records it in
their ledger, so every tool under `agents/<id>/plugins/` is untrusted over there. When
sandboxing is enabled (`AGENTD_SANDBOX_PLUGINS=1`), such a tool is granted:

|          |                                                            |
| -------- | ---------------------------------------------------------- |
| files    | the run's workspace only                                   |
| network  | no socket — outbound goes **through the host**, to declared hosts only |
| secrets  | **`{}` — always.** It never sees a provider key.           |
| models   | only if the tool declares `needs_model = True` — see below |
| **processes** | **never, and there is no way to ask.** `subprocess.Popen`, `os.system`, `os.exec*` are denied outright |

**Spawning is the one with no way out.** Network, secrets and models all invert — the plugin
asks, the host performs. There is no equivalent for "run this program": an HTTP request has a
shape you can send over a pipe and get an answer back; "launch a program on the user's machine"
does not.

So a private tool that needs to start a process **cannot exist**. Two ways forward, and the
second is the one worth checking first:

1. the work belongs to a SHARED tool — those are the daemon's own code and are never sandboxed
2. **the capability you want may already be a shared tool.** Before writing any private tool,
   look at what the catalog already offers. A private tool is for something no shared tool
   does; writing one that duplicates an existing tool costs you the sandbox for no gain.

The rule that follows: **a private plugin never opens a socket and never reads a key.** It asks
the host to do both. `import requests` and `os.environ[...]` work perfectly for the author and are
dead for every buyer — so `create_tool` refuses to write either into an agent-scoped tool. That is
not style; it is the difference between a tool that ships and one that is dead on arrival.

### Calling an external API from a private tool

Declare the hosts and the credential NAMES in the plugin's own `plugin.toml`:

```toml
[sandbox]
net     = ["api.acme.com"]     # or "*.acme.com" for subdomains
secrets = ["ACME_API_KEY"]     # names only — the value never reaches the plugin
```

Then call it:

```python
from agent_runtime.infrastructure.net.outbound import fetch

res = fetch(
    "https://api.acme.com/v1/things",
    headers={"Authorization": "Bearer ${ACME_API_KEY}"},
)
if not res.ok:
    return ToolResult.text(f"acme failed ({res.status}): {res.error or res.text}", is_error=True)
data = res.json()
```

`${ACME_API_KEY}` is a **placeholder, not a value**. The host substitutes it at the last moment
and makes the request itself, so the plugin cannot read the key, keep it, or send it anywhere it
did not declare. Unsandboxed the same call runs directly and reads the name from the environment —
one code path, both worlds.

`[sandbox]` is **not** `[requires]`. `[requires]` is a gate: _skip this plugin unless present_.
`[sandbox]` is a request: _when you box me in, leave these open_.

Three things to tell the user when you write one:

- The declaration is **public**. Anyone can read which hosts the agent contacts before installing
  it — write the narrowest list that works, never a wildcard you do not need.
- **Where the key comes from**: the daemon's environment, or the agent's own settings page writing
  `plugins.<plugin-id>.secrets.<NAME>`. That is how BYOK works inside a shipped agent.
- An operator can narrow the list further (`sandbox_net_allow` / `sandbox_net_deny`) and can never
  widen it. A host that is missing at runtime produces a refusal naming it, not silence.

If a plugin genuinely needs more than this, the person installing it can vouch for the whole agent
with `sandbox_trusted_agents = ["<agent-id>"]`. Never suggest that for code the user did not
author.

### Calling a model from a private tool

A sandboxed tool has no network and no keys, so it cannot reach a provider. It does not need to:
the call is **inverted** — the tool asks, and the host performs it. Use the one route that works
in every mode (hosted, BYOK, and a local model):

```python
from agent_runtime.infrastructure.llm.oneshot import text_complete   # or vision_complete

summary = text_complete(model=None, prompt=prompt, max_tokens=400)
```

Inside the sandbox that name resolves to a shim that hands the request to the host. Same
signature, same return type, so the tool behaves identically whether or not it is sandboxed.
`model=None` lets the host use the model this tool resolves to — pin one with
`[plugins.<pid>.tools.<tool>] model = "..."` in the agent's `agent.toml`.

The tool must also carry **`needs_model = True`** as a class attribute. That flag is the entire
authorisation: without it the sandbox grants zero models and the host refuses every call with
_"not granted"_. Add `model_kind = "vision"` too if it reads images.

`create_tool` sets both for you by reading the code — you never declare them. If you author a
plugin by hand with `write`, you must add them yourself, and `validate_agent` will tell you
(`UNTRUSTED_MODEL_UNDECLARED`) when you forget.

Spend goes to the account running the agent, attributed to your plugin, and is capped per tool
run (`sandbox_model_limits`: 8 calls, 4096 output tokens, 120s by default).

Today the sandbox flag is off by default and the shipped backend does not enforce the grant, so
nothing breaks immediately — but such a plugin will break the day real isolation lands, and on the
machine of anyone who installs the agent.

### Python library dependencies

There is **no pip field in `plugin.toml`**. `[requires]` covers binaries and env vars only.
For a third-party Python package, import it **lazily inside the function** and fail with
guidance, so the plugin still loads when the package is absent:

```python
try:
    import pandas as pd
except ImportError:
    return ToolResult.text(
        "this tool needs `pandas` — install it into the agentd runtime", is_error=True
    )
```

(A plugin distributed _as_ a pip package is a different thing, declared at the bundle level
with `source = "pip"` — not applicable to an agent-private plugin.)

**New private plugins are discovered at startup.** After writing one, call `reload_agent`
if it exists; otherwise tell the user a restart is needed.

## ui/ — the agent's own app

Requires an `[app]` section. Served by the daemon at `/apps/<id>/` on the **same origin as
the WebSocket**, straight from disk on every request — edit a file, reload the window, done.

### First decide the SHAPE. Chat is a default, not the answer.

Pick from what the agent DOES, not from what is easiest to scaffold:

| the agent… | the shape | what the window is |
|---|---|---|
| holds a conversation | **chat** | a thread and a composer |
| runs on its own and reports | **dashboard** | numbers, a chart, a table, a Refresh button |
| ingests a pile of things | **workbench** | a drop zone and a queue with per-item status |
| produces artifacts to review | **viewer** | the artifact, plus the two or three actions on it |

A trading monitor whose window is a chat box makes the user type "what's my P&L" to see a number
that should already be on the screen. A file-ingest agent whose window is a chat box makes them
describe files they could have dropped. **Chat is right when the work genuinely is a conversation
and wrong when it is a substitute for a control.**

Chat, dashboard and workbench are vanilla templates (`scaffold_ui(template='dashboard-app')`) —
copied whole, no build step. A viewer has no template, and neither does anything that mixes two
of these: for those, `scaffold_react_app` plus the samples, which is what the next section is
about.

### A window is not limited to chatting

These are the primitives. Everything a shape needs is here, and none of it requires a chat turn:

```
client.invokeTool(name, args)     run one of THIS agent's tools directly — no conversation,
                                  no model call, no tokens spent. The dashboard Refresh button.
client.request('workspace.upload', {...})   accept dropped files
client.request('workspace.list', {...})     + GET /file — read what the agent produced
client.request('config.get' | 'config.set') parameters, and the [[settings]] fields
client.on('chat.event', …)        live progress while something long runs
agentd.resultText(res)            a tool result -> the text to show
```

`invokeTool` is the one that changes what a window can be: a button that does the thing, in
milliseconds, with no model in the loop. Use chat for what needs judgement and tools for what
needs doing — most agents want both, in one window.

### A tool a WINDOW calls returns data, not just prose

```python
return ToolResult.text(
    f"{len(found)} workflow(s) in {folder}:\n" + rows,   # for the MODEL to reason about
    details={"folder": str(folder), "workflows": found},  # for the WINDOW to render
)
```

`invokeTool` gives the page back `{text, details, artifacts}`. **Read `details`. Never parse
`text`.**

A tool's text is a message written for a reader — it has a sentence, some bullets, a path in the
middle of a line. Scraping it works on the day you write the regex and breaks the day someone
rewords the sentence, and it breaks SILENTLY: the panel renders its empty state over a folder
with files in it, with nothing in the console and no error to search for. That is not a
hypothetical; it is where this paragraph came from.

Two rules that go with it:

- **`artifacts` is not a data channel.** It means "files THIS tool produced and wants shown to
  the user" — a listing tool that fills it can surface any file it happens to find.
- **A failed lookup is not an empty result.** `catch` around the call must not render "nothing
  here"; those are different answers and only one of them is about the workspace.

### A built app, when one of the three shapes is not enough

```
scaffold_react_app(agent_id='<id>')   →  app/ with package.json, vite.config.ts,
                                         tsconfig.json, index.html, vendor/ — and NO src/
```

The missing `src/` is the design. There is no single right shape for a window that does several
things, so instead of a fourth opinion you get the working agents under `agents/samples/` to
judge from. Read more than one, decide, then write it.

Two things about that project that only fail on somebody ELSE's machine, so check them before you
hand the agent over:

- **The SDK is vendored, not depended on.** `vendor/agentd-client.js` with an alias in
  `vite.config.ts` and a `paths` entry in `tsconfig.json`. The samples in this repo instead
  declare `"@agentd/client": "file:../../../../clients/sdk-js"` — correct where they live and
  broken everywhere else, because that path exists only inside this product's own tree. **If you
  copy a sample's `package.json`, delete that line.** The author never sees the failure; every
  recipient does.
- **`ui/` is the build output and `ui/` is what ships.** The daemon serves it and the packer
  takes what is on disk, so an unbuilt change is invisible however correct the source is. Run
  `npm install && npm run build` in `app/` before you call the agent finished, and again after
  every source change.

### Or `scaffold_ui`, for a plain window. Do not write these files by hand.

```
scaffold_ui(agent_id='<id>')                          →  chat app (default)
scaffold_ui(agent_id='<id>', template='dashboard-app') →  dashboard
scaffold_ui(agent_id='<id>', template='workbench-app') →  drop-zone + queue
```

It copies a chat app that already streams replies, shows live tool rows, takes pasted
screenshots, remembers conversations, **signs the user in on a hosted install**, and has a
settings page where the user pastes their own API key. Then you **edit** it — the title, the hero
text, the suggestions, the accent colour — and add whatever surface this particular agent needs.

### EVERY AGENT WITH A WINDOW SIGNS ITS USER IN. No exceptions.

`validate_agent` reports `UI_NO_SIGN_IN` as an **error** when an app never calls the gate, and
that error blocks both `package_agent` and `publish_agent`. An agent without it cannot ship.

One mechanism, and it is the SDK's:

```js
await agentd.mountSignInGate({ client })   // vanilla: before the socket is wired
```
```tsx
await mountSignInGate()                    // React: in main.tsx, before the first render
```

`scaffold_ui` writes it. `scaffold_react_app` writes `src/main.tsx` containing it — the one
source file it ships, because this is the one part of an app that is not a judgement call.

**Never write your own login form.** The gate's element ids (`gateEmail`, `gatePass`, `gateForm`)
are a contract the packaged-build login test drives, so a hand-rolled form silently disables it —
and a second login is a second implementation of credential handling, written by somebody who was
not thinking about credentials that day. It hits the same endpoints either way; the only thing a
custom form adds is a way to get it wrong.

It renders **nothing** when a stored session still works, and nothing on a build with no accounts
service — so it is correct to leave in an agent that will only ever run locally.

### Sign-in comes with the template — never hand-write a login

An agent that runs on platform keys needs the user signed in, or every model call fails. The
scaffolded `app.js` already calls `agentd.mountSignInGate({ client })` — BEFORE it wires the
connection, which is the one placement that also works for a web-delivered agent (see Sign-in
below) — and that is all a login takes. It lives in the SDK, so it is one implementation for
every agent.

It **shows nothing** unless a sign-in is genuinely needed — no `accountsUrl` (a BYOK install), keys
already live, or a stored session that still works. So it is correct to leave in place for an agent
that will only ever run locally; it costs one status call and renders nothing.

Theme it from `style.css` with `--gate-accent`, `--gate-card`, `--gate-fg` and friends. Never fork
its markup, and never rename `gateEmail` / `gatePass` / `gateForm` — the packaged-build login test
drives those ids.

For an agent that wants sign-in somewhere other than a modal, the mechanism is exposed directly:
`agentd.resolveAuth()`, `agentd.signIn({email, password, signup})`, `agentd.signOut()`.

Read the `ui/README.md` it writes before changing anything. It marks every spot to edit.

The rest of this section is what that app is built from — read it to modify the app, not to
retype it. Writing `ui/app.js` from scratch is the single most reliable way to ship an agent
that looks finished and does nothing: the two mistakes below are invisible at runtime, and
every hand-written app so far has made at least one of them.

```
ui/
├── index.html                  the entry (default "ui/index.html")
├── app.js
├── chat.js
├── settings.js
├── md.js
├── style.css
└── vendor/agentd-client.js     the SDK, verbatim — never edit or rewrite it
```

`index.html` loads the SDK as a plain script (it is an IIFE exposing a global `agentd`,
**not** an ES module):

```html
<script src="vendor/agentd-client.js"></script>
<script src="app.js"></script>
```

`app.js` connects with one line — the opener already put `token` and `scope` in the page URL:

```js
const client = agentd.fromPage({ clientName: "my-app/1" });

client.onStatus((s) => {
  /* 'connecting' | 'open' | 'closed' */
});
await client.send({ message, sessionKey });
await client.abort(sessionKey);
const { sessions } = await client.sessions();
const { messages } = await client.history(sessionKey);
const res = await client.invokeTool("my_tool", { arg: 1 });
const url = client.fileUrl(artifactPath); // authenticated URL for an artifact
```

**Pass no agent id.** The window is opened with `?scope=<agent-id>` and the daemon forces that
agent onto every request the page makes, so these calls are already about this agent. Writing
the id in the page just creates a second copy to keep in sync with `agent.toml`.

### Reading run events — THE PAYLOAD IS NESTED

`onRun` / `onAgent` hand you the whole `chat.event` push, not the event itself. The type you
switch on lives one level down, in `.event`:

```js
client.onRun(sessionKey, (payload) => {
  // payload = { sessionKey, runId, agentId, ts, event: { type, ... } }
  const ev = payload.event; // <-- NOT payload.type
  switch (ev.type) {
    case "message_update":
      if (ev.kind === "text_delta") append(ev.delta);
      else if (ev.kind === "thinking_delta") showThinking(ev.delta);
      break;
    case "tool_execution_start":
      startRow(ev.toolCallId, ev.toolName, ev.args);
      break;
    case "tool_execution_end":
      endRow(ev.toolCallId, ev.isError);
      break;
    case "agent_end":
      done(ev.stopReason, ev.error);
      break;
  }
});
```

Reading `payload.type` is the single most common way an agent UI ends up silently doing
nothing: every branch misses, the socket is fine, and the screen never updates.

### The events, and what each carries

Only these are part of the app contract. Anything not listed is internal and may change.

```text agentd:events
message_update       {kind: "text_delta"|"thinking_delta"|"toolcall", delta | toolCall}
tool_execution_start {toolCallId, toolName, args}
tool_progress        {toolCallId, toolName, text}
tool_execution_end   {toolCallId, toolName, result, isError}
message_end          {message}
model_fallback       {from, to, reason}
model_trace          {step, model, requestedModel, tokensIn, tokensOut, tokensCached}
continuation         {reason, attempt}
turn_start           {}
turn_end             {}
agent_start          {}
agent_end            {stopReason, error?}
```

Three that are easy to get wrong:

- **`turn_end` is NOT the end of the run.** One run has many turns — answer, call a tool,
  answer again. Settle the current bubble on `turn_end`; only go idle on **`agent_end`**.
- **`agent_end` carries the verdict.** `stopReason: "no_output"` means the run finished with
  nothing to show, and `error` explains why. Render it — a silent end looks like a hang.
- **`model_fallback` means the user is not talking to the model they configured.** Show it,
  or a failing primary model looks like your app being broken.

### What an app connection may call

```text agentd:app-methods
hello
chat.send
chat.abort
sessions.list
sessions.history
sessions.rename
sessions.delete
sessions.duplicate
agents.list
agents.detail
tools.list
tools.invoke
capabilities.list
plugins.catalog
workspace.list
workspace.mkdir
workspace.upload
workspace.delete
notifications.list
notifications.ack
config.get
config.set
mcp.status
mcp.approve
oauth.connect
oauth.status
oauth.disconnect
platform.status
platform.connect
platform.disconnect
```

`sessions.duplicate` copies a conversation and returns the new `sessionKey` — a **Fork** button.
It matters more than it sounds: a long thread is expensive to build, and without a fork the only
ways to try a different direction are to continue in it (losing the known-good state) or start
fresh (losing the context). Open the copy after forking — a fork the user does not land in is
indistinguishable from one that did nothing.

`config.get` / `config.set` ARE available, so an agent MAY offer its own settings screen —
that is how a shipped agent asks its user for their own API key (BYOK). One limit: for an
agent installed from a package, `config.get` omits the secret-bearing fields (`envValues`,
`raw`, `path`) — you can see _that_ a key is set and write a new one, never read one back.
Everything else — installs, projects, automation — is host-only and denied.

`mcp.status` reports the servers THIS agent declared in `[[mcp]]` — for each one its transport,
the exact command, which `${…}` settings it needs, the tools it is currently providing, and a
`problem` string when it is not up (a missing credential, or a command awaiting approval).
`mcp.approve {name}` records the user's consent to launch one stdio server's command. Both are
forced onto this agent, so a page can only ever see and approve its own servers.

`oauth.connect {name}` starts a sign-in and returns `authorizeUrl` — **the page opens it**, the
daemon does not. `oauth.status` says which declared connections are signed in and as whom;
`oauth.disconnect {name}` forgets the tokens. Same scoping rule: an agent's page can only see and
start its own.

`config.get` also returns this agent's own `[[settings]]`: `settings` (the declared fields),
`settingsValues` (the non-secret ones only), and a presence flag per key in `env`. `config.set`
accepts the provider keys and those declared names, nothing else — an undeclared name comes back
`{saved: false, error: "not writable from here: …"}`. The template's settings page already renders
all of it; you only declare the fields.

**WHOSE settings are they? Read `accountScoped`.** On a hosted daemon one machine serves many
people, so config is stored PER ACCOUNT: `config.get` answers with that user's own values (the
deployment's defaults, plus whatever they have overridden), and `config.set` writes only their
copy. Three fields tell your page what it may offer:

| field | meaning |
| --- | --- |
| `accountScoped` | `true` => these are the signed-in user's settings; a save reaches nobody else and needs no restart. `false` => a single-user install, where the config really is the machine's. |
| `machineOnly` | config keys the server owns (ports, paths, storage, sandbox). Render them read-only — a save that includes one is refused by name, not silently dropped. |
| `keysWritable` | `false` => provider keys and `[[settings]]` values cannot be saved here, because the `.env` is the machine's and shared. Hide the key fields rather than offering a save that will fail. |

Per-agent overrides are the useful half: `config.set {patch: {agents: {"<your-id>": {model: …}}}}`
sets the model YOUR agent runs on for THIS user, layered over the deployment's default. The daemon
forces the block to your own id, so you cannot configure another agent even by asking.

### Sign-in

`await agentd.mountSignInGate({ client })` is the whole thing. It draws a login only when this
daemon has an accounts service AND nobody is signed in; otherwise it returns immediately, so it is
safe to call unconditionally in every agent you build.

**Call it BEFORE the connection is wired, never inside a `status === 'open'` branch.** On a hosted
daemon the session token IS the socket credential, so a page opened from a marketplace link
(`/apps/<id>/`, no token in the url) cannot connect until somebody has signed in — a gate that
waits for the socket therefore never runs, and the page retries forever with nothing on screen.
The gate uses plain HTTP, so it needs no socket; passing `client` lets it rebuild the connection
once a session exists. The scaffolded `app.js` places it correctly (`AGENTD:SIGNIN`).

**There are no `auth.*` daemon methods.** The client signs in itself: it reads `accountsUrl` from
`platform.status` (also served over HTTP at `/platform/status`), POSTs to that service, keeps the
session, and reconnects presenting it. The daemon stores nothing — identity is a property of each
CONNECTION, which is what lets one daemon serve many people at once. There is likewise no
`auth.changed` event: each window owns its own session, so nothing broadcasts.

Do not confuse sign-in with `platform.*`. Those are the **billing** switch: whether model calls
run on platform keys or on the user's own. Signing in does not change who pays, and an agent on
the user's own API keys can still have users who log in. Treating those two as one question is
what previously made a login impossible on a local install.

Identity and run mode are machine-wide, so they can change in a window that is not yours — the
user signs out in another agent, or flips Local/Cloud in agentd. The daemon pushes this to every
connection when that happens. Without it a page keeps its own stale copy and goes on offering to
sign out of an account that is already gone. It never carries the token.

Plain HTML/CSS/JS needs no build. For React, build into `ui/` with Vite using
`base: './'` (an absolute base resolves outside `/apps/<id>/` and 404s).

## Packaging rules

Shipping is two steps, and only the first happens here:

```
agents/<id>/  --package_agent-->  <id>-<version>.agentpkg  --installer build-->  <id>.exe
```

The **`.agentpkg`** is the shareable unit: a zip holding `bundle.toml` + the whole agent
directory + any shared plugins it vendors. Anyone can install one (`marketplace.install`), and
it is what an `.exe` build consumes. `package_agent` produces it. Building the `.exe` itself
needs node + electron-builder + a repo checkout, so it is not a chat operation.

Author with packaging in mind:

- `workspace/`, `sessions/` and `clients/` are **excluded** from the package. Never put
  anything the agent _needs_ in `workspace/` — that is user data, and on upgrade it is the one
  directory preserved while the rest of the definition is replaced.
- The agent's own `plugins/` **are** included — they live inside the agent directory.
- Only agents with an `[app]` section can become a product exe.
- **Bump `version` in `agent.toml` on every shipped change.** It is the bundle's version, and
  installs supersede BY VERSION — re-packing the same number will not replace an existing copy.
- `bundle.toml` is optional and hand-written. Add one only for publisher-facing facts
  (`publisher`, `entitlement`, a bundle id that differs from the agent id, shared plugin
  dependencies). If it declares `version`, it **outranks** `agent.toml` — so normally leave
  that key out and let the agent's own version rule.
