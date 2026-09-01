---
name: build-agent
description: Use when the user asks to create, author, build, scaffold or extend an AGENT — including giving an agent its own tools, skills, app UI, or model wiring. The authoritative file-format reference for everything under agents/<id>/.
always: false
---

# Building an agent

**An agent is a directory.** There is no registration database and no build step — you create
files in the right places and the daemon reads them. Follow this and a by-chat agent is
byte-identical to a hand-authored one.

Author files with the `write` tool. Paths may be absolute.

## This file is the PROCEDURE. The format lives next door.

This page is what you DO, start to finish. The exact shape of every file is in `reference/`,
beside this one — read a page when the step you are on needs it, not before:

| you are about to | read |
| --- | --- |
| write or change an `agent.toml`, `[[settings]]`, `[[mcp]]`, `[[oauth]]` | `reference/agent-toml.md` |
| write a playbook for the agent | `reference/skills.md` |
| write a private tool | `reference/plugins.md` |
| build or change the agent's window | `reference/ui.md` |
| make that window talk to the daemon — events, methods, sign-in | `reference/app-connection.md` |
| package or publish | `reference/packaging.md` |
| connect a third-party service | the **`connect-mcp`** skill |

Read the page when you get to the step. Reading all of them first buys nothing and costs the
context you need for the actual work.

---

# Order of work

**Two jobs, two procedures.** Changing an existing agent is not a shortened version of building a
new one — start by knowing which you are doing.

- Creating an agent that does not exist yet → **Path A**.
- The user named an agent that already exists → **Path B**.

**A folder is not an agent.** The "New agent" button writes an empty one and then hands you a
message saying you are working on an EXISTING agent — so a build from scratch arrives looking
exactly like an edit. Read its `IDENTITY.md` first: if it still says *"its purpose has not been
written yet"* and there are no skills, `[[settings]]` or `[[mcp]]`, this is a **NEW agent that
happens to have a directory**. Take Path A. Taking Path B means starting with "read what you are
about to change" when the answer is *everything* — which is how a session spends thirty minutes
reading and writes nothing.

---

## Path A — a NEW agent

### A1. Agree the plan before you build it

**Come back with the shape of the agent, in plain language, and wait for a yes.** Not TOML, not a
file list — what it will do, what it will be able to reach, what it will do on its own, and what
you are going to write. Five or six lines. The owner should be able to read it without knowing
what an `[[mcp]]` block is.

Put the real questions in that same message, so they answer once:

```
What should its window be?
  1. None       — no window; reached from JARVIS chat or on a schedule
  2. Chat       — a conversation window of its own
  3. Dashboard  — numbers/charts on screen the moment it opens
  4. Workbench  — drop files in, watch each one process
```

Recommend one with a reason — a monitor wants a dashboard, an ingester wants a workbench. A
default picked silently is how an agent that should have had a screen ends up as another chat
box, and changing it later means re-authoring `[app]`, `ui/` and the tool wiring.

**And everything else that decides what the agent IS or what it can DO:**

- **What may it change?** Read-only, or may it create and modify and delete real things?
- **Does it run on its own**, on a schedule, without anybody asking?
- **Which service, and which account?** Name the servers or APIs you intend to reach, and say
  what the owner will have to supply.
- **Anything you are weighing.** If you catch yourself reasoning about which option they would
  probably want, that is the question — ask it instead of settling it.

These all go in `agent.toml`, so asking now costs one message and asking later means rewriting
the agent. **Do not ask about how you build it** — file layout, panel styling, how a skill is
worded. Those are yours; announce them.

Answer these for yourself, from `reference/agent-toml.md` and the design section below — they are
about HOW, not WHAT: which `[[mcp]]` block or private tool implements a capability they agreed to,
what the heartbeat routine actually does, which fields a `[[settings]]` block needs.

## You DECLARE settings. The owner FILLS THEM IN.

**Never write a value into `agent.config.json`.** Declaring the field is your job; supplying the
value is the owner's, always — and that holds for a value that looks obvious, a default you are
confident about, and one you found already sitting on the machine. Which account an agent acts on
is the owner's decision even when there is only one plausible answer.

`validate_agent` reports it if you do.

### A2. Plan in FILES, not phases

`update_plan`, one line per thing they asked for — and then, for each, **the file that will
satisfy it and what that file needs from you first**.

This is the difference between a build that starts in three minutes and one that never starts.
Every file has its OWN prerequisites: `agent.toml` needs the exact package names of the servers
you are declaring, and nothing else. It does not need the CSS variable names. It does not need to
know how a button triggers a run. **Never let one file's unknown delay another file's write** —
resolve what THIS file needs, write it, then go and find out what the next one needs.

