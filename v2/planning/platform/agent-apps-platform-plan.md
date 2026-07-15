# Agent Apps Platform — agentd as the one backend, clients as data

**Status: P0–P3 BUILT 2026-07-12 (uncommitted; daemon restart needed). P4 (desktop embedding +
SDK dogfooding) remains.** Delivered: docs/PROTOCOL.md; hello negotiation + chat.event agentId;
`@agentd/client` SDK (v2/clients/sdk-js, ESM+IIFE, 5 unit tests); `[app]` → AgentSpec.app;
`GET /apps/<id>/` static serving (traversal-proof, SPA fallback, 307 redirect); scoped
connections (`scope=agent:<id>` → stable tier only, forced agentId, filtered events, agent-allowed
tools.invoke in agent context); browser-origin gate; `app` in agents.list/detail/hello;
`agentd app list|url|open` CLI; bundle round-trip proof (synthetic app agent in tests).

**NOTE 2026-07-15: the `app-demo` reference agent (P3) was REMOVED at the user's request** —
its coverage was rewritten onto synthetic fixtures (tests/test_platform_protocol.py,
tests/test_agent_private_plugins.py). A new reference app agent will be built later under the
self-contained layout rules (everything an agent owns — source AND built products — lives in
agents/<id>/; installers delivered to agents/<id>/clients/desktop/, excluded from packing + git).

## 0. The idea (user's framing, verified against the code)

agentd becomes an explicit **platform**: one headless core + one published protocol, and every UI —
ours or anyone's — is just a client of it.

Two kinds of agents, one builder:

