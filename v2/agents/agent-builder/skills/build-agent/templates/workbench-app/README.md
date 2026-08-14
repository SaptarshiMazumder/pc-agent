# The workbench template

An agent that **ingests a pile of things**. Drop the files in, watch each one go through, see
which failed and why.

Pick this when the user's job is "here are forty of these, deal with them". Describing forty
files in a chat box is not a worse version of this window — it is a different and much slower job
the user should never have to do.

## Change these, in this order

1. **`queue.js` → `TOOL`.** The tool that processes **one** item. `client.invokeTool` runs it
   directly — no chat turn, no model call.
2. **`queue.js` → `argsFor(item)`.** The uploaded file becomes that tool's arguments.
   `item.path` is where the daemon saved it in this agent's workspace.
3. **`queue.js` → `summarize(result)`.** The one line shown under each finished item.
4. **`index.html`** — the drop-zone wording, in the terms of whoever is dropping. "Drop receipts
   here" beats "Drop files here".
5. **`style.css` → `--accent`**, and `app.js` → `SUGGESTIONS`.

## What is already wired

- Three ways in: drop, click-to-choose, and drag-over feedback so the page visibly accepts it.
- **Per-item state** — queued → uploading → working → done | failed. A single global spinner over
  forty files tells the user nothing they can act on.
- **One failure does not stop the batch.** A bad file is a fact about that file; the reason lands
  on its own row. Aborting the run would mean the user fixes one thing, reruns everything, and
  finds the next bad one.
- Two items at a time (`CONCURRENCY`). These are real tool calls — subprocesses, model calls, API
  requests. Raise it when you know the work is cheap.
- Oversized files are refused **on arrival**, with the name on screen, not as an opaque failure
  four steps later.
- Sign-in, settings, and the `[[settings]]` fields your `agent.toml` declares.

## The Ask view stays

"Why did these four fail?" is a question about the queue that the queue cannot answer. One window,
both.

## Before you ship

Run **`validate_agent`**. It checks that every `getElementById` has an element behind it, that the
events you listen for exist, and that you are not calling a method an app connection may not use.
