# agentd Client Protocol — v1

The published contract between the agentd daemon (the ONE backend) and every client — the desktop
app, the terminal, an agent-app's own UI, or anything a third party builds. A client may **invoke**
anything documented here; a client can never **extend** the backend (no new methods, no
orchestration). Source of truth for behaviour: `agentd/presentation/gateway.py` (`_dispatch`) and
`agentd/presentation/protocol.py` (framing) — this document tracks them.

**Transport-agnostic by design:** a client binds to a **URL + token**, never to a location. The
same client code runs against a local daemon (`ws://127.0.0.1:8787`) or a future cloud-hosted
daemon (`wss://…`) — `ws://` vs `wss://` is a transport detail, not a protocol one.

## 1. Connecting

- **Endpoint**: a WebSocket URL. Locally the daemon binds `config.host:config.port`
  (default `127.0.0.1:8787`).
- **Local discovery (host-side only)**: `~/.agentd/gateway.json` (mode 0600) —
  `{host, port, pid, token, version, started_at}`. Browser/app clients cannot read it; whoever
  *opens* the client (CLI, desktop shell, login page) injects the URL + token.
- **Auth**: bearer token, on by default. Present it as `?token=<t>` on the URL (the only slot a
  browser WebSocket has) or an `Authorization: Bearer <t>` header. Wrong/missing token → close
  code `4401`.
- **Scope (apps)**: an app connection appends `&scope=agent:<agentId>`. Scoped connections are
  restricted to the **stable tier** (§4), have their agent forced (§6), and receive only their
  agent's events (§7). Until real user auth lands, scope is a correctness seam, not a security
  boundary — the token holder is trusted.
- **Origins**: browser connections are accepted from same-host origins (any port) plus
  origin-less/native clients (`file://`, `null`, no header). Cross-host web pages are refused.

## 2. Wire format

Three JSON frame shapes (NOT JSON-RPC):

```jsonc
{"type":"req",   "id":"42", "method":"chat.send", "params":{…}}   // client → server
{"type":"res",   "id":"42", "ok":true,  "payload":{…}}            // server → client (reply)
{"type":"res",   "id":"42", "ok":false, "payload":{"error":"…"}}  // failure (error = plain string)
{"type":"event", "event":"chat.event", "payload":{…}}             // server → client (push, no id)
```

`id` is an opaque client-chosen string; replies echo it. Events are fire-and-forget with **no
sequence numbers and no replay** — a client that connects mid-run missed earlier events and should
rebuild state from `sessions.history`. Max inbound frame: 48 MiB (base64 attachments ride inside).

## 3. Handshake — `hello`

Send it first. Optionally introduce yourself:

```jsonc
// request params (all optional)
{"protocol": 1, "client": "my-app/0.2"}
// reply payload (excerpt)
{"agentName": "...", "agentId": "main", "model": "...", "version": "0.x",
 "protocol": 1, "compatible": true,             // advisory: server never rejects in v1
 "gatewayUrl": "ws://…", "workspace": "…", "sessions": 3,
 "product": "…", "productId": "…", "storeEnabled": true,
 "agents": [ {"id","name","version","tagline","suggestions","color", "app": {…}|null } ]}
```

`protocol` is the server's generation (bumped ONLY on breaking changes; additive fields/methods do
not bump it). `compatible` is `false` when the client declared a *newer* protocol than the server
speaks — degrade gracefully.

## 4. Method tiers

- **stable** — the published surface; agent apps and third-party clients may rely on it. Scoped
  connections may call ONLY this tier.
- **host** — for trusted host clients (desktop app, terminal, admin tooling): configuration,
  installs, automation. Denied on scoped connections.

| Tier | Methods |
|---|---|
| stable | `hello`, `chat.send`, `chat.abort`, `sessions.list`, `sessions.history`, `sessions.rename`, `sessions.delete`, `agents.list`, `agents.detail`, `tools.list`, `tools.invoke`, `capabilities.list`, `plugins.catalog`, `workspace.list`, `workspace.mkdir`, `workspace.upload`, `workspace.delete`, `notifications.list`, `notifications.ack` |
| host | `config.get/set`, `sessions.move/duplicate`, `projects.*`, `agents.create/remove`, `models.list`, `mcp.*`, `cron.*`, `workspace.cleanup`, `marketplace.*` |

## 5. Chat — the core flow

