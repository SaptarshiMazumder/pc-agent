# MCP Client Layer — Architecture Design

**Status:** design (no code yet — review before implementing).
**Goal:** let agentd act as an **MCP client** — connect to external MCP servers
(Google Workspace, Slack, Notion, GitHub, …), **discover their tools at runtime**,
and surface them as ordinary **guarded `Tool`s** with **zero per-service code**.
Adding Google = configuring one MCP server, not writing Gmail/Drive/Calendar code.

> Out of scope here (later phases): the approval/governance gate, OAuth UX. This
> design only adds the connector layer + makes external tools first-class.

---

## 1. The one idea

The whole point of MCP is that **an external tool looks identical to a native tool
once it's in the model's list.** So the design is a thin, well-layered adapter:

```
configured MCP server  ──(MCP protocol)──▶  McpClient  ──▶  McpTool (implements Tool)
   (Google Workspace, …)                    (1 per server)    (1 per discovered tool)
                                                                     │
                                       merged into build_tools() ────┤
                                                                     ▼
                                            GuardedTool(timeout/retry) ── engine ── prompt
```

Native tools and MCP tools end up in the **same flat `tools` list**, wrapped by the
**same `GuardedTool`**, advertised in the **same `## Tooling`** prompt section,
called by the **same loop**. The MCP-specific code stops at the adapter boundary.

---

## 2. Layering (hexagonal — respects the existing import-linter contract)

`main > presentation > infrastructure > application > domain`