Get that wrong and the whole build becomes one unit of work whose first write waits on the last
unknown anywhere in it. That has happened: forty tool calls, thirty minutes, zero files, with the
`agent.toml` fully decided by minute three and still unwritten because the dashboard's styling was
not settled yet.

You verify against this in A7, and you cannot verify against a memory of a conversation forty tool
calls ago. That is how an agent delivers four of five requested things and reports success, having
genuinely forgotten the fifth.

### A3. `create_agent` — it exists from here on

`agents_list` first if you need to check the id is free. Then `create_agent`, which writes
`agent.toml` + `IDENTITY.md` and registers the agent LIVE — it is resolvable from this moment.

**Pass `window=true` if it needs one.** That declares `[app]` AND hands you a complete, working
window in the same call: shell, chat, run-event handling, and the four shared screens already
wired. It is not a step you can do afterwards and forget. (An agent built without a window that
later needs one: `scaffold_react_app(agent_id=…)` writes the same thing.)

**Write `agent.toml` and `IDENTITY.md` as soon as this call returns**, filling in what the agent
actually is — display keys, `[tools]`, and any `[[settings]]`/`[[mcp]]` it needs. Nothing about
the WINDOW may hold these up. They are the cheapest files in the build and everything else hangs
off them.

### A4. THE VERTICAL SLICE — make ONE thing work, end to end

Pick the single most important thing the agent must do and make only that work, all the way
through, before you build anything else:

```
write the one skill / one tool / one [[mcp]] block
  -> validate_agent      does it hold together
  -> reload_agent        if you touched agent.toml or added a plugin
  -> mcp_status          if it declares [[mcp]] — did the servers come up, what did they expose
  -> run_agent           does it actually DO it
```

**`needs <SETTING>` IS NOT A BUG.** It is the platform asking the OWNER a question, routed through
you: a server that references `${SOME_SETTING}` will not start until that field has a value, and
the flag on the `[[settings]]` block makes no difference — referencing a setting is what makes it
required. So `0/N up, needs X` on a freshly declared agent is the system working.

Say so and wait: name the fields, say they are on this agent's settings page, and stop. Then run
`mcp_status` again once they are filled.

**Do not re-shape the declaration to make the message go away.** Dropping the `${…}` reference,
marking the field optional, or filling the value in yourself all turn the servers green — and all
of them mean the agent is now running on credentials nobody chose. That is the one outcome this
whole mechanism exists to prevent, and it looks exactly like success.

**If the agent has no tools you expected, `mcp_status` FIRST.** It names the reason — a setting
the user has not filled in, a command that is wrong, a server that started and offered nothing —
and it is the only place that reason exists. Do not go looking at the package, the approval
files, or the daemon config: they will not tell you, and an agent-builder once spent twenty
minutes proving it.

**Do not build the whole agent and then test it.** A wrong assumption made here — a tool that
takes different arguments than you thought, a server that exposes different tools than its docs
say — is cheap now and expensive after you have written six files on top of it. One slice
working is worth more than five written.

### A5. Broaden

Now the rest: the remaining skills, tools, data files, `AGENTS.md`, the `[tools]` and
`[plugins.*]` wiring. Same loop — `validate_agent` until clean, fixing every `[x]`.

**Anything the agent will do more than once goes in CODE, not in prose.** A tool does the whole
sequence in one call and does it the same way every time; a skill is a procedure the model works
through again on every turn. Read `reference/plugins.md` for which services a tool can reach
directly — most authenticate with a bearer token, and for those the tool is straightforwardly the
right answer.

**Propose the tool and wait for a yes.** Say what it would do, what it needs declared, and what it
saves. The owner will not think to ask for one, and they should still be the one who agrees to it.

Reach for a skill when the work genuinely needs judgement that changes with the situation, or when
the service can only be reached over `[[mcp]]` — a tool cannot call an MCP tool, so that procedure
has to be markdown. Say which of those it was; "it was easier to write" is not one of them.

For a private tool prefer `create_tool` with `agent="<id>"`; it compile-checks the code and
writes the plugin in the right shape.

### A6. The window: build it, then OPEN it

`app/` is source; `ui/` is what the daemon serves. **Call `build_app` after every change to
`app/`**, or the user reloads and sees the old screen with nothing to explain why.

Then `verify_app(agent_id='<id>')` — every time the agent has a `ui/`. `run_agent` proved the
agent's BRAIN works; this is the only step that looks at its SCREEN, and a screen is the part
that can be perfectly built, perfectly served, and blank.

**Then drive what you actually built** — the generic checks cannot know what this agent is for:

```
verify_app(agent_id='<id>', steps=[{action: 'click', target: 'Refresh'}])
```

