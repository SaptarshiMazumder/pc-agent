# Sample: Comfy Smith — the CHAT shape

A reference implementation. Read it when you are building an agent whose work is a
**conversation that produces a file**, and especially one that **drives a machine it does not
own**.

It is registered but flagged `sample`, so it appears under Samples in the agent list rather than
mixed in with the user's own agents. It exists to be read, run and copied from.

## The idea worth stealing first

**The agent asks the server, not the user.**

An earlier version of this sample declared `COMFY_VRAM_GB` and `COMFY_MODELS_DIR` as settings. Both
were wrong, and wrong in a way that is easy to repeat: they are facts about a machine that the
machine will tell you, so putting them in a form means

- the user answers from memory, and the answer is the card they bought rather than the VRAM that
  is free right now
- the answer goes stale the moment they download a model
- the agent opens every conversation asking something it could have looked up

Now `comfy_server`, `comfy_nodes` and `comfy_models` read `/system_stats`, `/object_info` and
`/models` from the server itself. The only declared settings are the three things the server
genuinely cannot tell you: **where it is**, **the token to reach it**, and **an SSH target** for
installing what is missing.

> **Declare what only the user knows. Ask the system everything else.**
> A settings field for something observable is a bug that presents as a form.

## The loop that makes it competent

Write → validate → **run** → read the real failure → fix.

`run_workflow` queues the graph on the real server, streams per-node progress back as
`tool_progress`, downloads what it produced, and returns ComfyUI's own node errors verbatim when
it fails. Without that middle step the agent is guessing with confidence: a workflow can be
structurally perfect and still die on a LoRA that is not installed, a resolution that does not
fit, or a sampler this build renamed.

`validate_workflow` shows the other half of the pattern — it checks structure from the JSON
alone, AND, when the server is reachable, checks every class, required input and model name
against what is actually installed there. When it cannot reach the server it says so instead of
reporting a clean bill of health for checks it did not run.

## What to steal from the app

**`app/src/agentd.ts` — the React hooks over the SDK.** The most reusable file here.
`useClient`, `useChat`, `useTool`, `useWorkspace`, `useSessions`, `useSettings`, `useMcpStatus`.
It encodes the four things every hand-written agent UI gets wrong, all of which fail silently:

- the run-event payload is **nested** — `payload.event.type`, not `payload.type`
- streamed text is `message_update` with `kind: 'text_delta'`; **`message_delta` does not exist**
- the socket outlives React, so subscriptions must return their unsubscribe or handlers stack up
  per render and every delta is appended twice, then four times
- **a turn is an ORDERED LIST OF BLOCKS.** Storing `text` and `tools` as two fields throws away
  their order, and the UI can then only render "all the tools, then all the prose" — a wall of
  tool names with four unrelated sentences fused into one paragraph underneath. Thinking, tool
  calls and text go in one array, in arrival order.

**Show the reasoning.** `thinking_delta` arrives on the same event as text under a different
kind. Rendering it — dimmed, collapsing once the turn ends — is the difference between watching
an agent work and watching a spinner. Long research phases are otherwise indistinguishable from
a hang.

**A settings page inside the agent's own window.** `config.get` / `config.set` are open to an app
window on purpose, and the form is generated from `agent.toml`'s `[[settings]]` — add a field to
the declaration and it appears here, with its label and help text, with no change to the React.
Secrets are write-only: presence comes back as a boolean, never the value.

The rail carries a warning dot when a required field is empty, from the first render. An agent
that fails for a missing credential looks exactly like an agent that is broken, and the moment
that matters is before anything has gone wrong.

**Attachments.** Drop on the transcript, paste into the box, or pick with the `+`. Paste is the
one that earns its keep — the common case is a screenshot of a bad render. A pasted image has no
filename, so it is named from its mime type; without that it is stored with no extension, is not
classified as an image, and never reaches a vision model as one.

**The artifact pane.** The agent's real output is a file, so it gets its own column with its
validation state, a **Run** button and a save button — instead of a code block the user has to
scroll back and find. Validate and Run are `client.invokeTool` calls: no chat turn, no model, no
tokens.

## The React + Vite convention

```
app/     source: React, vite.config.ts, package.json      (rebuild here)
ui/      BUILT output: vite outDir                        (what ships and gets served)
```

`agent.toml` points at the build: `[app] entry = "ui/index.html"`.

Three settings in `vite.config.ts` are load-bearing:

- **`base: './'`** — the app is served under `/apps/<id>/`, so assets must resolve relatively.
  An absolute `/assets/…` requests the daemon root and every chunk 404s.
- **`outDir: '../ui'`** — the built output is what gets packaged. Nobody installing this runs npm.
- **`emptyOutDir`** — otherwise stale hashed chunks accumulate in the thing you ship.

To rebuild after editing:

```
cd app && npm install && npm run build
```

Only the AUTHOR needs Node. Anyone installing the agent gets the built `ui/`.

## Trying it

Set `COMFY_URL` in Settings to any reachable ComfyUI — a RunPod or Vast pod, a box on the LAN,
or `http://127.0.0.1:8188` if you happen to run one locally. Press **Test connection**: it calls
`comfy_server` directly, so you find out whether the URL is right without spending a conversation
on it.

## What this sample deliberately does not do

No heartbeat and no stored state beyond the workflows themselves — it is a conversation, and it
has nothing useful to do while nobody is talking to it. For an agent that runs on its own and
reports, see the dashboard sample; for one that ingests a pile of things, the workbench sample.
Picking the wrong shape is the most expensive mistake available, so it is worth reading all three
before choosing.
