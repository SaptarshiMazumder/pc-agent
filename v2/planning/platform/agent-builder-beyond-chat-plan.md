# Agent Builder beyond chat: custom settings, MCP, and UIs that fit the agent

Three gaps, found while asking "could Agent Builder build an agent that trades on Coinbase, or one
that ingests a thousand files, or one that reviews video?"

The answer today is *mechanically yes, in practice no* — and each of the three fails for a
different reason. One is a teaching gap, one is half-built, one nearly exists.

---

## What is actually true today (verified, so nobody re-investigates)

| claim | reality |
|---|---|
| `scaffold_ui` can only make chat apps | The **library** has one template (`chat-app`); `TemplateLibrary` is hardcoded to `(CHAT_APP,)`. But the model writes UI with `write` — plain HTML/CSS/JS, no build step — so arbitrary UIs are already possible. Nothing *teaches* it that. |
| A UI can only chat | False. `tools.invoke`, `workspace.upload`, `workspace.list`, `config.get/set` are all app-callable. `game-master`'s UI already calls `tools.invoke`. |
| An agent can declare its own settings | **No such mechanism.** Nothing in `agent.toml` or `AgentSpec`. |
| `config.set` restricts which env names it writes | **No allowlist at all** — `env_file.update({k: str(v) for k, v in keys.items()})`. Any app-callable page can write any env var. |
| `config.get` reports custom env | No — only `PROVIDER_ENV_KEYS` (`env`, `envValues`, `providerKeys`). |
| A plugin can declare the secrets it needs | Yes — `[sandbox] secrets = ["ACME_API_KEY"]` in `plugin.toml`. The per-plugin half already exists. |
| MCP can be connected by chat | Yes — `add_mcp` tool (gated on `mcp_workshop`), plus `mcp.add` / `mcp.list` / `mcp.remove`. Server config carries `command`, `url`, `headers`, **`env`**. |
| An MCP connection travels with a published agent | **No.** `mcp.add` writes `agentd.config.json`, which is machine-wide and not packaged. `agent.toml` has no `[[mcp]]`. |
| MCP server names are per agent | No — one flat `config.mcp_servers`; `_mcp_add` refuses a duplicate name. Two agents cannot both have an `aws`. |
| An MCP subprocess is isolated from the daemon's env | No — `resolve_subprocess` gives the child `{**os.environ, **overlay}`. An unset declared key falls through to the daemon's own. |
| OAuth-authenticated MCP servers work | Not yet, but the SDK does the hard part: `mcp` 1.28.1 ships `OAuthClientProvider` (RFC 9728 discovery, RFC 7591 registration, PKCE, refresh) and `streamablehttp_client` takes `auth=`. We supply a `TokenStorage`, a redirect handler, and a callback handler. |
| `config.set`'s `patch` half is restricted | **No.** `WRITABLE_CONFIG_KEYS = frozenset(EXPOSED_CONFIG_KEYS)`, and it is app-callable — an installed agent's page can write `state_dir`, `sandbox_*`, `mcp_workshop`. |

---

## Part 1 — Custom settings fields  ← LANDED (2026-08-12)

Shipped as described below, with two decisions taken during the work:

- **A host connection gets every agent's declared names**, not none. `scope` is None there, so
  "this agent" names nobody; the machine's owner is not the threat the allowlist exists for. It is
  still an allowlist — no page invents an env name.
- **`settingsValues`** carries the `text`/`url` values (never a secret) as its own field, because
  `envValues` is stripped wholesale for an installed agent — exactly where a typo'd URL most needs
  to be fixable.

Not done: the `validate_agent` warning in the table below.


**The need.** An author declares what their agent needs from whoever runs it — an API key, a
database URL, an MCP endpoint. The declaration ships with the package. The **values never do**.
A downloader sees empty fields, fills them, and they land in their own `.env`.

### The declaration

```toml
# agent.toml — ships in the .agentpkg
[[settings]]
key      = "COINBASE_API_KEY"
label    = "Coinbase API key"
kind     = "secret"          # secret | text | url
required = true
help     = "Read-only key from Settings → API in Coinbase."

[[settings]]
key   = "TRADING_DB_URL"
label = "Database URL"
kind  = "url"
```

