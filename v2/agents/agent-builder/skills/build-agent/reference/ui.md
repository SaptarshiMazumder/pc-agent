# Reference — `ui/`, the agent's own app

Choosing the window's SHAPE, the window you are handed, the shared modules under
`src/common/`, the four screens you did not have to build, and organizations/seats.

How the window TALKS to the daemon — events and callable methods — is in
`app-connection.md`.

---

## ui/ — the agent's own app

Requires an `[app]` section. Served by the daemon at `/apps/<id>/` on the **same origin as
the WebSocket**, straight from disk on every request — edit a file, reload the window, done.

**A `ui/` with no `[app]` table is an ORPHANED_UI, and it is an error.** The files are built,
served and completely unreachable: nothing declares the window, so nothing can open it, and the
agent looks to its user like one that simply has no screen. The two always travel together —
`create_agent(window=true)` writes both, and if you add a `ui/` by hand you add the `[app]`
table in the same turn.

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

None of these shapes is a template you pick from a list. Every window starts as the same
working chat app and is EDITED into the shape that fits — because the shape that fits an agent is
a judgement about that agent, and a menu of answers is how every agent ends up being one of four
things. The next section is what you are starting from.

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

### The TWO ways a window makes something happen

There are exactly two, and picking the wrong one is why a control does nothing useful:

| | what runs | use it when |
| --- | --- | --- |
| `client.invokeTool(name, args)` | ONE tool. No model, no reasoning, no tokens. | the answer is a lookup — a number, a list, a file |
| `send(text)` | the WHOLE AGENT — model, its skills, its MCP tools — streamed into the thread | the answer needs judgement — a report, a summary, a decision |

`send` comes off the same hook the composer uses, so a button and a typed message are the same
path. It takes plain text, uses the window's current session, and streams the reply into the
thread already on screen:

```tsx
import { useRun } from '../agentd/run'

const { send } = useRun(client)

<button onClick={() => void send('Generate a cost report for the last 30 days')}>
  Cost report
</button>
```

That is the whole recipe. **Do not go reading the SDK to reconstruct it** — a build once spent
nineteen file reads working this out from `Dashboard.tsx`, `AgentPanel.tsx`, `send.ts`, `store.ts`
and the vendored `.d.ts`, and wrote nothing while it did.

A button that asks the agent to DO something wants `send`. A panel that shows a stored number
wants `invokeTool`. A dashboard usually has both.

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

### The window you start with

An agent created with a window already HAS one — a complete, working app, copied in at creation:

```
agents/<id>/app/
  src/App.tsx          the shell: a rail, and a view for each screen
  src/components/      chat, composer, message rendering
  src/agentd/          the client, the run-event folding, sessions
  src/common/          sign-in · credits · settings · organizations   ← not yours to edit
  src/tokens.css       the palette every one of those reads
  vendor/              the SDK, carried inside the app
```

**So you edit a running window rather than assembling one**, which is a different and much smaller
job — and one where a mistake shows up when you build, not when somebody else installs the agent.
`build_app`, reload, look at it.

