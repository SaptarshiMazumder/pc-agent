# Validation of the OVH implementation

Checked locally on Windows, October 12, 2026. No OVH resources were provisioned,
no real credentials were copied and no AWS/Neon resources were modified.

- New OVH lifecycle, admission, network configuration, ownership, cancellation,
  encrypted publishing, one-time R2 uploads, storage token separation and worker
  proxy readiness suite: **24 passed**.
- Targeted publishing adapters/intake/roster/admin plus OVH suite, before the final
  two proxy-readiness tests: **152 passed, one existing failure deselected**.
- Existing sandbox/plugin/model-broker contract selection: **123 passed**, one
  existing fixture failure described below.
- Ruff checks/formatting passed for new implementation files. Existing FastAPI
  `Body(...)` lint warnings in the older admin module remain informational.
- Docker Compose config validated with an isolated, generated **fake** secret
  directory. Workflow YAML, Bash scripts and image workflow Bash syntax validated.
- Production flavor URL synchronization and `--check` passed without Terraform.
- Configuration, root-key import and production preflight passed using a synthetic
  keypair and dummy credentials. No connection to Neon or a provider was made.
- `git diff --check` passed.

The older publishing/admin suites have **13 failures reproduced on checkpoint
156b3c741595293c61a3b66a89e5af2cab90e196**: eight publish-handler fixtures/expectations,
four HTTP publisher tests targeting the former client-side installer/one-request
contract, and the unconfigured admin key-panel expectation. The additional sandbox
model-broker failure (`SimpleNamespace` missing `name`) was also reproduced against
that checkpoint. These **14 baseline failures** are not reported as passing.

Docker Engine is not running in this Windows session. Image builds, Caddy config
execution, actual gVisor networking/firewall, Chromium/MCP compatibility, real
R2 transport, Google/Dodo callbacks, workload headroom and recovery/cutover checks
are **pending on the Linux host**. Run `host_smoke.py` and the acceptance sequence
in [the setup runbook](README.md) before production use. Passing unit tests does
not establish live deployment or feature parity.