Target the VISIBLE TEXT of a control, not a selector. Most windows are fine until you touch them —
the handler that throws only throws on click — so a verification that never interacts reports a
healthy page with a dead button. **Look at the screenshots it returns**: passing every check and
being unusable are entirely compatible, and the image is the only thing that shows the difference.

### A7. Show them what you built, against what they ASKED for

Walk the A2 checklist — every requirement, and whether it is done. Not a summary of what you
built: a comparison. Then name the two or three decisions you took that they might disagree with,
and say which thing you had to guess at.

**And say what THEY still have to do before it works.** If the agent declares `[[settings]]` or
`[[oauth]]`, it has no tools and cannot do its job until those fields are filled in — list them
by name and say they live on the agent's settings page. An agent handed over as "done" that
answers "I can't do that" on the first question is not done, and the user has no way to know the
difference between a missing key and a broken build. You do.

### A8. `package_agent` — only when they want to SHARE it

Produces the `.agentpkg`. It re-validates and refuses on errors, so a broken agent never reaches
anyone else. See `reference/packaging.md`.

---

## Path B — CHANGING an agent that already exists

Most requests are this. It is a short job and it stays short.

### B1. Read only what you are about to change

Name the files first, then open them. The agent's `agent.toml` and the file the change lives in —
not the whole directory, not every skill, not the app source unless the change is in the window.

**One file's unknown never delays another file's write** — the same rule as A2. If the change
spans a config file and a window file, the config file goes out as soon as ITS question is
answered; it does not wait for the window's.

**Check `.agentd-meta.json` before you plan an edit.** `origin: authored` is the user's own work
and yours to change; `installed` and `curated` are refused by the daemon. One `read` settles it —
see "Whose an agent is" in AGENTS.md.

### B2. Say what you are going to do

`update_plan`, briefly. For a one-file change this is one or two lines.

### B3. Make the change

`write` / `edit` the specific files. Use `create_agent(action='update')` **only** when the user
has asked in so many words to rebuild the agent from scratch — it re-scaffolds `agent.toml` from
the skeleton and destroys `[app]`, `[tools]`, the display keys and every `[plugins.*]` line.

### B4. Prove it

```
validate_agent    ->  fix every [x], re-run until clean
reload_agent      ->  after agent.toml changes or a new private plugin
                      (NOT needed for skills or ui/ — both are live the moment you save)
build_app         ->  if you touched app/
run_agent         ->  ask it something a real user would ask
verify_app        ->  if you touched the window
```

`run_agent` is not optional here either. The change you just made is exactly the thing nobody has
ever run.

### B5. Report against what they asked

What changed, in which files, and whether it does the thing they wanted — and, if the agent
declares `[[settings]]` or `[[oauth]]`, anything the user still has to fill in for it to run.

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

**Some placeholders have a SECOND HALF, and deleting one half is worse than deleting neither.** A
placeholder that is a whole screen also has a rail entry and a branch that renders it; a
placeholder section also has a row in the section list. Delete the file alone and the rail keeps a
row that opens nothing. Delete the entry alone and the screen is unreachable code the bundler
still carries. **Grep the filename before you delete it** — `grep -rn PlaceholderThing app/src` —
and remove everything it finds, in one edit.

**Prose that merely NAMES the marker is not a placeholder.** The check reads raw source, so a file
whose comments talk about the tag gets listed among the files to delete. If that file is one you
must keep, reword the comment rather than deleting the file — and never write the literal tag into
a file that is not itself scaffolding.

There is no third state. **By the time the agent is packaged, `grep -rn "@placeholder" app/src`
must find nothing** — `validate_agent` reports `UI_PLACEHOLDER_SHIPPED` while any remain, and it
closes the pack and publish gates. It stays a warning while you build, because the widgets are
supposed to be there on day one; it only becomes fatal when the work leaves this machine.

Sample data goes with them: a template's `SAMPLE_*` constants exist so the window renders before
a `fetch` does. A panel that quietly falls back to sample data is worse than an empty panel — it
shows a number that was never measured.

# How to work while you do either path

## The batching tripwire — read this one twice

**If you have thought "I have everything I need" and then looked something else up, you are
batching. Stop, and write the file you already had everything for.**

That sentence exists because the thought has been observed three times in one session — at
minutes 11, 29 and 30 — each time followed by another lookup, and the session wrote nothing. The
three lookups were the button pattern, the CSS token names, and a package name: three unknowns
belonging to three DIFFERENT files. Any one of those files could have been written the moment its
own question was answered.

Batching feels like diligence and produces nothing. The tell is not how much you have read; it is
whether the thing you just looked up belongs to the file you were about to write.