### Changes

| file | change |
|---|---|
| `domain/agent.py` | `SettingField` value object + `AgentSpec.settings: tuple[SettingField, ...]` |
| `infrastructure/agents/file_registry.py` | parse `[[settings]]`. A malformed row is **dropped with a warning**, never silently — the author has to see it |
| `presentation/gateway.py` `_config_get` | return this agent's declared fields plus **presence only** (`{key: bool}`). Never values, for the same reason `envValues` is stripped for installed agents |
| `presentation/gateway.py` `_config_set` | accept only `PROVIDER_ENV_KEYS` + **this agent's declared keys**. Everything else refused, loudly |
| `templates/chat-app/settings.js` (+ agent-builder, comfyui, game-master copies) | render the declared fields: label, help, required marker, "saved" for secrets |
| `agents/agent-builder/skills/build-agent/SKILL.md` | how to declare, and that values never ship |
| `validate_agent` | warn when a declared key is `required` and the agent has no `[sandbox] secrets` entry for it in any plugin that would read it |

### Decisions

- **The allowlist rides along, it is not a separate fix.** `config.set` writing any env name is a
  live hole: an installed agent's settings page can overwrite `OPENAI_API_KEY`, or the session
  token. The declaration is precisely what makes a safe allowlist possible, so the two land
  together.
- **Secrets are write-only in the UI.** You see "saved", never the value — the same rule
  `envValues` already follows for an installed agent, and for the same reason: a page that shipped
  inside someone else's download must not be able to read the user's key back out.
- **Presence, not values, over the wire.** `{"COINBASE_API_KEY": true}` is enough to render
  "saved" and to drive a "you still need to fill this in" prompt.

### Ships vs stays

```
agent.toml   [[settings]]  → packaged, travels to every installer
.env         the values     → already excluded from packaging; per machine, per user
```

### Part 1 RETROFIT — prefixed storage + a patch-side allowlist  ← LANDED (2026-08-12)

Landed as described, with three decisions taken during the work:

- **The patch allowlist keys off INSTALLED, not scoped.** Scoping it to every app connection broke
  Agent Builder's own settings window, which deliberately edits the daemon — and correctly so, it
  is the user's own tool on the user's own machine. The line that matters is the one this file
  already draws for `config.get`'s secret-bearing fields (`_redact_for_installed_agent`): code
  that arrived in a package is not code the user wrote.
- **`config.set`'s `raw` half is refused for a downloaded page too.** It replaces the whole config
  file, so it walks past both allowlists by construction. Missing it would have made the other two
  decorative.
- **A host write of a declared key must name the agent** (`agentId`). Auto-targeting the single
  declaring agent would be right today and silently wrong the day a second agent declares the same
  name — and the failure then is a credential written into the wrong agent's slot.

`current_setting_env` is the single resolution point, used by BOTH substitution sites (in-process
`fetch` and the sandbox broker) so they cannot drift apart.


Two holes found while planning Part 2. Both are in what shipped; neither is theoretical.

**1. Declared settings are stored under their RAW name, so two agents cannot hold two accounts.**
`config.set` writes `AWS_ACCESS_KEY_ID` verbatim into the one shared `.env`, and both the plugin
path (`_resolved` in `infrastructure/net/outbound.py`) and the MCP path (`resolve_subprocess`)
read it back raw from `os.environ`. A cost-monitoring agent and a provisioning agent pointed at
two different AWS accounts overwrite each other. The daemon's own ambient value is
indistinguishable from either.

Fix — **the prefix is a storage detail, applied and stripped at the boundary**:

```
declared by the author   key = "AWS_ACCESS_KEY_ID"        (unchanged)
shown on the page        "AWS access key"                 (unchanged)
stored in .env           aws-provisioner__AWS_ACCESS_KEY_ID = AKIA…
handed to its MCP child  AWS_ACCESS_KEY_ID = AKIA…
provider keys            ANTHROPIC_API_KEY = …            (unprefixed, machine-wide BY DESIGN)
```