1. `agents.list {}` → `{agents:[…], default}` — pick an `agentId`.
2. `sessions.list {agentId}` → `{sessions:[{sessionId,title,projectId,messages,modified,…}]}`.
3. `sessions.history {agentId, sessionKey}` → `{messages:[…]}` — rebuild a thread (includes
   persisted artifacts).
4. `chat.send {sessionKey, message, agentId?, projectId?, attachments?, idempotencyKey?}` →
   `{runId, attachments:[…]}`.
   - `attachments`: `[{name, mimeType?, dataBase64}]`, saved server-side into the agent/project
     workspace `uploads/`; ≤ 32 MiB each.
   - One active run per session: a second send while running → error.
   - `idempotencyKey` dedupes retries → `{runId, deduplicated:true}`.
5. Consume `chat.event` pushes for that `runId` (§7) until `agent_end`.
6. `chat.abort {sessionKey}` → `{aborted, runId?}` to cancel.

## 6. Direct tool invocation — `tools.invoke`

`{name, params:{…}}` → `{text, artifacts:[…]}`. Runs ONE tool with no LLM turn.

- **Host connections**: only tools that self-declare `artifact_action` (canvas buttons).
- **Scoped app connections**: any tool the scoped AGENT is allowed to use (its
  `tools.allow`/`deny`), executed in that agent's workspace context. This includes the agent's
  **private tools** — plugins shipped inside its own folder (`agents/<id>/plugins/<pid>/`, same
  plugin format as the shared tier). Private tools are implicitly allowed for their owner (an
  allowlist never needs to name them; `deny` still wins), invisible to every other agent and to
  the global catalog, and they travel inside the agent's `.agentpkg`.

## 7. Events (server → client push)

Broadcast to every authorized connection; **scoped** connections receive only their own agent's
`chat.event`/`sessions.changed` (plus `agents.changed`, `notification`).

| event | payload |
|---|---|
| `chat.event` | `{sessionKey, runId, agentId, ts, event:{type,…}}` — the run play-by-play |
| `sessions.changed` | `{agentId, sessionKey, …}` — refresh session lists |
| `projects.changed` | project CRUD happened |
| `agents.changed` | full new agents list (install/remove/update) |
| `marketplace.progress` | `{id, step, message}` during installs |
| `notification` | `{id, agentId, kind, text, detail, at}` |

Inner `chat.event.event.type` sequence per run (see `domain/events.py`):
`agent_start → turn_start → (message_start → message_update* → message_end | tool_execution_start →
tool_execution_update* → tool_execution_end)* → turn_end → … → agent_end{stopReason}`.
Sub-agent activity is relayed compactly as `subagent_event`. Files a tool produced arrive as
`artifacts` on `tool_execution_end` — exactly what the tool declared, nothing inferred.

## 8. Files & artifacts — `GET /file`

Plain HTTP on the SAME port: `GET /file?path=<abs>&token=<t>` → the bytes (byte-range supported,
so `<video>` seeking works). Paths must resolve under server-allowed roots. Artifact dicts in
events/results carry the server path; clients build the URL.

## 9. Agent apps — `GET /apps/<agentId>/…`

An agent whose directory has `ui/` + an `[app]` section in `agent.toml` is an **app agent**. The
daemon serves its UI statically at `/apps/<agentId>/` on the gateway port (same origin as the WS —
no CORS). The static files themselves need no token (they are shipped code, not data); the page is
OPENED with `?token=…&scope=agent:<id>` in its URL and passes both to its WebSocket connection.
SPA fallback: extensionless paths serve the app's `entry`. Discovery: `agents.list` /
`agents.detail` / `hello.agents[]` carry `app: {title, url, mode}` (URL without token — the
opener appends its own).

The AUTHOR declares the presentation in `[app]`: `mode = "browser"` (a normal tab, the default)
or `mode = "window"` (the app's own chromeless window — the "program" feel). Every opener honors
it: `agentd app open <id>` follows the declared mode (`--window` / `--browser` force one), and the
desktop client's **Open app** button opens a dedicated window for `"window"` apps or the system
browser for `"browser"` apps. `agents.create` accepts `app: "" | "browser" | "window"` — the
non-empty values scaffold a starter `ui/` + `[app]` so a new app agent is openable immediately.

## 10. Versioning policy

- Additive = free: new methods, new payload fields, new events. Clients must ignore unknown fields.
- Breaking (rename/retype/remove) = bump `PROTOCOL_VERSION` (`gateway.py`) + document migration.
- Errors are plain strings in `payload.error` (v1); structured codes would be additive.
