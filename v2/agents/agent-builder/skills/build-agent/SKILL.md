---
name: build-agent
description: Use when the user asks to create, author, build, scaffold or extend an AGENT — including giving an agent its own tools, skills, app UI, or model wiring. The authoritative file-format reference for everything under agents/<id>/.
always: false
---

# Building an agent

**An agent is a directory.** There is no registration database and no build step — you
create files in the right places and the daemon reads them. This document is the format
reference; follow it exactly and a by-chat agent is byte-identical to a hand-authored one.

Author files with the `write` tool. Paths may be absolute; `write` is not sandboxed.

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

# ---- tool scope (omit BOTH keys to grant the full catalog) ----
[tools]
allow = ["read", "write", "exec", "process", "report_outcome"]
deny  = ["computer_use"]           # deny always wins
# `exec` and `process` are a PAIR — see "Long-running work" below. validate_agent reports it
# if you grant one without the other.

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
```

An agent's private tools (in `plugins/` below) are **implicitly allowed** and never need
naming in `[tools] allow`.

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

### An agent's own tools become UNTRUSTED once someone installs the agent

Trust is decided by **provenance**: an agent's private tools are classified
`THIRD_PARTY_BUNDLE` when the marketplace ledger says that agent arrived in a `.agentpkg`.
Locally — an agent you just authored, or one that shipped with the product — the tools are
trusted. Owning tools is not itself suspicious.

The catch is what happens on **someone else's machine**. Installing your agent records it in
their ledger, so every tool under `agents/<id>/plugins/` is untrusted over there. When
sandboxing is enabled (`AGENTD_SANDBOX_PLUGINS=1`), such a tool is granted:

|         |                                                            |
| ------- | ---------------------------------------------------------- |
| files   | the run's workspace only                                   |
| network | **none**                                                   |
| secrets | **`{}` — always.** It never sees a provider key.           |
| models  | only if the tool declares `needs_model = True` — see below |

So the question to ask while writing a private plugin is _"will this still work after someone
downloads it?"_:

- **Do not read API keys from the environment.** Take the value as a tool _parameter_, or use a
  shared tool that already owns that capability. A plugin that reads `os.environ` works
  perfectly for its author and silently reads nothing for every user — the worst failure shape.
- **Do not assume network access.**
- If it genuinely needs either, the person installing it can vouch for the agent:
  `sandbox_trusted_agents = ["<agent-id>"]` in their config. Never suggest that for code the
  user did not author.

`create_tool` **refuses** to write an agent-scoped tool that does either. That is not a style
preference it is enforcing — it is the difference between a tool that ships and one that is dead
on arrival.

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

### Start with `scaffold_ui`. Do not write these files by hand.

```
scaffold_ui(agent_id='<id>')      →  a complete, working app in agents/<id>/ui/
```

It copies a chat app that already streams replies, shows live tool rows, takes pasted
screenshots, remembers conversations, and has a settings page where the user pastes their own
API key. Then you **edit** it — the title, the hero text, the suggestions, the accent colour —
and add whatever surface this particular agent needs.

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
platform.status
platform.connect
platform.disconnect
```

`config.get` / `config.set` ARE available, so an agent MAY offer its own settings screen —
that is how a shipped agent asks its user for their own API key (BYOK). One limit: for an
agent installed from a package, `config.get` omits the secret-bearing fields (`envValues`,
`raw`, `path`) — you can see _that_ a key is set and write a new one, never read one back.
Everything else — installs, projects, automation — is host-only and denied.

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

## Order of work

1. `agents_list` — check the id is free and learn the agents directory path.
2. `create_agent` — scaffold `agent.toml` + `IDENTITY.md` and register it LIVE. Do this
   first; the agent is resolvable from this moment.
3. `write` — everything else: `AGENTS.md`, `skills/`, `plugins/`, data files, `ui/`.
   For a private tool prefer `create_tool` with `agent="<id>"` — it compile-checks the code
   and writes the plugin in the right shape for you.
4. **`validate_agent`** — always. It reports three classes of problem the daemon will not:
   things being silently ignored, things that only break at installer-build time, and tools
   that will not survive the sandbox. Fix every `[x]`, then call it again until clean.
5. **`reload_agent`** — after creating an agent, editing `agent.toml`, or adding a private
   plugin. NOT needed for skills or `ui/`: a SKILL.md is re-read every turn and `ui/` is
   served straight off disk, so both are live the moment you save.
6. Tell the user how to try it: a new chat with that agent, or its app window.
7. **`package_agent`** — only when they want to SHARE it. Produces the `.agentpkg`. It
   re-validates first and refuses on errors, so a broken agent never reaches anyone else.

## Rules

- **Never invent a config key.** If a knob is not in this document, read an existing
  agent's `agent.toml` and copy the shape, or ask.
- One concern per file. Identity in IDENTITY.md, rules in AGENTS.md, procedures in skills.
- Prefer a **skill** (markdown, no code) over a tool. Reach for a private plugin only when
  a genuinely new capability is needed.
- Keep `[tools] allow` tight when the agent's job is narrow — it reduces mistakes and cost.
- After creating an agent, state exactly which files you wrote and where.
