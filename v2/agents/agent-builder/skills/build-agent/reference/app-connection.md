# Reference — how an app window talks to the daemon

The run events and their payload shape, every method an app connection may call, and
sign-in. **The two tagged lists in this file are checked against the runtime by a test**
(`tests/unit/test_build_agent_skill_is_true.py`) — an event or method that drifts out of
step with the daemon is a build failure, not a surprise weeks later.

---

## Reading run events — THE PAYLOAD IS NESTED

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
context_usage        {used, limit, pct, model, cached}
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
chat.status
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
oauth.connect
oauth.status
oauth.disconnect
platform.status
platform.connect
platform.disconnect
```

`chat.status {sessionKey}` answers `{running, runId}` — **call it on every reconnect, before
anything else.** A socket can drop while a run continues on the daemon, and the window that
comes back has no way to tell "still thinking" from "finished while I was away" except by
asking. Skip it and a reconnecting page either sits waiting for events that already fired, or
wipes a run that is still going. Asking is also what tells the daemon someone is watching
again, which stops it reaping the detached run.

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
`problem` string when it is not up (usually a credential the user has not filled in). It is forced
onto this agent, so a page can only ever see its own servers. Declared servers start on the
agent's first run; there was an `mcp.approve` beside this that gated each one on the user pressing
a button, and it is gone.

This is the method an agent's own SETTINGS PAGE uses. If you are BUILDING an agent and want the
same answer, call the **`mcp_status` tool** instead — same information, reachable from a tool
call.

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

`<Gate>` from `common/auth` is the whole thing. It draws a login only when this daemon says an
account is REQUIRED and nobody is signed in; otherwise it renders its children, so it is safe to
wrap every agent you build in it unconditionally.

**It must not wait for the socket.** On a hosted daemon the session token IS the socket
credential, so a page opened from a marketplace link (`/apps/<id>/`, no token in the url) cannot
connect until somebody has signed in — gating on `status === 'open'` therefore never runs, and the
page retries forever with nothing on screen. `Gate` asks over plain HTTP (`authStatus`), so it
needs no socket, and the scaffolded `main.tsx` puts it outside everything that does.

**`required`, not `available`.** An accounts service existing is not the same question as this
daemon demanding an account — a desktop daemon takes the machine token and needs none. Conflating
the two once put a login form in front of every window on a local install.

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