Touches `_config_get` / `_config_set` (prefix on write, strip on read), `_resolved` (take the
agent from the run context), the MCP env mapping, and the Part 1 tests. The settings pages do not
change — they never see the prefix.

This REPLACES the "scrub the child environment" idea from the first draft: prefixing gets the same
property by construction rather than by exception. One case it does not cover — an unset value
still lets the daemon's ambient variable show through — so the connector must also refuse to
connect when a referenced setting is unset (see Part 2).

**2. `config.set`'s `patch` half still has no allowlist.** Part 1 closed the `keys` side only.
`WRITABLE_CONFIG_KEYS = frozenset(EXPOSED_CONFIG_KEYS)` and `config.set` is app-callable, so an
INSTALLED agent's settings page can today write `state_dir`, `agents_dir`, `sandbox_*` — and
`mcp_workshop`, which then lets it chat-drive `add_mcp` into running anything it likes. The
build-agent skill *tells* authors not to touch machine plumbing; nothing enforces it.

Fix: a patch-side allowlist for SCOPED connections — its own `agents.<id>.*` block and its model
knobs, nothing else. Host connections keep the full surface.

---

## Part 2 — MCP that travels with the agent  ← LANDED (2026-08-12)

Landed as planned, including all seven walkthrough requirements. Decisions taken during the work:

- **Declared-MCP tools are implicitly allowed.** Requiring `[tools] allow = ["aws__*"]` on top of
  `[[mcp]]` would mean every author writes the same list twice and one copy silently wins.
- **`mcp_workshop` needed a real per-agent filter, not just prompt text.** The existing capability
  gates (`autonomy`, `notify`) only shape what the agent is TOLD it can do; `add_mcp` is a shared
  catalog tool, so an agent with the capability off still had it. `capability_enabled` is now the
  one rule, used by both the prompt and the toolset so they cannot drift.
- **Consent is recorded against the exact argv**, not the server name — otherwise approving "the
  aws server" once is consent to whatever that name means three versions later.
- **A failure is remembered, not retried every turn.** A server that is down would otherwise add
  its timeout to every message the user sends. Changing a setting or approving the command clears
  it; `config.set` does that automatically for the agents affected.

Left for Part 4: `validate_agent` checks. Left for Part 2b: `auth = "oauth:…"` (the field is
parsed and carried today, and nothing consumes it yet).


**The first draft of this section said "runtime is already there, this is a skill, not code."
That was wrong.** What exists is the *author's* path: `add_mcp` connects a server and persists it
to `agentd.config.json`, which is **machine-wide and not packaged**. Publish that agent and the
installer gets `[tools] allow = ["aws__*"]` matching nothing — an agent that looks installed and
silently has no tools.

### The declaration

```toml
[[mcp]]
name    = "aws"                                          # tool namespace -> aws__*
command = ["uvx", "awslabs.aws-api-mcp-server@latest"]   # stdio; or url = "https://…"
env     = { AWS_REGION = "${AWS_REGION}" }               # ${…} names a [[settings]] key
# headers = { Authorization = "Bearer ${ACME_TOKEN}" }   # for url servers
# auth    = "oauth:acme"                                 # instead of headers — see Part 2b
```

Ships inside the package. Values never do; they resolve from the installer's own `.env` through
Part 1's settings page.

### Data flow

```
agent.toml [[mcp]]
   └─ file_registry parses ──> AgentSpec.mcp          (declaration, pure data)
                                     │
run of agent X ──> AgentMcpConnector.ensure(spec)     (application: policy + cache)
                                     │  referenced ${VAR} unset? -> refuse, loudly
                                     │  resolves ${…} to LITERAL values, per agent
                                     ▼
                            connect(cfg) callable     (injected; infra owns the SDK)
                                     │
                     tools ──> AgentService._agent_mcp_tools[X]
                                     │
                           _tools_for(X) = shared ∪ private ∪ X's MCP tools
```

### Changes