| Layer | New pieces | Why here |
|---|---|---|
| **domain** | `McpToolSpec`, `McpCallResult` (pure value objects) | no IO; the data shapes only |
| **application/interfaces** | `McpSession` (Protocol), `McpToolSource` (Protocol) | the **contracts** the rest depends on (DIP) |
| **infrastructure/tools/mcp/** | `transports/`, `session.py`, `provider.py`, `tool.py`, `factory.py`, `mapping.py` | the concrete protocol/IO implementations |
| **config.py** | `mcp_servers: list[McpServerConfig]` | which servers, transport, auth — JSON/env |
| **main/container.py** | build provider → discover → merge into tools | the **composition root** (only place concretes are named) |

Nothing in `application`/`domain` imports the MCP SDK — that stays an
infrastructure detail behind our interfaces (DIP).

---

## 3. Components (each with one responsibility — SRP)

### domain/mcp.py — pure data
```text
McpToolSpec   = { server: str, name: str, description: str, input_schema: dict }
McpCallResult = { content: list[ContentBlock], is_error: bool, details: Any }
```
No behavior, no IO. `McpCallResult` reuses the existing `ContentBlock` domain type,
so mapping to `ToolResult` is trivial.

### application/interfaces/mcp.py — the contracts (DIP + ISP)
Two **small, focused** Protocols (ISP — no fat interface):

```text
class McpSession(Protocol):           # one live connection to one server
    async def list_tools(self) -> list[McpToolSpec]: ...
    async def call_tool(self, name: str, args: dict) -> McpCallResult: ...
    async def close(self) -> None: ...

class McpToolSource(Protocol):        # "produces Tools" — provider implements it
    async def discover(self) -> list[Tool]: ...
    async def aclose(self) -> None: ...
```

`McpTool` and the container depend on **these interfaces**, never on a transport or
the MCP SDK. (`Tool` itself is the existing contract.)

### infrastructure/tools/mcp/transports/ — wire IO (SRP, LSP)
One low-level interface, interchangeable implementations:
```text
class McpTransport(Protocol):  open() / send(jsonrpc) / receive() / close()
   ├── StdioTransport   # launch a local MCP server process, talk over stdio
   └── HttpTransport    # streamable-HTTP / SSE to a hosted MCP server (Composio, …)
```
Transports are **substitutable** (LSP) — the session doesn't care which it has.
**We wrap the official `mcp` Python SDK** here rather than hand-rolling JSON-RPC
(DRY): `StdioTransport` ≈ `mcp.client.stdio`, `HttpTransport` ≈ the SDK's
streamable-HTTP client. The SDK is an *implementation detail* hidden behind
`McpSession`.

### infrastructure/tools/mcp/session.py — the MCP protocol (SRP)
`SdkMcpSession(McpSession)` — does the MCP handshake (`initialize`), `tools/list`,
`tools/call` over an injected `McpTransport`. Depends on the **transport interface**
(DIP), not a concrete one. Owns per-call protocol concerns (request ids, timeouts).

### infrastructure/tools/mcp/tool.py — the adapter (LSP, never-raises)
```text
class McpTool(Tool):
    name        = spec.name           # (namespaced — see §5)
    description = spec.description
    parameters  = spec.input_schema   # MCP inputSchema IS JSON Schema → drop-in
    default_retryable = False         # external side effects: don't auto-retry
    def __init__(self, session: McpSession, spec: McpToolSpec): ...
    async def execute(self, id, params, abort, on_update=None) -> ToolResult:
        try:    return map_result(await self._session.call_tool(self.name, params))
        except CancelledError: raise
        except Exception as e:  return ToolResult.text(f"{name} failed: {e}", is_error=True)
```
- **IS-A `Tool`** → substitutable everywhere (guarded, validated, prompted, executed). (LSP)
- **Never raises** (matches every other tool) — errors become error `ToolResult`s.
- `parameters = MCP inputSchema` → the existing `validate_args` works unchanged.
- One `McpTool` class covers **every tool from every server** — no Gmail class, no
  Drive class. (OCP — new servers add tools without new code.)

### infrastructure/tools/mcp/provider.py — lifecycle + discovery (SRP)
`McpProvider(McpToolSource)` — owns the connection lifecycle for the configured
servers:
- for each enabled server: open transport → `SdkMcpSession` → `list_tools()` →
  build `McpTool` per spec,
- **graceful degradation**: a server that fails to connect is logged and skipped —
  its tools are absent, **others and the gateway are unaffected** (same pattern as
  the browser/computer factories returning `None`),
- `aclose()` closes all sessions on shutdown.

### infrastructure/tools/mcp/factory.py — selection (SRP, mirrors existing factories)
`build_mcp_provider(config) -> McpProvider | None` — returns `None` when no servers
are configured or the `mcp` SDK isn't installed (so MCP is **off by default** and a
missing optional dep never breaks unrelated tools — exactly like
`build_computer_provider`).

### infrastructure/tools/mcp/mapping.py — result mapping (SRP)
`map_result(McpCallResult) -> ToolResult` and schema passthrough. Keeps the
MCP↔agentd translation in one place.

---

## 4. Config (`config.py`)

```text
@dataclass
class McpServerConfig:
    name: str                       # namespace, e.g. "google"
    transport: str                  # "stdio" | "http"
    command: list[str] | None       # stdio: ["uvx","workspace-mcp",...]
    env: dict | None                # stdio: env (e.g. GOOGLE_OAUTH path)
    url: str | None                 # http: hosted endpoint
    headers: dict | None            # http: auth header (e.g. Composio key)
    enabled: bool = True
    allow: list[str] | None = None  # optional tool allowlist (phase 3)

# Config:
mcp_servers: list[McpServerConfig] = field(default_factory=list)   # JSON config; [] = off
```
No servers → no MCP, no cost, no behavior change. Secrets (OAuth file path, hosted
keys) live in env/`.env`, never hardcoded — consistent with the rest of agentd.

---

## 5. Cross-cutting policies

**Namespacing (collision-safe).** Every MCP tool name is prefixed with its server:
`google__gmail_send`, `slack__post_message`. Guarantees no clash with native tools
or across servers, and reads clearly in the prompt. (Stored on `McpTool.name`; the
session still calls the server with the *bare* tool name.)

**Reuse, don't reinvent.** MCP tools get, for free:
- `GuardedTool` → per-tool timeout + retry + error-norm + `tool_progress`,
- `## Tooling` prompt line (`TOOL_SUMMARIES.get(name) or description.splitlines()[0]`),
- `validate_args` (MCP inputSchema is JSON Schema),
- per-tool policy overrides via the existing `tool_overrides` (e.g. give
  `google__drive_export` a longer timeout) — **no new mechanism**.

**Lifecycle.** Connect + discover at gateway startup (async); a server-down at
startup → skipped; transport drop mid-run → that call returns an error `ToolResult`
(reconnect is a phase-2 nicety); clean `aclose()` on shutdown.

**Async + abort.** `execute` respects the shared `abort` and re-raises
`CancelledError` (so disconnect-abort still kills in-flight MCP calls), and the
per-call timeout is the `GuardedTool` wrapper + an inner session timeout.

---

## 6. Container wiring (the only place concretes meet)

```text
# main/container.py — build_service / build_gateway
mcp_provider = build_mcp_provider(config)              # infra factory
mcp_tools    = await mcp_provider.discover() if mcp_provider else []
raw          = build_tools(config, browser_manager, computer_provider) + mcp_tools
tools        = [GuardedTool(t, resolve_policy(config, t)) for t in raw]
# provider.aclose() registered on gateway shutdown
```
`build_tools` stays native-only; MCP tools are merged **after** it and guarded by the
**same** comprehension. The engine, prompt, and every existing tool are untouched.

---

## 7. SOLID — explicit mapping

| Principle | How this design honors it |
|---|---|
| **S**RP | transport (wire) · session (MCP protocol) · provider (lifecycle/discovery) · tool (one adapter) · factory (selection) · mapping (translation) — each has exactly one reason to change |
| **O**CP | new transport (websocket) or new server → **no change** to `McpTool`/provider/engine. Adding MCP doesn't modify native tools, the loop, or the prompt — they extend through the `Tool` seam |
| **L**SP | `McpTool` **is** a `Tool` — substitutable wherever a Tool is used (guard, validate, prompt, execute). Transports are substitutable behind `McpTransport` |
| **I**SP | three **small** interfaces — `McpTransport` (send/recv), `McpSession` (list/call/close), `Tool` (execute). No consumer depends on methods it doesn't use |
| **D**IP | `McpTool` → depends on `McpSession` *interface*; `McpSession` → depends on `McpTransport` *interface*; the MCP SDK is hidden in infrastructure. Interfaces live in `application`; the **container** injects concretes |

Plus the repo's own rule — **import-linter** stays green: interfaces in
`application`, implementations in `infrastructure`, wiring in `main`; no inward
dependency violated.

---

## 8. Testing (no real network/Google needed)

- **`FakeTransport`** (in-memory JSON-RPC scripted responses) → `SdkMcpSession`
  handshake/list/call.
- **`McpProvider`**: discovers → returns `McpTool`s; **server-down → []** and no
  raise (graceful degradation).
- **`McpTool.execute`**: maps content → `ToolResult`; MCP error → error result;
  exception → error result (**never raises**); abort re-raises `CancelledError`.
- **mapping**: content blocks ↔ `ToolResult`; namespacing.
- **container**: MCP tools present + each wrapped in `GuardedTool`; names namespaced;
  `lint-imports` KEPT.
No live Google calls in tests — the Google MCP server is exercised manually/e2e.

---

## 9. Phases

1. **Core (stdio):** transport(stdio) · session · provider · tool · factory · mapping
   · config · container wiring · GuardedTool reuse · tests + lint. → *plug in any
   local stdio MCP server; tools appear, guarded.*
2. **Hosted (http):** `HttpTransport` (streamable-HTTP/SSE) + header auth → connect
   to hosted servers (Composio/Zapier) for managed-OAuth Google.
3. **Polish:** per-server tool allow/deny lists, reconnect on drop, richer
   result/content mapping, optional MCP **resources/prompts**.

Then: **wire the Google Workspace MCP server** (chosen from the research) as the
first `mcp_servers` entry — Gmail/Drive/Calendar/Chat/Meet land as `google__*`
tools with no further agentd code.

---

## 10. Trust note (carried to the approval phase)

This layer makes it *possible* for the agent to read/send mail and touch Drive. The
**approval gate** (separate, later) is what makes it *safe* — it attaches at the
same `GuardedTool` chokepoint these MCP tools already pass through, so no rework:
the seam is already here.
