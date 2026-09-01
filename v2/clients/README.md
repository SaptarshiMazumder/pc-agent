# clients/

Front-ends for the agent. Each is an INDEPENDENT program that connects to the
agentd gateway over WebSocket (rendezvous via ~/.agentd/gateway.json, which also
carries the auth token) and speaks the chat.send / chat.event JSON protocol. They
share NO server code — a client could be written in any language.

## The GUI: one UI, two shells

`ui/`, `desktop/` and `web/` are an **npm workspace** rooted at this directory — so the
lockfile and `node_modules` live HERE, not in a shell.

```
clients/
├── package.json      workspace root  (npm install / npm run … here)
├── ui/               THE renderer — one React app, shared verbatim by both shells
├── desktop/          Electron shell: main + preload, supervisor, installers
└── web/              hosted browser client: vite build + nginx image
```

`ui/` is the whole user interface. Neither shell forks it and neither copies it: both point
their vite `root` at `../ui`, so a component edit lands in the desktop app and the browser
client at the same time.

The **only** thing that differs per target is host capability, and it is confined to a single
file — `ui/src/lib/platform.ts`. On desktop it uses the Electron preload bridge
(`window.agentd`) for the supervisor and native file dialogs; on web it falls back to browser
equivalents (network daemon, HTTP `/file` streaming, download/upload). Nothing else in the
renderer touches the host, so "same UI with minor per-target differences" holds by
construction rather than by discipline.

### Commands (run from `clients/`)

| | |
| --- | --- |
| `npm install` | installs all three workspaces |
| `npm run dev:desktop` | Electron app with HMR |
| `npm run dev:web` | browser client on :5273 |
| `npm run build:desktop` | → `desktop/out/` |
| `npm run build:web` | → `web/dist/` (what the nginx image serves) |
| `npm run typecheck` | typechecks every workspace |

Installers are desktop-only: `cd desktop && npm run dist:core` (or `dist:studio`).

> `electron` is pinned to an EXACT version in `desktop/package.json`. npm hoists it to
> `clients/node_modules`, where electron-builder's version probe cannot see it — with an exact
> version it reads the number straight from the manifest instead. Keep it exact.

## Other clients

- terminal/  — shim; the Python terminal REPL lives IN the package now
               (agent_runtime/clients/terminal, ships with the wheel).
               Run: `agentd chat`  (or `python -m clients.terminal` from a checkout)
- watch.py   — shim; event-log tail moved to agent_runtime/clients/watch.
- sdk-js/    — `@agentd/client`, the JS SDK agent apps use to talk to the daemon.