| layer | file | change |
|---|---|---|
| domain | `domain/agent.py` | `McpServerDecl` value object + `AgentSpec.mcp` — mirrors `SettingField` |
| infrastructure | `infrastructure/agents/file_registry.py` | parse `[[mcp]]`; malformed row dropped **and logged**, same rule as `[[settings]]` |
| application | `services/agent_mcp_connector.py` **(new)** | connected?, settings present?, connect once per agent, drop on change. Injected `connect` callable — no SDK import, no `McpServerConfig` import |
| application | `services/agent_service.py` | second per-agent map; union in `_tools_for`; `await connector.ensure(agent)` before the toolset is built |
| main | `main/container.py` | build the connector; inject `connect` (decl → `McpServerConfig` → `McpProvider.add_server`) |
| presentation | `presentation/gateway.py` | after `config.set` writes keys, invalidate every agent whose `[[mcp]]` references a written name → re-dial next run |
| skill | `skills/connect-mcp/SKILL.md` **(new)** | public server / pasted manifest / never invent an endpoint / credentials via `[[settings]]` / verify before claiming success |

(No `validate_agent` row — see Part 4. Validation is built ONCE, last, against the finished rules.)

### What the three-agent walkthrough forced in

Traced against: two AWS agents on two accounts (cost dashboard, provisioner) and one agent for an
obscure health app with a private MCP.

- **Declared servers must NEVER enter `config.mcp_servers`.** That list is machine-wide and
  `_mcp_add` refuses a duplicate name — the second AWS agent would be rejected outright, and
  `mcp.remove "aws"` would rip out whichever got there first. Declared servers live only in
  `AgentSpec` + the connector's cache.
- **The second per-agent map is not optional.** `_agent_tools` is rebuilt WHOLESALE on every
  marketplace hot-reload (`container.py` `set_agent_tools`), so MCP tools appended there vanish on
  the next install. Separate map, separate owner.
- **`${…}` is resolved by the connector, not by infra.** `resolve_subprocess` expands from
  `os.environ` and knows nothing about agents — after the retrofit it would find nothing (values
  are stored prefixed) or, worse, the daemon's own. The application layer resolves per agent and
  hands infra a fully-resolved config. Infra keeps its current behaviour for machine-wide servers.
- **`find_tool(name, agent_id)` must search the MCP map**, or a channel- or cron-invoked `aws__x`
  misses.
- **Tag the tools with `_agent_id`.** The scoped `tools.invoke` check admits a tool only if it is
  the agent's own or inside its allow scope. Without the tag, scenario 1's whole point — a cost
  dashboard calling `aws__get_cost_and_usage` with no chat turn — does not work.
- **An on-demand verify path is needed.** The skill's core rule is "never claim success until
  `tools.list` names the tools back", but connect is lazy (first run) and Agent Builder cannot
  easily trigger another agent's first run. Without `mcp.verify` (or an authoring tool that
  connects agent X's declared servers now and reports), that instruction is unenforceable.
- **Per-agent tools stay out of the machine-wide `tools.json` catalog.**

### Decisions

- **Lazy connect**, on the agent's first run — not at boot, not at install. Settings are filled in
  AFTER install, and an agent nobody opens should not spawn a subprocess. Cost: the first message
  after a restart is slower.
- **Show the command and ask, once, at first connect.** `command = ["uvx", "…"]` means installing
  an agent causes third-party code to be downloaded and executed on the user's machine. URL servers
  execute nothing locally, so the prompt is stdio-only. Deliberately at FIRST CONNECT, not at
  install — the install-consent path belongs to the other developer.
- **`mcp_workshop` becomes a per-agent capability.** It is the flag that puts the `add_mcp` TOOL in
  an agent's toolset, letting the MODEL wire up a server mid-conversation from chat text — which can
  arrive from a webpage or an email. Global stays ON; `[capabilities] mcp_workshop` overrides per
  agent, exactly how `autonomy` / `notify` / `channels` already resolve (absent → inherit,
  explicit → wins). Consequence: an agent that says nothing INHERITS ON, so **Agent Builder must
  write `mcp_workshop = false` into every agent it builds** unless the author asks for it.

---

## Part 2b — OAuth, as a global capability  ← LANDED (2026-08-12)

Landed, with one significant departure from the plan below and three smaller decisions:

