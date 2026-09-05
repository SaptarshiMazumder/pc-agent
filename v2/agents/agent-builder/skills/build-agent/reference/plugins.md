# Reference — `plugins/`, the agent's own tools

Writing a private tool: the format, the sandbox contract it must satisfy to survive being
installed on someone else's machine, calling an external API, calling a model, and Python
dependencies. Read it before `create_tool`.

---

## plugins/ — the agent's own tools

### A REPEATABLE JOB BELONGS IN CODE

If the agent will do something more than once — the same fetch, the same comparison, the same
five calls in the same order — that is a tool. One call, the same answer every time, nothing
re-derived. The alternative is an agent that works out how to do its own job on every single
turn, slowly and slightly differently each time.

**Propose it, and wait for a yes.** The owner will not know to ask for a tool. So you raise it:
what it would do, what it needs declared, and what it saves. Recommending is your job; deciding
is theirs. Do not write it and present it afterwards.

**Reaching a third-party service is a separate question** — it decides HOW the tool works, not
whether to write one:

| the service authenticates with… | do this |
|---|---|
| a bearer token or OAuth (most: GitHub, Notion, Slack, Stripe, Linear…) | **write the tool.** Declare `[sandbox] net` + `secrets`, put `${NAME}` in a header — see "Calling an external API" below |
| a SIGNED request (AWS SigV4 and friends) | **`[[mcp]]`** — the signature is computed FROM the secret, and the host substitutes values rather than signing, so a placeholder cannot stand in |
| — and it already has a good MCP server | **`[[mcp]]`** — one block gets the whole maintained toolset, and it travels with the agent |
| nothing: local logic, parsing, computing, the workspace | **write the tool** |

**A SKILL is the fallback, and you have to earn it.** Markdown is right when the work needs
judgement that changes with the situation. It is NOT the answer to "this was hard to write as a
tool" — try the tool first, and if it cannot work, say which of the rows above stopped you.

When the answer is `[[mcp]]`, the repeatable procedure still has to live somewhere: a tool cannot
call an MCP tool, so it goes in a skill, naming the exact tool names `mcp_status` reported.

### The format

`agents/<id>/plugins/<plugin-id>/`. Same format as a global plugin, but visible only to
this agent. Two files minimum:

**`plugin.toml`**

```toml
id = "example-kit"
name = "Example Kit"
kind = "native"                  # "native" | "mcp"
entry = "example_kit:register"   # "<module>:<callable>" — module is in THIS folder
# description = "..."            # optional one-liner
# scripts = ["helper.py"]        # optional declared helper files
# data    = ["table.json"]       # optional declared data files
# [requires]                     # gate: plugin is SKIPPED unless satisfied
# bins = ["ffmpeg"]              # binaries that must be on PATH
# env  = ["SOME_API_KEY"]        # env vars that must be set
```

**`example_kit.py`** — the plugin folder is added to `sys.path`, so import siblings by
bare module name.

```python
from agent_runtime.application.interfaces.tool import Tool, ToolResult


class LookupEntryTool(Tool):
    name = "lookup_entry"                 # what the model calls
    label = "Lookup Entry"                # UI label
    default_retryable = True              # False for anything side-effecting
    description = "Look one entry up by name. Use when asked about a stored item."
    parameters = {
        "type": "object",
        "required": ["city"],
        "properties": {"city": {"type": "string", "description": "City name"}},
    }

    async def execute(self, tool_call_id, params, abort, on_update=None):
        city = params.get("city", "")
        try:
            ...
            return ToolResult.text(f"{city}: 21C, clear")
        except Exception as e:  # never let a tool crash the loop
            return ToolResult.text(f"lookup_entry failed: {e}", is_error=True)


def register(api, ctx):
    api.register_tool(LookupEntryTool())
```

### A tool NEVER works out where the workspace is — it asks

```python
from agent_runtime.application.run_context import current_workspace

folder = Path(current_workspace(".")) / "outputs"
```

**There is no fixed path to an agent's workspace, and the agent directory is not it.** The
runtime picks it per run: a signed-in user gets their own, a chat inside a project gets the
project's shared one, and a hosted deployment gives every account a separate one. Two runs of the
same agent can have two different workspaces, both correct.

