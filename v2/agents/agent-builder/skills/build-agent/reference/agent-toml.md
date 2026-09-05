# Reference — `agent.toml`, and where an agent lives

The whole file format: the directory layout, every key of `agent.toml`, and the four
declaration blocks an agent uses to ask for something from whoever runs it —
`[[settings]]`, `agent.config.json`, `[[mcp]]`, `[[oauth]]`.

Read this when you are writing or changing an `agent.toml`. The procedure is in `../SKILL.md`.

---

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

An agent's private tools (see `plugins.md`) are **implicitly allowed** and never need
naming in `[tools] allow`.

### `[[settings]]` — what the agent needs from whoever runs it

An API key, a database URL, an endpoint. Anything that differs per person and that **you must not
know**. Declare it and the daemon does the rest: the agent's settings page grows a field for it,
and whatever the user types is stored in **that agent's own** `agent.config.json`, on their
machine.

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

[[settings]]
key     = "MODEL"
label   = "Model"
kind    = "text"
default = "gpt-5.6-sol"            # the author's starting point — NON-SECRET fields only
help    = "Which model this agent thinks with."
```

**`default` is the one part of a setting whose VALUE ships.** Use it when the agent only makes
sense on a particular model, endpoint or mode: every installer starts there instead of on
whatever their daemon happens to run, and they can still change it — a default is a starting
point, not a ceiling. It is layered under whatever the user stores.

**Never on a `kind = "secret"`.** A value that travels to everyone who installs the agent is not
a secret; the validator refuses it (`SETTING_SECRET_DEFAULT`) and the runtime drops it.

**A setting that holds a HOST needs one more line** if a private tool calls it — declare it in
that plugin's `[sandbox] net` as `"${TRADING_DB_URL}"`, or the tool works for you and is refused
for everyone who installs the agent. See `plugins.md`, "A host the USER supplies".

**The declaration ships; the value never does.** `agent.toml` travels inside the package, so every
installer sees the field. `.env` is excluded from packaging and always was. A downloader opens the
settings page, sees empty fields, and fills in their own.

**Each agent's values are its own,** and they live in the agent's own file — one more reason two
agents that both declare `ACME_API_KEY` hold two different accounts rather than overwriting
each other. You declare `ACME_API_KEY`, your code reads `${ACME_API_KEY}`, and the
daemon does the rest.

They USED to be lines in the machine's shared `.env`, under a prefixed name
(`<agent-id>__ACME_API_KEY`) invented precisely because one file was being shared. You may
still see that prefix in a process environment — it is how the value reaches a sandboxed tool or
an `[[mcp]]` child, which can only read an environment variable — but it is transport, not
storage, and nothing asks you to type it.

Provider keys (`ANTHROPIC_API_KEY` and friends) DO still live in `.env`: those are one
machine-wide credential shared by every agent, by design.

So: **never put a key IN `agent.toml`.** That file is the packaged one. Writing
`api_key = "sk-live-…"` there ships your credential to everyone who installs the agent.

**And you may not need to ask at all.** `agent.config.json` is yours to write as the AUTHOR — a
default server URL, a model your agent needs to work — and it SHIPS with the agent, so whoever
installs it gets your choices instead of their daemon's defaults. Secret-kind values are stripped
when the package is built, so your own key cannot travel even if you filled the field in while
building. See "Config that ships with your agent" below.

**A declaration is also a permission.** `config.set` will write the provider keys and the names
this agent declared — nothing else. An undeclared name is refused with an error naming it. That is
why a field that does not work is almost always a field you forgot to declare.

**`kind = "secret"` is write-only in the UI.** The user sees "•••• saved" and can replace it, never
read it back — the same rule the provider keys already follow inside an installed agent, for the
same reason. `text` and `url` values ARE shown, so a typo is fixable.

**You do not build the form.** The shared settings page (`common/settings`, copied into every
agent) renders whatever is declared here, on its API Keys tab, with the write-only rule above
already applied. Adding a field is this block and nothing else. If the value needs PROVING — a URL
that must answer, a folder that must exist — add a Test button through `extras`; see "Custom
settings and the shared page" in `ui.md`.

Reading the value in a private tool: it is an ordinary environment variable, so
`[sandbox] secrets = ["COINBASE_API_KEY"]` in that plugin's `plugin.toml` and the
`${COINBASE_API_KEY}` placeholder — see "Calling an external API from a private tool".

**Custom settings still make an agent local-only for now.** The values are per-agent rather than
per-account, and a hosted daemon runs one copy of an agent for everybody — so one user's key would
still become everyone's. Moving them out of the shared `.env` fixed the collision BETWEEN agents,
not the one between accounts. Until per-account secrets exist, an agent with `[[settings]]` is for
a local install. Say so when you build one.

### `agent.config.json` — the config that ships WITH your agent

`[[settings]]` is what you ask the INSTALLER for. This is what you decide FOR them.

```json
{
  "user_editable": false,
  "model": "anthropic/claude-opus-5",
  "vision_model": "gemini/gemini-3.1-pro-preview",
  "max_turns": 40,
  "settings": { "COMFY_URL": "https://a-sensible-default" }
}
```

Written beside `agent.toml`, packaged with the agent, and it beats the daemon's own values key by
key. An agent that needs a vision model to be any use at all can say so once, instead of arriving
on a stranger's machine configured by that stranger.

**Three layers, and each belongs to somebody different:**

```
agents.<id>.*        the INSTALLER's, in their own agentd.config.json
agent.config.json    YOURS, shipped in the package
the daemon           everything neither of you decided
```

**`user_editable` is false by default,** which means the settings page shows your keys read-only
and the daemon refuses to write them. Set it true and the installer can change everything. Either
way they can ALWAYS fill in `[[settings]]` fields — their URL, their API key, their tokens. Those
are theirs by definition; only your run knobs are yours.

Locking covers only what you actually SET. "My agent needs Gemini for vision" must not also mean
"and you may not change the turn limit", so a knob you never touched stays theirs.

**Your secrets cannot travel.** The same file holds the values YOU typed into your own settings
page while building. On `package_agent` every value whose declared kind is `secret` is stripped,
and then the finished package is read back and the pack FAILS if any survived. You do not have to
remember anything.

**Only run knobs are honoured** — `model`, `vision_model`, `max_turns`, `reasoning_effort`,
`cost_efficiency`, `model_fallbacks`, `verify_tool`, `memory_enabled`. Anything else in the file is
a key nobody reads: an agent may not reconfigure the machine it lands on.

### `[[mcp]]` — servers the agent brings with it

A whole toolset somebody else wrote. Declare it in `agent.toml` so it survives publishing, wire
its credentials to `[[settings]]` keys, and **read the `connect-mcp` skill before you do** — it
covers public vs private servers, why `add_mcp` is not the answer, and how to verify a connection
instead of assuming one. The short version:

- `${NAME}` in an `[[mcp]]` block must be a `[[settings]]` key of the same agent. The daemon
  refuses to connect a server whose referenced settings are empty, so an agent can never silently
  run on the daemon's own credentials.
- Never inline a real credential — `agent.toml` ships.
- A `command` server launches a process on the user's machine on the agent's first run; a `url`
  server runs nothing locally. Neither asks the user's permission first.
- Check **`mcp_status`** and name the tools back. A server that connects and exposes nothing looks
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
  that installs the agent and carries every restriction in `plugins.md`. A shared tool has none
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