- **The flow is ours, not the SDK's.** The plan said "the SDK does the protocol". It does — for
  MCP-spec servers with published metadata and dynamic registration. But `OAuthClientProvider` is
  an `httpx.Auth` that only runs during an MCP request, so it cannot serve the OTHER consumer (a
  plugin calling a plain REST API) and cannot help a classic provider like Google or Notion, which
  is the realistic case. So `OAuthService` implements authorization-code + PKCE + refresh
  (~200 lines), with RFC 8414 discovery so a modern provider is still three lines of toml. One
  flow, both consumers, and no branch that only works for one kind of server.
- **PKCE always**, even where a client secret exists: the code comes back through a loopback
  redirect any local process could race for.
- **`${oauth:…}` reads a stored token and never refreshes**, because it runs inside a plugin's
  synchronous `fetch`. A stale token surfaces as the provider's own 401 rather than blocking a
  tool call on a token endpoint. The MCP path, which can await, does refresh.
- **An expired token with no refresh token disconnects itself.** Handing back something that will
  401 sends the user looking in the wrong place.

Known limitation, deliberately not papered over: an MCP session gets its bearer token at CONNECT
time, so a long-lived session can outlive it. The fix is that a 401 drops the server and the next
run reconnects — not a token quietly going stale inside a session nobody re-examines.


Static credentials only would have left "an app I signed up to" unbuildable — most such services
have no API key at all. So OAuth is in, and **not as an MCP feature**: one service, two consumers.

**The SDK does the protocol.** `mcp` 1.28.1 ships `OAuthClientProvider` — RFC 9728 discovery, RFC
7591 dynamic client registration, PKCE, refresh — as an `httpx.Auth`, and
`streamablehttp_client(url, auth=…)` takes it. It needs exactly three things from us: a
`TokenStorage` (4 methods), a `redirect_handler`, a `callback_handler`. This is wiring and a token
store, not a protocol implementation.

**The callback has a home already.** The daemon serves HTTP at `_http_request`, so the redirect URI
is `http://127.0.0.1:<daemon-port>/oauth/callback` — fixed, which matters because most providers
require a pre-registered one.

### The declaration

```toml
[[oauth]]
name   = "myhealth"                     # what tools and servers reference
server = "https://api.myhealth.app"     # discovery root; or explicit authorize/token URLs
scopes = ["read:records"]
# only for providers WITHOUT dynamic registration (Google, Notion, Coinbase…):
client_id     = "${MYHEALTH_CLIENT_ID}"
client_secret = "${MYHEALTH_CLIENT_SECRET}"

[[mcp]]
name = "myhealth"
url  = "https://api.myhealth.app/mcp"
auth = "oauth:myhealth"
```

**Two worlds, one declaration.** An MCP-spec server supporting dynamic client registration needs
nothing but a click. A classic provider needs a pre-registered app, so the author declares
`client_id`/`client_secret` as `[[settings]]` and the INSTALLER pastes their own — Part 1 composes
here unchanged.

### Two consumers, one store

```
[[oauth]] ──> OAuthService ──> TokenStore (per account, per agent)
                   │
                   ├──> MCP:     SDK OAuthClientProvider over our TokenStorage adapter
                   └──> plugins: ${oauth:myhealth} in outbound.py -> a live, refreshed token
```

The second line is what makes it global: a private tool calling a REST API writes
`Authorization = "Bearer ${oauth:myhealth}"` and never touches a refresh cycle. Same rule as
`${SECRET}` today — the plugin says where the credential goes and never holds it.

