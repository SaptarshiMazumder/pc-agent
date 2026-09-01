# The dashboard template

An agent that **runs on its own and reports**. The window opens on the numbers; asking is the
secondary view, not the main one.

Pick this when the user's first question on opening the window is *"what is it now?"* — a monitor,
a tracker, a cost or health or portfolio view. Pick the chat template when the work genuinely is a
conversation.

## Change these, in this order

1. **`board.js` → `TOOL` and `ARGS`.** One of this agent's own tools. `client.invokeTool` runs it
   directly — no chat turn, no model call, no tokens — which is why Refresh is instant.
2. **`board.js` → `shape()`.** Turn the tool's result into `{ tiles, series, rows }`. Have the tool
   return JSON and this stays four lines.
3. **`index.html`** — the title, the brand mark and name, the panel headings.
4. **`style.css` → `--accent`** — one hue drives the whole app.
5. **`app.js` → `SUGGESTIONS`** — openers for the Ask view.

## What is already wired

- `Refresh`, and `REFRESH_MS` for a timer (0 = off; only worth it if the data really moves).
- **The board reloads when a tool finishes in conversation** (`onToolDone` in `app.js`). Ask the
  agent to do something and the numbers follow. Without this the screen lies after every chat.
- Every state is drawn: loading, empty, error, data. An error lands ON the screen — a refresh that
  silently does nothing looks exactly like numbers that have not changed.
- Sign-in, settings, and the `[[settings]]` fields your `agent.toml` declares.

## The chart is hand-rolled SVG on purpose

No CDN, no charting library. A published page runs under a strict CSP, so an external `<script>`
silently never loads: you get a blank rectangle and a console nobody is reading. Fifty lines of
`<polyline>` always renders. Need more than lines? Draw more SVG.

## Before you ship

Run **`validate_agent`**. It checks that every `getElementById` has an element behind it, that the
events you listen for exist, and that you are not calling a method an app connection may not use.