This is not permission to guess — "Never guess at an interface" in AGENTS.md holds absolutely, and
verifying a package name against its registry before you declare it is exactly right. The failure
is different: it is holding a finished decision hostage to an unrelated one. A first version on
disk is what makes every remaining question concrete, and it is testable in a way that a plan in
your head is not.

## Send big research to a subagent

`spawn_subagent` runs a child with its own context. "Read this API's docs and tell me which server
exposes cost tools, and the exact arguments its main tool takes" comes back as three sentences
instead of spending this conversation on twenty pages.

Use it whenever finding out will take more than a couple of fetches. **Your context is for
BUILDING**; finding out belongs somewhere else. What comes back is what you keep.

## Write decisions down as you make them

Anything you decide, put it in `update_plan` or in the file it belongs to, in the same turn.

Your visible text, your tool calls and their results are all replayed to you if this conversation
is interrupted and resumed. **Your reasoning is not.** A decision that lives only in your head is
the one thing a resumed session cannot get back — so externalise it, and it survives.

## Run what you write

You have `exec`. Generated JS gets `node --check`. A generated Python plugin gets imported to
confirm it loads. Anything with a syntax error is a broken agent you handed over without looking.

Anything slow goes in the background: `exec(background=true)` returns a session id at once and
`process` polls it. Never `sleep` inside a foreground `exec` — it blocks the whole turn and shows
the user nothing.

## Where you may write

**Inside the agent you are authoring, and nowhere else.** This is enforced — `write`, `edit`,
`create_agent` and `create_tool` all refuse a path outside it, so this is not a guideline you
weigh against convenience.

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
desktop, but the agents you BUILD must live inside that fence — see "Design for hosted" in
`reference/agent-toml.md`.

Three of those deserve their reason, because each looks like an obstacle until you know it:

**The shared `plugins/` directory.** A tool written there becomes part of the product for every
agent on the machine, and — unlike an agent's own tools — it is never sandboxed on a machine that
installs it. That makes it the one place a capability refused to a private tool could be
reintroduced, so there is no route to it from here: `create_tool` requires an owning `agent` and
always writes a private, sandboxed tool. Adding a shared tool is the USER's call, made by hand. To
give an agent a capability the catalog ALREADY has, name that existing tool in its `[tools] allow`.

**Your own files.** An agent that can rewrite its own rules is an agent with no rules. If a
constraint here is wrong, say so — do not edit it.

**An installed agent.** Editing it leaves it no longer matching what its publisher shipped while
still carrying their name. Build your own instead.

**Do not look for a way around this.** `exec` runs a shell and is not covered by the same check;
using it to write where `write` refused is defeating a deliberate boundary, not solving a
problem. If you genuinely need a path outside your scope, that is a conversation with the user.

## Rules that always apply

- **Never invent a config key.** If a knob is not in `reference/`, read an existing agent's
  `agent.toml` and copy the shape, or ask.
- One concern per file. Identity in IDENTITY.md, rules in AGENTS.md, procedures in skills.
- **Anything repeatable goes in a tool, not a skill** — proposed to the owner and built once they
  agree. A skill is for work whose shape changes with the situation, or for a procedure over
  `[[mcp]]` tools, which a tool cannot call.
- Keep `[tools] allow` tight when the agent's job is narrow — it reduces mistakes and cost.
- **Always set `version`**, and bump it on every change you ship.
- After creating or changing an agent, state exactly which files you wrote and where.
- **Finished means verified.** `validate_agent` clean AND you have run it. Not when the files
  exist.

---

# Design it as a MECHANISM, not a chat box with tools

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

## The markdown files

Three files, three distinct jobs. Do not merge them.

- **IDENTITY.md** — _who it is._ Role, voice, boundaries. Injected every turn. Keep it short.
- **AGENTS.md** — _how it operates._ Numbered hard rules, data locations, output format,
  red lines. This is where behaviour actually gets specified; be concrete and testable.
- **HEARTBEAT.md** — _what to check on an autonomous tick._ Only injected on heartbeat runs.
  Requires `heartbeat` + `[capabilities] autonomy = true`.

Never author `presentation.json` — the daemon fills in tagline/suggestions itself.

## When you are blocked, say so in one line and ask

Check anything you CAN check — your own files, your tools, your workspace — and answer from that.
But when the cause is somewhere you cannot see (daemon state, a tool that simply is not there,
another agent's setup), name what is missing in one sentence and ask the user.

**Do not diagnose a system you cannot inspect.** An explanation you have no way to verify reads
like an answer and is not one — and it is worse than silence when it sends the user to fix
something that was never broken. Every agent you build should follow this rule too; put it in its
`AGENTS.md` in your own words.