| layer | file | change |
|---|---|---|
| domain | `domain/oauth_connection.py` **(new)** | `OAuthConnection` value object; finally wires up `AuthProfile`, which was built for multi-account rotation and left explicitly unwired |
| application | `services/oauth_service.py` **(new)** | `begin → authorize_url`, `complete(state, code)`, `token(agent, name)` refreshing near expiry, `list`, `revoke`. Ports for store + HTTP |
| infrastructure | `auth/file_token_store.py` **(new)** | `<state_dir>/accounts/<acct>/oauth/<agent>/<name>.json`, 0600 |
| infrastructure | `tools/mcp/oauth_token_storage.py` **(new)** | adapts our store to the SDK's `TokenStorage` protocol |
| infrastructure | `net/outbound.py` | resolve `${oauth:<name>}` through the service; agent from the run context |
| presentation | `presentation/gateway.py` | `GET /oauth/callback`; `oauth.list` / `oauth.connect` / `oauth.disconnect` so a settings page shows "Connected as …" |
| tool | `tools/oauth_connect_tool.py` **(new)** | the global tool: the agent asks, the user gets a link, the tool reports which account connected |
| skill | `skills/connect-mcp/SKILL.md` | a 401 with `WWW-Authenticate` means OAuth — declare `[[oauth]]`, do not hunt for an API key |

### Decisions

- **A connection is scoped per (account, agent).** Not a tradeoff — it is SIMPLER: the token path
  gains one segment and the lookup key is already `(agent, name)` because the declaration lives in
  that agent's toml. Sharing would need an extra "who owns this connection" rule. A `shared = true`
  opt-in can come later if one Google login for everything is ever wanted.
- **The CLIENT opens the browser.** The service returns the authorize URL; the daemon opening a
  browser is right on desktop and wrong the moment the UI is a tab somewhere else. Daemon-opens
  stays as the terminal-client fallback.
- **Tokens at rest: plain JSON, 0600, in the state dir. Not encrypted.** OS keychain (DPAPI /
  Keychain / libsecret) is a later step, and saying so plainly beats implying protection that is
  not there. Acceptable because these agents are local to the user's own PC and `.env`-class
  secrets are already stored the same way.

---

## Part 3 — UIs that fit the agent  ← LANDED (2026-08-12)

Teaching + two templates, as planned. Notes:

- **`viewer` got no template.** Three shapes cover the real cases and the fourth is a dashboard
  with the tiles swapped for the artifact — a template for it would be a guess written before
  anyone wanted one, which is what the library's own docstring warns against.
- **The template tests are now parametrized over the whole catalogue**, which the library
  demanded ("an untested template is the path everyone picks"). Worth it immediately: the
  markup check caught `chat.js` reaching for a `newChat` button that neither new template
  declared — a null dereference at boot that would have taken every control on the page with it,
  in both templates, invisible until someone opened the window.
- **`scaffold_ui`'s own description now leads with the shape choice.** The enum came free from
  the catalogue, but a tool whose description only describes chat is a tool the model reads as
  "this makes chat apps".

## Part 3 as planned (kept for reference)

**Teaching first, templates second.** The ceiling today is that nothing tells the model the choice
exists — not that it cannot build other shapes.

**Skill section: pick the shape from what the agent DOES.**

| the agent… | the UI |
|---|---|
| holds a conversation | chat |
| runs on its own and reports | dashboard — stats, chart, table, params |
| ingests a pile of things | workbench — drop zone, queue with per-item status |
| produces artifacts to review | viewer + actions |

**The primitives table, which currently appears nowhere:**

```
tools.invoke       run an action with no chat turn
workspace.upload   file uploads
workspace.list     + GET /file — read what the agent produced
config.get/set     parameters (and Part 1's declared fields)
chat.event         live progress while something long runs
```

**Two more templates** as starting points — `dashboard-app` and `workbench-app`. Same vendored SDK
and sign-in as `chat-app`. **Hand-rolled SVG charts, no CDN** — a published page is served under a
strict CSP and an external script silently never loads. `TemplateLibrary` already takes a tuple;
it is hardcoded to `(CHAT_APP,)`.

---

## Part 4 — `validate_agent`, deliberately LAST  ← LANDED (2026-08-12)

`DeclarationRules` covers the list below. Two things worth recording:

- **Being written last paid for itself immediately.** The first version flagged any settings key
  ending in `_API_KEY` as shadowing a provider key — which is exactly the naming convention
  `build-agent/SKILL.md` tells authors to use (`COINBASE_API_KEY`). A check that fires on its own
  documented example is one people switch off. The rule now takes the REAL `PROVIDER_ENV_KEYS`
  by injection, like `UiRules` takes the method vocabulary: a rule must not guess at a list the
  runtime owns.
