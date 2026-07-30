# agentd — Code Architecture (Clean Architecture)

*The exact structure of the Python agent code, in Clean Architecture terms: the layers, their
responsibilities, the interfaces, the implementations, the wiring — built so nothing is coupled and
every piece is swappable.*

Two words to know (and then we drop the jargon):
- **interface** = a contract: *what* something must do, with no code (e.g. "a MemoryRepository can
  `load` and `save`").
- **implementation** = the real code that fulfils a contract (e.g. a JSONL store, or a cloud DB).

---

## 1. The layers and their responsibilities

| Layer | Owns / responsible for | Knows about tech? |
|---|---|---|
| **Domain** | the core concepts + rules: what a `Message`/`Event` is, validation. *What the agent IS.* | No |
| **Application** | the **use-cases** (the workflow/orchestration) **+ the interfaces** it needs from outside. *What the agent DOES, step by step.* | No |
| **Infrastructure** | the **implementations**: the real LLM client, database, tools, engine. *HOW it talks to the world.* | Yes |
| **Presentation** | **delivery** — how requests get IN: gateway/web server, CLI, messaging channels. Turns outside input into a use-case call and streams the result back. | Yes |
| **Main** | the **composition root**: read config, build the implementations, wire them into the use-cases, start everything. | Yes |

---

## 2. The one rule

**Dependencies point inward.** Infrastructure + Presentation depend on **Application**; Application
depends on **Domain**; Domain depends on **nothing**. The inner layers *define* interfaces; the outer
layers *implement* them. Only **Main** knows the concrete implementations and plugs them in.

```
main/  ->  presentation/ + infrastructure/  ->  application/  ->  domain/
                  (implement the interfaces)      (defines them)
```

---

## 3. Folder structure

```
agentd/
├── domain/                  # entities + pure rules (no IO, no libraries)
│   ├── messages.py          # Message + content-block types
│   └── events.py            # AgentEvent types
│
├── application/
│   ├── interfaces/          # the contracts the use-cases depend on
│   │   ├── llm.py           # LLMService
│   │   ├── memory.py        # MemoryRepository
│   │   ├── tools.py         # Tool, ToolRegistry
│   │   ├── skills.py        # Skill, SkillRegistry (loadable playbooks)
│   │   ├── agent_engine.py  # AgentEngine (the swappable brain)
│   │   ├── events.py        # EventStream
│   │   ├── policy.py        # PolicyService, ApprovalService
│   │   ├── auth.py          # AuthService
│   │   └── metering.py      # MeteringService
│   └── services/            # the use-cases (orchestration)
│       └── agent_service.py # handle_message(...)
│
├── infrastructure/          # concrete implementations of the interfaces
│   ├── engine/
│   │   ├── native.py        # our loop + continuation/verify  (DEFAULT)
│   │   ├── claude_sdk.py    # Claude Agent SDK (Claude-only)
│   │   └── langgraph.py     # LangGraph
│   ├── llm/
│   │   ├── litellm.py       # LLMService via LiteLLM          (now)
│   │   └── fake.py          # LLMService for tests
│   ├── tools/
│   │   ├── fs.py exec.py web.py browser.py
│   │   └── registry.py      # ToolRegistry (builtin + plugins/MCP)
│   ├── skills/
│   │   └── file_skills.py   # FileSkillRegistry: scans skills/<name>/SKILL.md (now)
│   │                        # (later: cloud / per-user skill vault, same interface)
│   ├── memory/
│   │   ├── local_store.py   # MemoryRepository via JSONL/SQLite (now)
│   │   └── cloud_bank.py    # MemoryRepository via cloud + E2E  (later)
│   ├── policy/
│   │   ├── rule_policy.py    # PolicyService: allow/deny/needs-approval
│   │   └── guarded_tool.py   # wraps any Tool with policy + approval
│   └── controlplane/
│       ├── auth_client.py    # AuthService (outbound)
│       └── metering_client.py # MeteringService (outbound)
│
├── presentation/            # delivery — how requests get IN
│   ├── gateway/websocket.py # the localhost server (token-gated) + EventStream impl
│   ├── cli/terminal.py
│   └── channels/telegram.py # ... whatsapp.py etc.
│
└── main/                    # composition root
    ├── config.py            # Config (engine, model/tier, memory backend, paths, endpoints)
    ├── container.py         # build implementations from config + wire into the use-cases
    └── main.py              # entrypoint: python -m agent_runtime

# content (data, not code) — lives beside the package, not inside it:
skills/                      # one folder per skill, each with a SKILL.md playbook
  browser-automation/SKILL.md   # ships with agentd (the browser operating loop)
  <your-skill>/SKILL.md         # drop in your own; picked up on the next message
```

---

## 4. The dependency rule (exact)

| Layer | May import | May NOT import |
|---|---|---|
| `domain/` | stdlib, `domain/` | application, infrastructure, presentation, main, any external lib |
| `application/` | `domain/`, its own `interfaces/` | infrastructure, presentation, main, external libs |
| `infrastructure/` | `application/interfaces/`, `domain/`, external libs | presentation, main |
| `presentation/` | `application/`, `domain/` | infrastructure internals, main |
| `main/` | everything | — |

Use `typing.Protocol` for the interfaces — an implementation then doesn't even need to import the
interface to satisfy it (loosest possible coupling).

---

## 5. Domain (pure)

Holds only **entities + value types**: `Message` (User/Assistant/ToolResult + content blocks),
`AgentEvent`. No IO, no libraries. This is the vocabulary every other layer speaks. Tiny and stable.

---

## 6. Application — interfaces + use-cases

**Interfaces** (the contracts; what the use-cases need from the outside):

```python
# application/interfaces/agent_engine.py  — the swappable brain
class AgentEngine(Protocol):
    async def run(self, *, messages, system_prompt, tools, events, abort, llm=None) -> list[Message]: ...

# application/interfaces/llm.py  (stateless; gets full messages each call)
class LLMService(Protocol):
    async def stream(self, *, model, system_prompt, messages, tools, abort) -> AsyncIterator[dict]: ...

# application/interfaces/memory.py
class MemoryRepository(Protocol):
    async def load_history(self, session_id) -> list[Message]: ...
    async def append(self, session_id, message: Message) -> None: ...
    async def remember(self, session_id, item) -> None: ...
    async def recall(self, session_id, query, k) -> list: ...
    async def get_profile(self, session_id): ...
    async def forget(self, session_id, item_id): ...

# application/interfaces/tools.py
class Tool(Protocol):
    name: str; description: str; parameters: dict; concurrency: str
    async def execute(self, call_id, params, abort): ...
class ToolRegistry(Protocol):
    def all(self) -> list[Tool]: ...
    def get(self, name) -> Tool | None: ...

# application/interfaces/skills.py  — loadable playbooks (know-how, NOT actions)
class Skill(Protocol):
    name: str          # short id (folder name by default)
    description: str   # one line: WHEN to use it (matched against the request)
    path: str          # absolute path the agent reads on demand (via the read tool)
class SkillRegistry(Protocol):
    def all(self) -> list[Skill]: ...    # read fresh each turn; only descriptions go in the prompt

# application/interfaces/events.py
class EventStream(Protocol):
    async def emit(self, event: AgentEvent) -> None: ...

# application/interfaces/policy.py
class PolicyService(Protocol):
    def authorize(self, *, tool, args, ctx): ...   # ALLOW | DENY(reason) | NEEDS_APPROVAL(reason)
class ApprovalService(Protocol):
    async def request(self, *, tool, args, reason) -> bool: ...   # human Yes/No

# application/interfaces/auth.py / metering.py
class AuthService(Protocol):
    async def verify(self, token) -> "Principal": ...    # user id, tier, entitlements
class MeteringService(Protocol):
    async def record(self, usage) -> None: ...
```

**Use-case** (the orchestration — real code, but only calls interfaces):

```python
# application/services/agent_service.py
class AgentService:
    def __init__(self, engine: AgentEngine, memory: MemoryRepository,
                 tools: ToolRegistry, llm: LLMService):
        self._engine, self._memory, self._tools, self._llm = engine, memory, tools, llm

    async def handle_message(self, session_id, text, events: EventStream, abort):
        history = await self._memory.load_history(session_id)        # read (local)
        history.append(UserMessage(text))
        await self._memory.append(session_id, history[-1])
        memories = await self._memory.recall(session_id, text, k=8)
        system_prompt = build_system_prompt(memories, ...)
        new = await self._engine.run(messages=history, system_prompt=system_prompt,
                                     tools=self._tools.all(), events=events,
                                     abort=abort, llm=self._llm)
        for m in new:
            await self._memory.append(session_id, m)                 # persist (encrypt -> vault)
```

The use-case knows **interfaces only** — never `litellm`, never a DB, never the web server.

---

## 7. Infrastructure — the implementations

Each file here **implements one interface** and is the only place external libraries / IO live:
`infrastructure/llm/litellm.py` (calls LiteLLM), `infrastructure/memory/local_store.py` (disk),
`infrastructure/tools/*` (file IO, subprocess, HTTP, Playwright),
`infrastructure/skills/file_skills.py` (scans the `skills/` folder),
`infrastructure/engine/native.py` (the reason→act loop + continuation/verify),
`infrastructure/policy/*`, `infrastructure/controlplane/*` (outbound HTTP to your cloud).

> The reason→act **loop is here**, not in the application layer — because the engine is swappable, it
> is one implementation (`engine/native.py`) of the `AgentEngine` interface.

> **Tools vs Skills** — two different things, both swappable behind their own interface. A **tool**
> is a callable *action* (read, exec, browser) with a JSON schema; its schema is always in context. A
> **skill** is a markdown *playbook* (`SKILL.md`) — *know-how*, not an action. The prompt builder asks
> `SkillRegistry.all()` and lists only each skill's one-line description (progressive disclosure);
> when a request matches, the agent reads that skill's full body **on demand using the ordinary `read`
> tool**, then follows it. So a skill needs **no new tool and no core change** — adding one is dropping
> a file in `skills/`. This is how you teach domain workflows (a browser routine, "export from
> Photoshop", "fill this form") with no code and no prompt bloat. agentd ships one — `browser-automation`
> — which is why the browser operating-loop is no longer hardcoded in the prompt: it became a skill.

---

## 8. Presentation — delivery (how requests get in)

`presentation/gateway/websocket.py` is the **localhost server** (token-gated). On `chat.send` it builds
an `EventStream` implementation and calls `AgentService.handle_message(...)`, streaming events back.
`presentation/cli/` and `presentation/channels/*` are the other doors. Presentation converts outside
input into a use-case call — and contains no business logic itself.

---

## 9. Main — the composition root (the only place concretes are named)

```python
# main/container.py
def build_agent_service(config) -> AgentService:
    engine = _make_engine(config)      # native (default) / claude_sdk / langgraph
    llm    = _make_llm(config)         # litellm / fake
    memory = _make_memory(config)      # local_store now, cloud_bank later
    policy = _make_policy(config)
    approvals = UiApproval(...)
    raw_tools = build_builtin_tools(config)
    tools  = ToolRegistryImpl([GuardedTool(t, policy, approvals) for t in raw_tools])  # guardrails
    return AgentService(engine, memory, tools, llm)
```

- **Config-driven swap:** change `config.engine` / `config.memory` / `config.llm` → a different
  implementation is built; the use-cases and domain are untouched.
- **Guardrails wired here:** each tool is wrapped in `GuardedTool` so policy/approval run before
  execution without the use-cases knowing.
- `main/main.py` = config → `build_agent_service` → start the gateway.

---

## 10. Runtime flow (how the layers interact)

See `request-flow.puml`. In short: **Presentation** (gateway) → **Application** (`handle_message`) →
read **Infrastructure** memory → **AgentEngine** loop { stream **LLM** (remote) → on tool calls:
**Policy** authorize → **Approval** if sensitive → **Tools** run locally → append → back to the LLM }
→ persist memory (encrypt → vault) → report metering → stream the answer to the UI. The LLM is
**stateless**; the engine re-feeds the full `messages[]` each iteration.

---

## 11. Mapping from v2 today (the refactor is small)

| v2 file | Goes to |
|---|---|
| `types.py` | `domain/messages.py` |
| `events.py` | `domain/events.py` (+ `application/interfaces/events.py`) |
| `loop.py` + `incomplete_turn.py` | `infrastructure/engine/native.py` (+ `application/interfaces/agent_engine.py`) |
| `llm.py` | `infrastructure/llm/litellm.py` (+ `application/interfaces/llm.py`) |
| `tools/__init__.py` `Tool` | `application/interfaces/tools.py`; tools → `infrastructure/tools/*` |
| `skills/*/SKILL.md` (content) | `application/interfaces/skills.py` + `infrastructure/skills/file_skills.py` |
| `session.py` | `infrastructure/memory/local_store.py` (+ `application/interfaces/memory.py`) |
| `gateway.py` | `presentation/gateway/websocket.py` |
| `config.py` / `__main__.py` | `main/config.py` / `main/container.py` / `main/main.py` |
| (none yet) | `application/interfaces/policy.py` + `infrastructure/policy/*` + `application/interfaces/auth.py` + `infrastructure/controlplane/*` |

v2 is ~80% there: the loop already takes `stream_fn` / `tools` / `on_event` (interfaces in disguise),
and tests already inject a **fake** LLM. We're formalizing the boundaries, not rewriting logic.

---

## 12. Why this is clean + scalable + swappable

- **Swappable:** every external thing sits behind an interface — LLM, memory backend, **agent engine**,
  tools, **skills**, policy, transport. Change the implementation (one config value); inner layers untouched.
- **Decoupled:** the dependency rule guarantees Domain/Application can't couple to a library or a UI.
- **Testable:** run the whole use-case with a **fake** LLM + in-memory repository + a capturing
  EventStream — no network, no model (v2 already does this).
- **Scalable:** the inner layers hold **no hidden state** (state lives behind `MemoryRepository`), so
  many sessions run concurrently and you can move memory to a shared DB or split the gateway into its
  own service without touching Domain/Application.
- **Extensible:** add a tool, an LLM provider, a channel, an engine = add an implementation; never
  edit the inner layers.

---

## 13. Keep it clean over time

Add **`import-linter`** (a CI check) that fails the build if `domain/` imports anything outer, or if
`application/` imports a concrete `infrastructure/` module. That one check stops the layers from
rotting into a tangle.

---

## 14. Status (have / net-new)

| Interface | Status in v2 |
|---|---|
| AgentEngine | native loop exists; formalize as an interface |
| LLMService | ✅ LiteLLM |
| ToolRegistry | ✅ registry |
| SkillRegistry | ✅ FileSkillRegistry (scans `skills/*/SKILL.md`); ships `browser-automation` |
| MemoryRepository | basic JSONL; generalize + add cloud bank |
| EventStream | ✅ via on_event/gateway |
| PolicyService / ApprovalService | none — net-new (chokepoint exists in `_execute_tool_calls`) |
| AuthService / MeteringService | none — net-new (control-plane client) |