So a path built from the agent's own directory — or from `__file__`, or from anything the model
saw in an `ls` — points at a folder that is real, writable, and **not the one anything else
reads**. The failure is silent and completely convincing: the write succeeds, the file is on
disk, and every tool and every panel that lists the workspace reports nothing there. Observed:
an agent wrote its output into its own definition folder, then spent three tool calls and a page
of reasoning working out why its own listing tool could not see a file it had just written.

The same rule applies to the app: `workspace.list` reads the run's workspace, so a file written
outside it is invisible in the window too.

**And when a tool finds nothing, it says WHERE it looked.**

```python
return ToolResult.text(f"No workflows in {folder}")     # ends the question
return ToolResult.text("No workflows yet")              # starts an investigation
```

An empty result that names its folder turns exactly this class of bug into one line of output.
It costs nothing and it is the difference between the agent noticing a path mismatch and the
agent theorising about one.

### An agent's own tools become UNTRUSTED once someone installs the agent

Trust is decided by **provenance**: an agent's private tools are classified
`THIRD_PARTY_BUNDLE` when the marketplace ledger says that agent arrived in a `.agentpkg`.
Locally — an agent you just authored, or one that shipped with the product — the tools are
trusted. Owning tools is not itself suspicious.

The catch is what happens on **someone else's machine**. Installing your agent records it in
their ledger, so every tool under `agents/<id>/plugins/` is untrusted over there. When
sandboxing is enabled (`AGENTD_SANDBOX_PLUGINS=1`), such a tool is granted:

|          |                                                            |
| -------- | ---------------------------------------------------------- |
| files    | the run's workspace only                                   |
| network  | **yes, any host** — no socket of its own; `fetch` asks the HOST, which makes the request. No declaration needed for reach; only an operator's own deny/allow config can refuse a host |
| secrets  | **yes, by name** — it writes `${NAME}` and the host substitutes the value (`[sandbox] secrets`). The code never SEES the key |
| models   | only if the tool declares `needs_model = True` — see below |
| **processes** | **never, and there is no way to ask.** `subprocess.Popen`, `os.system`, `os.exec*` are denied outright |

**Spawning is the one with no way out.** Network, secrets and models all invert — the plugin
asks, the host performs. There is no equivalent for "run this program": an HTTP request has a
shape you can send over a pipe and get an answer back; "launch a program on the user's machine"
does not.

So a private tool that needs to start a process **cannot exist**. Two ways forward:

1. **The capability you want may already be a shared tool.** Before writing any private tool,
   look at what the catalog already offers. Shared tools are the daemon's own code and are
   never sandboxed, so they can do the things a private tool cannot. Reaching one is a
   `[tools] allow` entry in the agent's `agent.toml`, naming the existing tool. A private tool
   is for something no shared tool does; writing one that duplicates an existing tool costs you
   the sandbox for no gain.
2. **If nothing in the catalog does it, say so and stop.** You cannot author a shared tool —
   `create_tool` requires an owning `agent` and always writes a private, sandboxed one. That is
   deliberate: a shared tool is machine-wide code that every agent inherits and nothing sandboxes,
   so adding one is the operator's decision, made by hand, not a step in building an agent. Tell
   the user what the agent would need and let them decide; do not design around the fence.

The rule that follows: **a private plugin never opens a socket and never reads a key.** It asks
the host to do both. `import requests` and `os.environ[...]` work perfectly for the author and are
dead for every buyer — so `create_tool` refuses to write either into an agent-scoped tool. That is
not style; it is the difference between a tool that ships and one that is dead on arrival.

### Calling an external API from a private tool

**The network is open** — a plugin fetches any host with no declaration. Declare only the
credential NAMES in the plugin's own `plugin.toml`:

```toml
[sandbox]
secrets = ["ACME_API_KEY"]     # names only — the value never reaches the plugin
```

Then call it:

```python
from agent_runtime.infrastructure.net.outbound import fetch

res = fetch(
    "https://api.acme.com/v1/things",
    headers={"Authorization": "Bearer ${ACME_API_KEY}"},
)
if not res.ok:
    return ToolResult.text(f"acme failed ({res.status}): {res.error or res.text}", is_error=True)
data = res.json()
```

`${ACME_API_KEY}` is a **placeholder, not a value**. The host substitutes it at the last moment
and makes the request itself, so the plugin code cannot read the key or keep it. Unsandboxed the
same call runs directly and reads the name from the environment — one code path, both worlds.

