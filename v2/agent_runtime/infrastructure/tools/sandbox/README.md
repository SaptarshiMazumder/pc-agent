# The plugin sandbox — read it like a book

This package is the **trust boundary for third-party plugin code**: tools that ride inside a
marketplace agent's own package (`agents/<id>/plugins/`). Our own runtime tools (`v2/plugins/`) and
agents you authored on this machine are never touched — see `classify.py`. The whole thing is one
idea: **an untrusted tool doesn't run in the daemon; it runs in a box that has no keys, no network,
and can only see its own workspace — and when it needs a model or an HTTP call, it *asks the host*
instead of dialing out.**

> ⚠️ **Name clash, read this first.** There is a SECOND, unrelated thing called "sandbox":
> `agent_runtime/infrastructure/sandbox/` (`LocalSandbox`) and `application/interfaces/sandbox.py`
> (`Sandbox` protocol). That is an **old, dormant seam (S17)** for future Docker/SSH *exec*
> isolation — **never wired up**, nearly empty, and NOT this package. If you opened
> `infrastructure/sandbox/` and it looked empty, that's why. Ignore it (or delete it).

---

## Reading order

### Ch. 0 — the vocabulary (ports + domain, outside this dir)
| File | What it is |
|---|---|
| `application/interfaces/plugin_sandbox.py` | The two **ports**: `PluginSandbox` (run a tool in isolation) and `CapabilityResolver` (decide the grant). Everything plugs into these. |
| `domain/sandbox.py` | The value objects: `CapabilityGrant` (what a run may touch), `PluginOrigin` (FIRST_PARTY / THIRD_PARTY_BUNDLE), `DENY_ALL`. |
| `domain/sandbox_net.py` | Net helpers: host matching, allowed schemes, `${SECRET}` placeholder checks. |

### Ch. 1 — the decision: *who* gets sandboxed, and *how*
| File | What it is |
|---|---|
| `classify.py` | **Start here.** `classify_origin` decides trust by PROVENANCE (is the agent in the install ledger?), `wrap_untrusted` wraps only the untrusted ones. |
| `backends.py` | `resolve_backend_name` + `build_plugin_sandbox`: WHICH backend, decided by **host capability** (`host_can_spawn_subprocess`), not deployment mode. |

### Ch. 2 — the wrapper (the transparent seam)
| File | What it is |
|---|---|
| `sandboxed_tool.py` | `SandboxedTool`: looks/acts exactly like a normal `Tool`, but routes `execute()` through a backend with a freshly-resolved grant. |
| `capabilities.py` | `DefaultCapabilityResolver`: the conservative grant — own workspace r/w, own agent folder read-only, no net, no secrets, a model only if the tool declares it. |

### Ch. 3 — the backends (where isolation actually happens)
| File | What it is |
|---|---|
| `local.py` | `LocalPluginSandbox`: in-process passthrough. **No isolation.** The fallback when a host can't spawn, and for first-party dev. |
| `subprocess_backend.py` | `SubprocessPluginSandbox`: **the real one.** Host side — spawns the child, strips env, sets `deny_paths`, rlimits/uid-drop, serves model+fetch, enforces the deadline. |

### Ch. 4 — the child (what runs inside the box)
| File | What it is |
|---|---|
| `worker.py` | The child entry (`python -m …sandbox.worker`): one job in, one result out; claims fd 1 so a plugin's `print` can't forge the protocol. |
| `child_guard.py` | The in-child **audit hook**: denies fs/net/spawn/ctypes outside the grant. Interpreter-level (its own header is honest about the limit). |
| `child_models.py` | Child-side model shim: a model call becomes a `model_request` frame to the host. |
| `child_net.py` | Child-side fetch shim: `outbound.fetch` becomes a `fetch_request` frame to the host. |

### Ch. 5 — the wire + the host services
| File | What it is |
|---|---|
| `protocol.py` | The parent↔child JSON-lines format: job, `update`, `model_request/response`, `fetch_request/response`, `result`. |
| `model_broker.py` | Host serves a sandboxed tool's model calls: grant check → clamp → **meter the account** → call. Credential never crosses. |
| `fetch_broker.py` | Host serves its HTTP: allowlist enforced, `${SECRET}` substituted host-side, size/time caps. |
| `stdout_capture.py` | Scrubs + caps the child's stderr for logging as the plugin's output. |
| `__init__.py` | The package index / public exports. |

---

## Where it's turned ON and wired (outside this package)
| File | What it holds |
|---|---|
| `config.py` | The knobs: `sandbox_untrusted_plugins` (**default ON**), `sandbox_plugin_backend`, `sandbox_child_uid/gid`, `sandbox_env_passthrough`, `sandbox_net_allow/deny`, `sandbox_fetch_limits`, `sandbox_trusted_plugins/agents`, `sandbox_untrusted_agents`. |
| `main/container.py` | The wiring: `build_plugin_sandbox` + `_agent_private_tools()` → `wrap_untrusted(… installed_agent_ids …)`. The composition root owns the install ledger (what counts as third-party). |

## Related but SEPARATE — the tenant FS fence (NOT this package)
This guards our **own built-in tools** (read/write/edit); the plugin sandbox guards **third-party
plugin code**. Two different walls to the same workspace — don't conflate them.
| File | What it is |
|---|---|
| `application/run_context.py` | `RunContext.read_roots` / write clamp — the per-run fence values. |
| `application/write_scope.py` | `check_read` / `check_write` — the app-level guard the built-in fs tools call. |
| `infrastructure/user_state.py` | `tenant_scope` — computes the fence from the account's on-disk layout. |

## Tests (the executable spec)
`tests/unit/test_plugin_sandbox.py` (classify + wrap) · `test_plugin_sandbox_subprocess.py` (real
backend end-to-end + backend selection) · `test_sandbox_model_broker.py` · `test_sandbox_fetch.py`
· `test_sandbox_force_untrusted.py`.

## Not runtime — the author-time check (agent-builder)
`agents/agent-builder/.../domain/sandbox_rules.py` + `sandbox_contract.py` check that an agent
*declares* its sandbox needs correctly **before publish**. Different concern from enforcing them at
runtime, which is everything above.
