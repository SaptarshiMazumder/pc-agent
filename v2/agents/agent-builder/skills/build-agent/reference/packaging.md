# Reference — packaging and publishing

What `package_agent` and publishing refuse, and the versioning rules that decide whether an
install supersedes an older copy.

---

## Packaging rules

Shipping is two steps, and only the first happens here:

```
agents/<id>/  --package_agent-->  <id>-<version>.agentpkg  --installer build-->  <id>.exe
```

The **`.agentpkg`** is the shareable unit: a zip holding `bundle.toml` + the whole agent
directory + any shared plugins it vendors. Anyone can install one (`marketplace.install`), and
it is what an `.exe` build consumes. `package_agent` produces it. Building the `.exe` itself
needs node + electron-builder + a repo checkout, so it is not a chat operation.

Author with packaging in mind:

- `workspace/`, `sessions/` and `clients/` are **excluded** from the package. Never put
  anything the agent _needs_ in `workspace/` — that is user data, and on upgrade it is the one
  directory preserved while the rest of the definition is replaced.
- The agent's own `plugins/` **are** included — they live inside the agent directory.
- Only agents with an `[app]` section can become a product exe.
- **Bump `version` in `agent.toml` on every shipped change.** It is the bundle's version, and
  installs supersede BY VERSION — re-packing the same number will not replace an existing copy.
- `bundle.toml` is optional and hand-written. Add one only for publisher-facing facts
  (`publisher`, `entitlement`, a bundle id that differs from the agent id, shared plugin
  dependencies). If it declares `version`, it **outranks** `agent.toml` — so normally leave
  that key out and let the agent's own version rule.
