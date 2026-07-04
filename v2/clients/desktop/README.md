# agentd desktop — the Claude-Desktop-style shell (M3/M5/M6)

An Electron client of the local agentd daemon. It owns exactly three things:
**supervising the daemon** (find via `~/.agentd/gateway.json`, else spawn detached),
**rendering the protocol** (chat with streaming markdown + tool blocks, agents,
sessions, store, settings), and **being a product** (flavors). All intelligence
stays in the daemon — this app is a WebSocket client with a window.

## Dev

```powershell
npm install
npm run dev              # core flavor
npm run dev:studio       # Figure Creator Studio flavor
npm run typecheck
```

Useful env while developing:

| var | effect |
|---|---|
| `AGENTD_FLAVOR` | which `flavors/<name>/distribution.toml` a dev run uses (default `core`) |
| `AGENTD_DAEMON_CMD` | explicit daemon launch command for the supervisor (e.g. `...\.venv\Scripts\python.exe -m agentd`) |
| `AGENTD_HOME` | sandbox the rendezvous/state (used heavily by the e2e tests) |

First daemon start on a cold machine can take a minute+ (python imports); the
supervisor streams its status into the top banner. Warm starts are seconds.

## Architecture

```
src/main/       index.ts (window + IPC), supervisor.ts (find/spawn daemon),
                rendezvous.ts (gateway.json mirror), flavor.ts (distribution.toml)
src/preload/    the ONE typed bridge (window.agentd: flavor / supervisor / gateway url)
src/renderer/   gateway/ (protocol types + WS client)  state/store.ts (zustand)
                components/ (Sidebar, ChatView, MessageItem, StoreView, SettingsView)
```

Protocol notes: requests are `{type:"req", id, method, params}` with id-matched
responses; broadcasts (`chat.event`, `agents.changed`, `marketplace.progress`,
`notification`) fan out to every client. The renderer connects DIRECTLY to
`ws://127.0.0.1:<port>/?token=…` — the token comes from the rendezvous file via
the main process. The reference client for every event shape is
`agentd/clients/terminal`.

## Flavors (one codebase, many products)

`flavors/<name>/distribution.toml` = branding + provisioned plugin set + default
agent + store wiring + preinstalled bundles. The shell reads it for branding; the
daemon it spawns inherits it via `AGENTD_DISTRIBUTION`, so app and daemon are always
the same product. Bundled `.agentpkg` files in `flavors/<name>/bundles/` are
installed on first run through the ordinary marketplace flow.

## Shipping installers

```powershell
# 1. the embedded daemon runtime (a REAL venv — marketplace pip-plugins stay possible)
powershell -File scripts/build-runtime.ps1

# 2. the flagship bundle for the Studio flavor
agentd bundle pack ..\..\agents\figure-creator --out flavors\figure-creator-studio\bundles

# 3. installers
npm run dist:core        # dist/core/   — "agentd"
npm run dist:studio      # dist/studio/ — "Figure Creator Studio"
```