| Type | What it is | UI |
|---|---|---|
| **Chat agent** (today's kind) | `agent.toml` + IDENTITY.md + tools/skills | rendered by the shared JARVIS client — no custom UI |
| **App agent** (new) | the same definition **+ its own client UI** (`ui/` dir) | its own web app: standalone in any browser, later embeddable in JARVIS |

**The invariant (the whole point):** an agent app can **INVOKE** the backend in any combination it
likes, but can **never extend it** — no new RPCs, no orchestration logic, no private backend, no
core edits. Enforced *structurally*, not by discipline:

- an app is a separate process/page that can only speak the WS protocol;
- agents extend only through the existing Tool/plugin contract (discovered, self-describing);
- only agentd core owns the loop, the gateway, and the RPC surface.

Corollary: every core improvement lifts every agent and every client at once. Ship an agent-app and
it "comes with agentd" — the package pins/depends on the runtime; the UI needs no desktop app to
survive.

**Auth note:** connect-token auth already exists (`gateway_auth` default ON, bearer/`?token=`,
`gateway.py:1572-1591`). Real user auth / multi-user is **deferred by user decision** — we keep the
token seam and add a scoping stub (P2.4) it can later plug into.

### 0.1 The deployment matrix (user clarification, 2026-07-12)

The core itself will eventually come in TWO deployments, and agent clients in TWO forms — all four
combinations must work with the SAME packages and the SAME protocol:

| | core = **local desktop daemon** (today) | core = **cloud server** (future, paid SaaS) |
|---|---|---|
| **desktop agent client** | `ws://127.0.0.1:8787` | same client, `wss://<cloud-host>` |
| **browser agent client** | served by the local daemon at `/apps/<id>/` | served by the cloud daemon, same route |

**Invariant this adds: clients bind to a URL + token, never to a location.** Concretely:
- The SDK takes `{url, token}` explicitly; it must contain **zero** localhost/gateway.json
  assumptions (discovery is the HOST's job — CLI/desktop locally, account login on cloud).
- App bundles reference only same-origin relative paths (`/apps/<id>/…`, `/file`, the WS on the
  page's own origin) so the identical `.agentpkg` deploys to either core.
- PROTOCOL.md documents connection as URL-based; `ws://` vs `wss://` is a transport detail.
- Cloud mode = the same daemon code hosted remotely + the deferred auth/multi-tenancy/payments
  layer; the P2.4 scope stub is the seam those claims plug into. Nothing in P0–P3 may assume the
  daemon and the client share a filesystem **except** the local-host conveniences (`agentd app
  open`, gateway.json discovery), which are explicitly host-side, not client-side.
- "Desktop agent client" v1 = the same web bundle in a thin shell (browser window / later
  Electron wrapper); it is NOT a separate build target.

## 1. What the audit found (why this is cheap)

Already true today — verified, not aspirational:

- **Headless core behind a WS RPC**: custom 3-frame envelope (`req`/`res`/`event`,
  `presentation/protocol.py:1-73`); dispatch = one chain in `gateway.py:1615-1719`; the desktop
  client is just a consumer (`clients/desktop/.../gateway/client.ts`).
- **Full RPC surface exists** for everything an app needs: `hello`, `chat.send/abort`,
  `sessions.*`, `agents.list/detail`, `tools.list/invoke`, `capabilities.list`, `plugins.catalog`,
  `workspace.*`, `notifications.*`, `marketplace.*`.
- **Streaming**: `chat.event` broadcast per run with the full `AgentEvent` play-by-play
  (`domain/events.py:13-30`), artifacts lifted onto `tool_execution_end` (`gateway.py:1560-1570`).
- **Token on connect** (above), rendezvous file `~/.agentd/gateway.json` `{host,port,pid,token,
  version,started_at}` (`lifecycle.py:33-51`).
- **HTTP on the WS port already**: `_http_request` serves guarded `GET /file` on 8787
  (`gateway.py:1472-1556`) → we can serve app UIs from the same port ⇒ **same origin ⇒ no CORS,
  no second server**.
- **`.agentpkg` already round-trips a `ui/` dir**: `pack_bundle` rglobs everything except
  `EXCLUDED_DIRS` (`bundle_io.py:21-30,102-129`) — `ui/` survives pack→install; it's just inert.
- **Hot-reload install**: `marketplace.install` → unpack → ledger → `after_change` reloads
  agents+plugins live, no restart (`marketplace_service.py:102-150`, `gateway.py:2179-2208`).
- **Compat + SKU seams exist**: `agentd_compat`, `entitlement` in the manifest
  (`domain/bundle.py:61-71`); flavors via `distribution.toml` (`distribution.py`).

The real gaps (the work):

| # | Gap | Fixed in |
|---|---|---|
| G1 | Protocol is implicit (lives in gateway.py + protocol.ts), `protocol: 1` hardcoded, never negotiated | P0 |
| G2 | No client SDK — every client re-implements framing/reconnect/event-filtering | P1 |
| G3 | Events are fan-out to ALL sockets, no per-agent filtering; `chat.event` lacks `agentId` | P0 (additive field) + P2.4 (scoped filter) |
| G4 | No static app serving; `ui/` dir is inert; no `[app]` concept in agent.toml/AgentSpec | P2 |
| G5 | No way to open an app (no URL mint, no CLI, no store surfacing) | P2 |
| G6 | Scoping: an app connection can call anything incl. `config.set`, `marketplace.*` | P2.4 (stub; real auth later) |
| G7 | No WS `origins=` check (any web page could try the socket; token is the only gate) | P2.5 |
| G8 | Nothing proves the loop end-to-end | P3 test agent |

## 2. Non-goals (deliberate)

- **No per-agent code in the core client, ever** (the declarative widget-kit idea is superseded by
  this plan for rich UIs; it can come later as a convenience layer on the same rails).
- **No new backend surface per agent** — apps get zero custom endpoints.
- **No real auth/multi-user now** (user-deferred). Token + scope stub only.
- **No payments/licensing RPCs** (CLI licensing stays as is).
- **No React/build-step requirement for app UIs** — the SDK ships an IIFE bundle so a plain
  `<script>` + vanilla JS app works; frameworks optional.
- **Desktop embedding of apps** is P4 (optional, after standalone works).

## 3. Phases

### P0 — Freeze the contract (protocol doc + version + tiny additive fixes)

The protocol already exists; make it *official* so third-party clients can rely on it.

- [ ] **`v2/docs/PROTOCOL.md`** — the envelope, every RPC (method, params, payload, errors),
      every event with shapes, the chat event-type sequence, artifact fetching via `GET /file`,
      connect/auth (`gateway.json`, `?token=`/Bearer), reconnect guidance. Source: the audit; keep
      it generated-from-code honest (each entry cites the handler).
- [ ] **Tier the surface** in the doc: `stable` (apps may use: hello, chat.*, sessions.*,
      agents.list/detail, tools.list/invoke, capabilities.list, plugins.catalog, workspace.*,
      notifications.*) vs `host` (JARVIS/CLI only: config.*, marketplace.*, mcp.*, cron.*,
      agents.create/remove, projects.*). This tiering is what P2.4 enforces for scoped connections.
- [ ] **Version negotiation (minimal)**: client may send `hello {protocol: N, client: "name/ver"}`;
      server replies as today (`protocol: 1`) plus `compatible: bool`. No hard reject in v1 —
      advertise, don't break. (`gateway.py:3089-3118`)
- [ ] **Additive event field**: include `agentId` in the `chat.event` broadcast payload
      (`gateway.py:3479-3494`) so clients/SDK can filter without joining sessions manually.
- [ ] Note (doc-only for now): errors are bare strings in `payload.error`; structured codes are a
      possible later additive change.

*Core diff: ~20 lines (hello params + one broadcast field). Everything else is documentation.*

### P1 — Client SDK: `@agentd/client` (TypeScript)

One small library so nobody re-implements the wire. Extracted from the proven desktop code
(`gateway/client.ts` + `protocol.ts`), not rewritten.

- [ ] New package **`v2/clients/sdk-js/`** (standalone npm pkg; `tsup` build → ESM + **IIFE**
      `agentd-client.js` for `<script>` use; no runtime deps).
- [ ] Core: `AgentdClient.connect({url, token})` (explicit — a browser can't read gateway.json;
      the host mints the URL), auto-reconnect w/ backoff, id-matched `request()`, `on(event)`.
- [ ] Typed helpers over the stable tier: `hello()`, `agents()`, `agentDetail(id)`,
      `sessions(agentId)`, `history(key)`, `send({agentId, sessionKey, message, attachments})`,
      `abort(key)`, `invokeTool(name, params)`, `capabilities(agentId?)`, `catalog()`,
      `notifications()`, `fileUrl(path)` (builds the tokenized `GET /file` URL).
- [ ] **`onRun(sessionKey|runId, handler)`** — subscribes to `chat.event` and does the
      filter-by-run bookkeeping (fixes G3 client-side), yielding a clean stream:
      `message_update`, `tool_execution_*` (+artifacts), `agent_end`.
- [ ] Types re-exported from a single `protocol.ts` (moved here; desktop later imports from the SDK
      — migration is optional follow-up M-final, not a blocker).
- [ ] Unit tests against a mock WS server; one e2e smoke vs a real daemon (node `ws`).

*Zero core changes. The terminal client and desktop stay as-is for now (in-repo consumers #2/#3).*

### P2 — App hosting: the daemon serves agent UIs

The elegant trick: agents already own a directory; the gateway already speaks HTTP on its port.

- [ ] **`[app]` in agent.toml** → `AgentSpec.app` (frozen dataclass field):
      `entry` (default `"ui/index.html"`), `title` (default agent name). Parsed in
      `file_registry.py` next to the other keys (`file_registry.py:136-235`). No `[app]` → not an
      app agent (today's agents unchanged).
- [ ] **Static serving on the gateway port**: extend `_http_request` (`gateway.py:1472-1484`) with
      `GET /apps/<agentId>/<path>` → files from `<agents_dir>/<id>/ui/`:
      path-traversal guard (reuse the `is_under_roots` pattern from `_serve_file`), stdlib
      `mimetypes`, SPA fallback to `entry` for extensionless paths, `no-store` cache headers in v1.
      Same origin as the WS ⇒ the page connects back with zero CORS.
- [ ] **Token flow**: the page is opened as `http://127.0.0.1:8787/apps/<id>/?token=…&agent=<id>`;
      the app reads `token` from its URL and passes it to the SDK. (Serving the static files
      themselves also requires the token in v1 — simplest safe default; revisit if sharing pages.)
- [ ] **Discovery**: `agents.list`/`agents.detail`/`hello.agents[]` gain `app: {title, url} | null`
      (URL minted server-side, token NOT embedded — the caller appends its own token).
- [ ] **`agentd app open <agentId>`** CLI (new `cli/commands/app.py`): resolves gateway.json,
      mints the tokenized URL, `webbrowser.open()`. Also `agentd app url <id>` to print it.
- [ ] **P2.4 Scope stub (pre-auth)**: optional `&scope=agent:<id>` on the WS connect URL. A scoped
      connection: (a) may only call the `stable` tier from P0; (b) `chat.send`/`tools.invoke`/
      `sessions.*`/`workspace.*` are forced/filtered to that agentId; (c) receives only `chat.event`
      with matching `agentId` (uses the P0 field) + its own `sessions.changed`. Implemented as a
      small check at the top of `_dispatch` + a filter in `_send_all` (`gateway.py:3496-3505`).
      **This is the seam real auth plugs into later** — scope becomes a claim in a signed token.
- [ ] **P2.5 Origin allowlist**: pass `origins=` to `websockets.serve` (`gateway.py:816-822`)
      allowing same-host origins + `null`/absent (native clients). Closes G7 cheaply.
- [ ] **Packaging**: nothing to add — `ui/` already packs (`bundle_io.py`). Add `has_app: bool` to
      `marketplace.catalog` entries (derived at pack or install) so stores can badge app agents.
      `bundle.toml` needs no new required fields.

*Core diff is contained: `file_registry.py` (+~15), `domain/agent.py` (+3), `gateway.py`
(static route ~80, scope ~40, origins ~5, discovery fields ~15), one new CLI file.*

### P3 — The test agent: `app-demo` (proves everything end-to-end)

A deliberately simple "nuanced agent with its own client" — the HR-agent shape without the HR.

- [ ] **`v2/agents/app-demo/`**: `agent.toml` (name "App Demo", `[app] title="App Demo Console"`,
      a small tools allowlist), `IDENTITY.md` (a tiny persona), **`ui/`**: `index.html` +
      `app.js` + `style.css` — **vanilla JS, no build step**, `<script src="/apps/app-demo/vendor/agentd-client.js">`
      (the SDK IIFE copied in as a vendored file).
- [ ] The UI exercises every rail, and ONLY via SDK calls:
      1. connect + `hello` (shows server version/protocol/compatible);
      2. chat panel: `send()` + `onRun()` streaming (text deltas, tool chips for
         `tool_execution_*`, stop reason), abort button, session list + resume via `history()`;
      3. "Direct invoke" panel: `capabilities()` rendered as a list → run a safe tool via
         `invokeTool()` (e.g. `ls`) with raw params JSON — proves the no-LLM path;
      4. artifact rendering via `fileUrl()` (`<img>` for images);
      5. a visible "scope" badge — and a button that tries `config.get` to SHOW it being denied
         (the invariant made visible).
- [ ] **Package + install proof**: `agentd bundle pack agents/app-demo` → `.agentpkg` (contains
      `agent/ui/**`) → `marketplace.install {file}` on a clean state dir → hot reload →
      `agentd app open app-demo` → works in a plain browser. This is the "anyone can ship a child
      client" demo.
- [ ] Keep it in-repo as the living reference for third-party app authors (like templates/README).

### P4 — (Optional, later) Desktop embedding + SDK dogfooding

- [ ] JARVIS "Apps": agents with `app` get an "Open app" affordance; embedded
      `<webview>`/iframe pointing at the same `/apps/...` URL (sandboxed). No new rails — it's the
      same page.
- [ ] Migrate the desktop renderer to `@agentd/client` (deletes its private `gateway/` copy).
- [ ] Later ladder (explicitly deferred): real auth (scope→signed claims), per-session event
      subscription RPC, structured error codes, protocol hard-negotiation, payments, remote (non-
      loopback) serving with TLS.

## 4. Test plan

- **P0**: unit — hello returns `protocol`/`compatible`; `chat.event` carries `agentId`
  (extend existing gateway tests).
- **P1**: SDK unit vs mock server (framing, reconnect resends nothing, `onRun` filters runs
  interleaved across sessions); one live-daemon smoke in CI (node).
- **P2**: pytest — static route: serves `ui/` file, 404 outside, traversal blocked, SPA fallback,
  token required; scope: denied method, forced agentId, event filtering (two sockets, one scoped);
  origins: cross-origin rejected; discovery: `app` field present only when `[app]` + `ui/` exist.
- **P3**: bundle round-trip test (pack `app-demo` → install into tmp state dir → ledger + files +
  `ui/` present → `marketplace.catalog` has `has_app`); a Playwright (already a dep) e2e: open the
  app URL against a live test gateway, send a chat message with a stub model, see streamed text.
- Full suite stays green at every phase.

## 5. Sequencing & effort

P0 → P1 → P2 → P3 strictly (each consumes the previous). P4 whenever.

Rough sizing: P0 small (mostly writing), P1 medium (extraction + tests), P2 medium (one focused
gateway feature + small parser bits), P3 small-medium (demo UI + e2e). No daemon-restart-breaking
changes anywhere; every core change is additive.

## 6. Risks / open decisions

1. **Same-port static serving** rides `websockets`' `process_request` — fine for local apps (it
   already serves `/file`), not a CDN. Acceptable: apps are local-first; remote is a later ladder.
2. **Scope stub is not security** — it's correctness + the auth seam. Until real auth, the token
   holder is trusted; document loudly in PROTOCOL.md.
3. **SDK API stability**: mark `0.x` until the desktop migrates onto it (P4) — that migration is
   the API's real test.
4. **Event fan-out volume**: scoped filtering (P2.4) also reduces waste; a true subscription RPC is
   deferred until someone actually needs it.
5. **Naming**: `[app]` / `ui/` / `/apps/<id>/` / `agentd app open` — bikeshed now, cheap forever.