- **Levels are deliberate.** `CREDENTIAL_IN_AGENT_TOML` and the undeclared-`${…}` checks are
  ERRORS — they ship a key or guarantee a toolless agent. `SETTING_NEVER_USED` is a WARN, because
  a plugin may read the value straight from the environment in code the rule cannot see. The
  local-only note is INFO: nothing is wrong, it just must be said before publishing.

Swept over all 16 agents in the repo: no findings, which is the right answer — none of them
declares any of these blocks yet.

Not done from the list below: the "stdio command whose launcher is not installed" check. It needs
to look at the machine rather than the file, so it belongs with the connector's own error, which
already reports it at connect time.

## Part 4 as planned (kept for reference)

**Validation is not a row in each part's table. It is its own step, built once, at the end, against
the finished rules.** Written incrementally it would be three half-checks chasing a moving target,
each one written before the rule it enforces had settled. Written last it can validate the hell out
of the whole surface — and it is the thing that decides whether an author ships a working agent or
a broken one, so it gets a pass of its own rather than a footnote in someone else's.

What it owes by then (grows as the parts land — add to this list, do not implement early):

**Settings**
- a `required` field with no `[sandbox] secrets` entry in any plugin that would read it
- a declared key that nothing in the agent ever references — dead field, or a typo
- a key that collides with a PROVIDER key or an `AGENTD_*` name
- a value inlined into `agent.toml` that looks like a credential (the packaged-secret catastrophe)

**MCP**
- `[[mcp]]` referencing a `${VAR}` that is neither declared in `[[settings]]` nor in the environment
- `[tools] allow` naming a `server__*` namespace no `[[mcp]]` block declares (the silent-no-tools
  failure this whole part exists to prevent)
- both `command` and `url` set, or neither
- a stdio `command` whose launcher is not installed on this machine

**OAuth**
- `[[oauth]]` with neither dynamic registration nor a declared `client_id`
- `auth = "oauth:<name>"` naming no `[[oauth]]` block

**Shipping**
- any agent declaring `[[settings]]`, `[[mcp]]` or `[[oauth]]` is **local-only** — say so before
  publishing, not after
- `mcp_workshop` inherited rather than explicitly set

---

## Hosted mode is OUT OF SCOPE for all of this — decided, not deferred by accident

`[[settings]]` values live in the daemon's `.env`; a stdio `[[mcp]]` server spawns a subprocess in
the container; the OAuth callback is a loopback URL. On a hosted daemon one container serves every
account, so all three are wrong there for the same reason.

The call: **anything that connects to a third-party service with the user's own credentials is a
DESKTOP agent.** Not a limitation to work around later — it is what makes the design safe to keep
simple (no per-account secret vault, no shared-container credential isolation, no hosted callback
broker). `validate_agent` should say "local-only" for any agent declaring `[[settings]]`,
`[[mcp]]`, or `[[oauth]]`, rather than letting someone discover it after publishing.

---

## Order

**1-retrofit → 2 → 2b → 3 → 4 (validation).**

The retrofit comes first: Part 2 cannot resolve `${…}` correctly until storage is prefixed, and
every extra day the patch-side hole stays open is a day an installed agent can rewrite machine
config. Part 2b lands after Part 2 because `auth = "oauth:…"` is the same seam in the connector —
doing it second costs nothing, doing it first blocks on plumbing that does not exist yet. Part 3 is
independent and can slot in anywhere. **Part 4 is last by design, not by neglect** — validation
written against rules that are still moving is validation that has to be rewritten.

## Context when this was written

- The **agents-directory layout is in flux** — the other developer is changing where agents live.
  The `<agents_dir>` write-scope fix and per-account agent paths are parked until that settles.
  None of the three parts here depend on it.
- Already landed and independent: `agents.detail` returns `dir`; the install ledger records
  `publisher_id`; `_protected_paths` skips agents you published.
- Unrelated and outstanding: the per-agent installer build fails with
  `makensis: Bad text encoding — stub.nsi:1`, so bundles publish but no `.exe` is produced.