It is the shape the user PICKED at creation — `chat` (a thread and a composer) or `dashboard`
(a panel grid over the agent's own tools, chat still in the rail). Templates live in
`templates/_variants/`, each holding only the files that differ from the one base skeleton. Reshaping it into a dashboard or a workbench is
ordinary work: the chat view is one branch in `App.tsx` and one component tree under
`components/`. Read the shape table above, then change that branch. What you must not do is
delete the four shared screens along with it — see below.

`scaffold_react_app` exists for the agent that was created WITHOUT a window and is getting one
later. It writes the same thing.

Two things about that project that only fail on somebody ELSE's machine:

- **The SDK is vendored, not depended on.** `vendor/agentd-client.js` with an alias in
  `vite.config.ts` and a `paths` entry in `tsconfig.json`. Never replace that with
  `"@agentd/client": "file:..."` — a relative path into this product's own tree exists nowhere
  else, so `npm install` fails for every recipient and never for you.
- **`ui/` is the build output and `ui/` is what ships.** The daemon serves it and the packer takes
  what is on disk, so an unbuilt change is invisible however correct the source is — the user
  reloads the window, sees the old screen, and nothing on it explains why. **Call `build_app`
  after every change to `app/`,** and again before `verify_app`, `package_agent` or
  `publish_agent`: all three read `ui/`, so an unbuilt change is one that does not ship.

  Use `build_app` rather than `exec`ing npm yourself. It finds the Node the product ships (a user
  who installed the app has no toolchain of their own, and no terminal to run one in), links the
  agent to the shared copy of react and vite instead of downloading them per agent, and returns
  vite's own error — file and line — when the build fails.

### There is no second way. `scaffold_ui` is gone.

It copied a complete vanilla app into `ui/` with no build step. That is no longer offered and the
tool is not registered — **do not call it**. A window is a React project, the product ships the
Node and the packages to build one, and `build_app` is the build.

Agents in this product that still have a hand-written `ui/` keep working — the daemon serves those
folders straight off disk — but nothing maintains them any more. Reworking one means rebuilding it
in React. **Never write vanilla JS for an agent window.**

### The common modules — `src/common/`

Accounts, money and configuration arrive already written. `scaffold_react_app` copies
`templates/_common/` into every new agent as `app/src/common/`:

```
common/
  README.md                    read this — it says what each module needs from you
  auth/Gate.tsx                wraps your app; shows the card only when an account is required
  auth/SignIn.tsx              the sign-in card — the assistant's own
  auth/useAuth.ts              signedIn · email · signIn() · signOut() · run mode
  auth/ProfileMenu.tsx         the account menu, and the way to reach Credits
  credits/Credits.tsx          the Credits & billing page
  orgs/OrgView.tsx             organizations and seats — create, join, invite, manage members
  settings/Settings.tsx        the settings page — the SAME one the assistant's window has
  settings/SettingsActions.tsx `useSettingsActions()`, for an `extras` control that must save first
  dev/LiveReload.tsx           reloads this window when you rebuild it; inert once published
```

Each folder also carries its own `.css`, which defines **no colours and no fonts** — every visual
property is a `var()`. The names come from `src/tokens.css`, which the scaffold ships and your
agent owns. Restyle by editing the values there; every shared page follows.

**The settings page is not yours to design.** A user configures the assistant, opens your agent,
and must meet the same page — same knobs, same names, same grouping — plus one thing: your agent's
values win over the daemon's, key by key, and every row says which layer it came from. Render it
with `<Settings client={client} agentId="<your-id>" onRestart={...} />`. Agent Builder runs this
exact module itself, so it is not a page you are being asked to trust untested.

#### Custom settings and the shared page

`[[settings]]` in `agent.toml` is all it takes to get a field: the daemon sends the declaration,
the shared page renders it, and what the user types lands in the `.env` on their machine. Nothing
in your app writes that form.

What the shared schema cannot know is anything else your window has — a run-mode switch, the MCP
servers it connects to, a **Test button** proving the URL somebody just pasted actually answers.
Those go in `extras`, keyed by the tab each belongs to:

```tsx
<Settings
  client={client}
  agentId="my-agent"
  onSaved={() => void reloadWhateverElseShowsThatValue()}
  extras={{ keys: <ServerTest onTest={() => invokeTool('probe_server')} /> }}
/>
```

**A Test button must save before it probes.** The tool it calls reads the agent's ENVIRONMENT, not
the form — so testing without saving reports on the value the user just replaced. The page owns the
edit buffer, so ask it:

```tsx
import { useSettingsActions } from '../common/settings/SettingsActions'

const page = useSettingsActions()
if (page.dirty) await page.commit()       // then probe
// and label the button `page.dirty ? 'Save & test' : 'Test connection'`
```

`onSaved` fires after every save, from the page's own button and from an `extras` control alike.
Use it if a declared value is shown anywhere ELSE in your window — a server URL in the top bar, a
"not configured" badge in the nav — or it will still be showing what was there when the window
connected.

**Import them. Never rewrite them.** `validate_agent` compares the agent's copy against the source
and reports `UI_COMMON_MODIFIED` — which blocks packaging and publishing. Every agent handling
credentials and payments the same way is the entire point; a local edit forks that into a
published artifact, and it still builds, so nothing else would catch it.

If a change is genuinely needed by every agent, make it in `templates/_common/` so they all get
it. If it is only about this agent's look, use the CSS custom properties each module documents.

**Copying is not wiring.** They arrive; you still have to render them — see the two rules below.

### The four screens you did NOT have to build

Sign-in, credits, settings and organizations arrived working. This section is about not breaking
them, because there is nothing here left to write.

`validate_agent` reports `UI_NO_SIGN_IN`, `UI_NO_CREDITS`, `UI_NO_SETTINGS` and `UI_NO_ORGS` as
**errors**, and each blocks `package_agent` and `publish_agent`. They fire when something stops
RENDERING one of these — which, now that the skeleton ships them wired, means somebody deleted a
view or replaced a file.

**Why each is mandatory, since deleting one always looks like a saving:**

| screen | what breaks without it |
|---|---|
| sign-in | on a hosted install every model call fails with a provider error and nothing on screen explains why |
| credits | running out is the one failure a user can fix themselves; without it the agent just stops |
| settings | they cannot change its model, its turn limit, or the keys it uses from inside it |
| organizations | an agent a company bought can only ever be used by whoever installed it |

Every one of those is silent for you and total for whoever installed the agent. That is the whole
reason they are copied in rather than described.

**`src/common/` — the `.tsx` IS NOT YOURS TO EDIT; the `.css` IS.** Every stylesheet in there
(`auth.css`, `credits.css`, `settings.css`, `orgs.css`) is the agent's own: restyle the login
card, the credit packs, the settings rows however this agent's look demands. What stays locked is
the behaviour — the `.tsx`. `validate_agent` compares your copy against the source and
reports `UI_COMMON_MODIFIED`, which blocks packaging — and it still BUILDS, which is what makes a
local edit dangerous. A user signs in to the assistant, opens your agent, and must meet the same
card, the same shop and the same seats page. If a change is genuinely needed by every agent, make
it in `templates/_common/`. If it is only about how this agent LOOKS, edit `src/tokens.css`: the
shared modules define no colours and no fonts at all, so every one of them follows.

**What you may do** is move them: a view in your own nav, a route, a modal reached from a gear.
The skeleton puts all four in the rail; where they live is yours, that they exist is not.

### Organizations and seats — what "an agent a company bought" means

`common/orgs` is one of the four screens that arrived working, and the one whose absence is
hardest to spot: everything works perfectly for whoever installed the agent, and nobody else can
get in.

It is the assistant's own page, so the vocabulary is the platform's rather than yours:

- **an organization** owns seats and a shared credit pool
- **a seat** is a membership. Joining takes one; every join path goes through the same gate, so an
  org with five seats admits five people whichever way they arrived
- **an invite** is a single-use code, minted by an admin, good for seven days
- **a domain** can be allowed, which OFFERS the org to anyone signing in with a matching email —
  it never adds them silently
- **a member cap** limits how much of the pool one person may spend in a month

Every one of those is enforced by the accounts service, not by the page. That is why you must not
build your own: a second client's idea of who may invite whom is a second way to get access wrong,
and access bugs are found by the people they let in.

Nothing in it needs configuring. It renders an empty state on a build with no accounts service, so
an agent that will only ever run on one laptop still passes and still costs nothing.