`[sandbox]` is **not** `[requires]`. `[requires]` is a gate: _skip this plugin unless present_.
`[sandbox]` is a request: _substitute these names for me when I ask_.

Two things to tell the user when you write one:

- **Where the key comes from**: the daemon's environment, or the agent's own settings page writing
  `plugins.<plugin-id>.secrets.<NAME>`. That is how BYOK works inside a shipped agent.
- An operator's config (`sandbox_net_allow` / `sandbox_net_deny`) can still refuse hosts at the
  deployment level — a hosted daemon fencing off its own internal endpoints. A refused host
  produces a refusal naming it, not silence.

**A host the USER supplies.** An agent that wraps a service the person running it hosts (their
own server, their database, an internal API) does not know the host when it is written. Name the
SETTING — `[sandbox] net` is the declaration that makes a `${SETTING}` legal inside a URL:

```toml
# agent.toml
[[settings]]
key  = "SERVICE_URL"
kind = "url"
help = "Where your instance runs, e.g. https://abc.example.com"
```
```toml
# plugins/<id>/plugin.toml
[sandbox]
net = ["${SERVICE_URL}"]      # lets ${SERVICE_URL} appear in this plugin's URLs
```
```python
res = fetch("${SERVICE_URL}/v1/jobs", method="POST", json=payload)
```

The host resolves from that setting at call time, for whoever is calling — two people on one
hosted daemon each reach their own instance. An EMPTY setting fails closed with a message naming
the field, so an unconfigured install is a fixable error, not silence.

**Get this wrong and nothing tells you until a buyer hits it.** A plugin that builds its URL from
`${SOME_URL}` without declaring that setting in `[sandbox] net` is refused at call time — the
placeholder is treated as an undeclared name. `validate_agent` reports `UNTRUSTED_HOST_FROM_SETTING`
and packaging refuses, because the failure is otherwise invisible until the agent is in someone
else's hands.

### Calling a model from a private tool

A sandboxed tool never holds a PROVIDER key — those are the daemon's, not the agent's, and no
`[sandbox] secrets` declaration reaches them. It does not need to: the call is **inverted** — the
tool asks, and the host performs it, exactly as `fetch` does for a declared host. Use the one route that works
in every mode (hosted, BYOK, and a local model):

```python
from agent_runtime.infrastructure.llm.oneshot import text_complete   # or vision_complete

summary = text_complete(model=None, prompt=prompt, max_tokens=400)
```

Inside the sandbox that name resolves to a shim that hands the request to the host. Same
signature, same return type, so the tool behaves identically whether or not it is sandboxed.
`model=None` lets the host use the model this tool resolves to — pin one with
`[plugins.<pid>.tools.<tool>] model = "..."` in the agent's `agent.toml`.

The tool must also carry **`needs_model = True`** as a class attribute. That flag is the entire
authorisation: without it the sandbox grants zero models and the host refuses every call with
_"not granted"_. Add `model_kind = "vision"` too if it reads images.

`create_tool` sets both for you by reading the code — you never declare them. If you author a
plugin by hand with `write`, you must add them yourself, and `validate_agent` will tell you
(`UNTRUSTED_MODEL_UNDECLARED`) when you forget.

Spend goes to the account running the agent, attributed to your plugin, and is capped per tool
run (`sandbox_model_limits`: 8 calls, 4096 output tokens, 120s by default).

The sandbox is ON by default, desktop and hosted alike, and the grant IS enforced today. A tool
that reaches for a socket, a subprocess or `os.environ` works only where the agent is trusted —
its author's own machine and the platform catalogue — and is refused for everyone who installs
it. Write for the sandbox from the first line; there is no later day when this starts mattering.

### Python library dependencies

There is **no pip field in `plugin.toml`**. `[requires]` covers binaries and env vars only.
For a third-party Python package, import it **lazily inside the function** and fail with
guidance, so the plugin still loads when the package is absent:

```python
try:
    import pandas as pd
except ImportError:
    return ToolResult.text(
        "this tool needs `pandas` — install it into the agentd runtime", is_error=True
    )
```

(A plugin distributed _as_ a pip package is a different thing, declared at the bundle level
with `source = "pip"` — not applicable to an agent-private plugin.)

**New private plugins are discovered at startup.** After writing one, call `reload_agent`
if it exists; otherwise tell the user a restart is needed.

