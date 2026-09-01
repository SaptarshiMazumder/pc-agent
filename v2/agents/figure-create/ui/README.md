# chat-app — read this before you touch anything

You just scaffolded a **working** app: it connects, it streams, it shows tool calls, it saves
and reopens conversations, and it has a settings page where the user pastes their own API key.

Your job is to make it *this agent's* app. That is mostly writing and colour, not plumbing.

---

## Change these

| where | what |
|---|---|
| `index.html` | `<title>`, the brand mark and name, the hero heading and subheading |
| `chat.js` | `BOT_NAME`, and the `hero()` text (it is rebuilt on "new chat", so it must match) |
| `app.js` | `SUGGESTIONS` — openers for someone who has never used this agent |
| `style.css` | `:root` only. Change `--accent` and the whole app moves with it |
| `settings.js` | `GROUPS` — add a knob only if this agent genuinely needs it (see below) |

Everything the model is meant to edit is marked `CHANGE ME` in the source.

## Do not change these

The event handling in `chat.js`. It is correct against the daemon as shipped and it is
checked in CI. Every hand-written chat view before this one got the same two things wrong,
and both are invisible at runtime — the socket connects, the console is clean, and the screen
never updates:

- **the event arrives wrapped.** `{sessionKey, runId, agentId, ts, event}` — the type you
  switch on is `payload.event.type`, one level down.
- **streamed text is `message_update` with `kind: 'text_delta'`.** There is no
  `message_delta` event; a branch on one can never run.

`validate_agent` reads your `ui/*.js` and reports both. Run it before you say you are done.

## No agent id anywhere

The window opens with `?scope=<agent-id>` and the daemon forces that agent onto every request
this page makes. So `client.sessions()`, `client.history(key)` and `client.send({...})` are
already about this agent — passing an id would just be a second copy to keep in sync with
`agent.toml`. `app.js` reads `scope` from the URL once, for the window title.

## Files

```
ui/
├── index.html    the shell: rail · chat view · settings view
├── app.js        boot, view switching, conversation history   ← your surface goes here
├── chat.js       streaming, tool rows, attachments, abort     ← protocol; leave it alone
├── settings.js   config.get / config.set, BYOK
├── md.js         markdown renderer (escapes before it formats)
├── style.css     the design system — recolour from :root
└── vendor/
    └── agentd-client.js   the SDK, verbatim. Never edit or rewrite it.
```

`md.js` and `vendor/agentd-client.js` were copied from Agent Builder's own `ui/` at scaffold
time rather than kept as template copies, so there is exactly one of each in the product and
they cannot drift.

## Adding this agent's own surface

If the agent has a tool worth a button, put it in `app.js`:

```js
const res = await client.invokeTool('my_tool', { some: 'arg' })
showSomething(agentd.resultText(res))
```

and refresh that surface from `Chat`'s `onToolDone` callback too — so the screen follows what
the agent does *in conversation*, not only what the button does.

## About the settings page

`config.set` changes the **daemon**, which every agent on the machine shares. It is not
per-agent. So each knob you add is a knob this agent's window offers over the user's whole
install.

The three groups that ship — API keys, Model, Behaviour — are the ones that belong on an
agent's own page. `port`, `state_dir`, `workspace`, `agents_dir`, `subagent_max` and the tool
timeouts are machine plumbing: an agent offering to change the daemon's port is offering to
break the user's install from inside a package they trusted for one job.

Add a key only when this agent needs it. `config.get` returns everything the daemon will
accept — log `data.values` to see the list.

## Sign-in is already handled — do not write your own

`app.js` awaits `agentd.mountSignInGate()` as the first thing it does once the socket opens.
That is the whole login. It comes from the SDK (`vendor/agentd-client.js`), so it is one
implementation shared by every agent rather than a copy per app.

**It renders nothing unless a sign-in is actually needed.** Three cases where it is a no-op:

- the install is BYOK (the daemon reports no `accountsUrl`) — there are no accounts to sign in to
- the daemon already holds a live platform credential
- a stored session from last time still works, so it re-binds silently

That conditionality is why it can sit in this template unconditionally. Do not add an `if` around
it, and do not gate it on a flag: the daemon is the thing that knows whether this install is
hosted, and it is asked at runtime.

**Leave the call as it is —** `await agentd.mountSignInGate()` — unless this agent needs different
copy. The heading already comes from the page `<title>`, which is this agent's name, so passing a
`product` would be a second copy of the name to keep in sync with `agent.toml`.

The one thing worth adding is a `blurb`, because only you know what this agent does with a login:

```js
await agentd.mountSignInGate({
  blurb: '<one line: why this agent needs an account>'
})
```

**Theme it with tokens, never by editing the gate's markup.** Set any of these in `style.css`
under `:root` — the gate reads them and falls back to a neutral light card:

```
--gate-bg  --gate-card  --gate-fg  --gate-muted  --gate-border  --gate-input
--gate-accent  --gate-on-accent  --gate-error-bg  --gate-error-fg
```

Forking the markup instead would mean your copy stops matching the gate the next time the SDK is
re-vendored, and the element ids are load-bearing: the desktop shell's `AGENTD_E2E_LOGIN` hook
drives `gateEmail` / `gatePass` / `gateForm` by id to test packaged builds with nobody at the
keyboard. Renaming them disables that test silently — it fills nothing and still passes.

If this agent needs its own sign-in surface rather than a modal, use the mechanism directly:
`agentd.resolveAuth()`, `agentd.signIn({email, password})`, `agentd.signOut()`.

## The states

`style.css` ends with a `states` section: empty, loading, error, and connection. They are
already wired. Keep them wired for anything you add — a list with no empty state reads as
broken, an action with no in-flight state reads as frozen, and a failure with no message
reads as nothing happening at all.

## When you are done

1. `node --check` every `.js` file you touched.
2. `validate_agent` on this agent — it must come back clean.
3. Open the window and send one message. Watch text stream and a tool row appear.
