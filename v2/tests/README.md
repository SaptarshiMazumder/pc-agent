# Test tiers

Tests are tiered by directory. The tier marker (`unit` / `integration` / `e2e`) is
auto-stamped from the directory by `conftest.py` — never add it by hand; a new file is
tiered by where you put it.

| Tier | What it is | Boundary rule | Runs |
| --- | --- | --- | --- |
| `unit/` | One component in isolation; all I/O faked | No composition across layers | Every push/PR (blocking) |
| `integration/` | Real components wired together in one process (Gateway RPCs, AgentService, the agent loop, stores + services, pipelines) | May cross layers; never crosses a process or network boundary (exceptions opt in via `live` / `browser` / `computer` markers and are excluded from Stage 1) | Every push/PR (blocking) |
| `e2e/` | Boot the real daemon as a subprocess, connect a real client, assert across the wire | Crosses the process boundary on purpose | Not in Stage 1 yet; run explicitly |

Commands (from `v2/`):

```
pytest tests/unit -q
pytest tests/integration -m "not live and not browser and not computer" -q
pytest tests/e2e -m e2e            # requires nothing external; boots its own daemon
```

`v2/scripts/verify.ps1` runs the first two as blocking gates and mirrors
`.github/workflows/ci-fast-tests.yml` exactly. The browser/live/computer-marked tests run
in `.github/workflows/ci-slow-tests.yml` (on merge to develop/main, or on demand).

## Where does a new test go?

- Faking every collaborator? → `unit/`.
- Constructing real components and letting them talk in-process? → `integration/`.
- Starting the daemon / talking over a socket / driving the packaged app? → `e2e/`.
