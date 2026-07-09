# CI/CD Build-Out Plan — pc-agent (`v2/`)

> **Status:** Phase 0 ✅ · Phase 1 ✅ (ruff green, mypy informational, import-linter clean, 704 tests, `verify.ps1`; eslint deferred) · **Phase 2 ci.yml WRITTEN** (`.github/workflows/ci.yml`), push to activate · **Target:** GitHub Actions · **Scope:** Stages 1–4 · **Adds:** ruff + mypy

## Phase 1 completion notes (what actually shipped)

- **ruff** — config in `pyproject.toml`; baselined (136 auto-fixes + 275 files reformatted). Remaining 42: 6 real F-code items fixed/flagged, 36 stylistic **ratcheted** into `[tool.ruff.lint] ignore` (a visible TODO list to burn down). ruff is a **green blocking gate**.
- **3 real bugs surfaced + flagged in place** (`# noqa` + `# TODO`), for later: `export_pptx_tool.py` background never drawn; `figures_overlay.py` arrowhead style ignored; `render_overlay_tool.py` **duplicate `"scale"` dict key** (one param silently lost).
- **import-linter** — found + FIXED a real violation: `presentation.gateway → main.list_plugins`. Extracted the shared catalog helpers to a new floor-3 module `agentd/infrastructure/plugins/catalog.py`; both CLI and gateway now import DOWN into it. Contracts: 2 kept, 0 broken.
- **mypy** — lenient config, scoped to `agentd/`; **156 errors, informational** (`continue-on-error` in CI; `verify.ps1` shows the count but doesn't gate). Burn down, then promote to blocking.
- **verify.ps1** (`v2/scripts/verify.ps1`) — one local command = the whole Stage-1 gate; auto-finds the root `.venv`; ASCII-only (PS 5.1 reads BOM-less `.ps1` as ANSI).
- **eslint (1.5) DEFERRED** — desktop gate is `typecheck` + `build` (both green) for now; add eslint later (it will surface its own backlog, like ruff did).
> **Working doc** — check boxes off as we go. Do it phase by phase; each phase is independently shippable.

## Environment notes (discovered while doing Phase 1)

- **Canonical interpreter = the ROOT `pc-agent\.venv`.** It holds `agentd` (editable), croniter, ruff, mypy, pytest, and runs the daemon + Claude Code MCP servers (headroom, uv). The nested `v2\.venv` was a redundant partial clone (no `agentd`) → **deleted**, keeping one venv.
- **Activate the venv before running anything.** With no venv active, `python` on PATH resolves to the **system** Python (`AppData\Local\Programs\Python\Python311`), which lacks the project deps. That mismatch produced *phantom* test failures (see below), not real bugs. In `v2/`: `& "..\.venv\Scripts\Activate.ps1"`, then confirm `python -c "import sys; print(sys.executable)"` points at `.venv`.
- CI is immune to this by construction: one `setup-python` interpreter, no system-Python fallback.

## Test-suite triage (the 10 failures from the first full run)

| Cluster | Count | Root cause | Resolution |
|---|---|---|---|
| `test_cron.py` | 3 | Ran under system Python (no croniter); `cron_valid` swallows the ImportError → "invalid cron" | **Env, not code.** Passes under root venv. ✅ |
| `test_browser_persistent.py` | 2 | Stale tests — the "uniform config-driven capabilities" commit moved `Config.browser_persistent` → config-only knob `plugins.browser.tools.browser.persistent` (read via `browser_knob`); no more `AGENTD_BROWSER_PERSISTENT` env | **Rewrite the tests** against `browser_knob`. |
| `test_browser_*` (launch) | 3→4 | Genuinely drive a real Chromium; fail (not skip) locally because Playwright is installed. **Also** carry the same stale flat-config bug (their `_cfg`/`_provider` helpers set flat `browser_*` attrs the provider now reads via `browser_knob`) | **Marked `browser`** (per-test in cursor_profile; module-level in action_recovery both tests + smoke) → Stage-1 excludes. **STAGE-2 TODO:** fix flat→knob config in the 3 helpers (`test_browser_tool_smoke._provider`, `test_browser_action_recovery._cfg`, `test_browser_cursor_profile._cfg`) so they PASS under a real browser; verify eyes-on (esp. the cursor-scan assertion). |
| `test_computer.py` | 2 | **NOT infra** — fully mocked (`FakeProvider`). Stale test helper `_cfg` passes flat `computer_max_steps`/`computer_save_screenshots`, but the driver reads `computer_knob(config, "max_steps"/"save_screenshots")` (`plugins.computer.tools.computer.*`) after the refactor → defaults used → assertions fail | **Fix `_cfg`** to build the `plugins` knob shape. Same stale-test class as `browser_persistent`. No marker. |

## Overview

Two artifacts, chained:

| Target | Location | Toolchain | Output |
|---|---|---|---|
| **`agentd` Python daemon** (gateway + agents + plugins + CLI) | `v2/` | Python 3.11+, hatchling, pytest | a wheel → `agentd`/`jarvis` CLI |
| **Electron desktop client** | `v2/clients/desktop/` | Node/npm, electron-vite, electron-builder | Windows NSIS installer (`.exe`) |

The desktop installer **embeds** the Python daemon: `build-runtime.ps1` assembles embedded CPython + the agentd wheel into `runtime/agentd-env`, which electron-builder bundles. So a full release build is: **build wheel → embed runtime → build Electron → package installer → per flavor** (`core`, `figure-creator-studio`).

**Current state:** zero CI in the real project (all `.github/workflows` hits are under `reference/` or `node_modules/`). Greenfield.

### Pipeline shape

```
 PR opened ──────▶  [1] VALIDATE (fast, every push)          → ci.yml
                      ├─ Python: pytest (unit), import-linter, ruff, mypy
                      └─ Desktop: npm ci, tsc typecheck, eslint, build
                              │
 merge → develop ──▶  [2] INTEGRATE (slower, gated)           → integration.yml
                      └─ live/e2e tests (browser, llm, google) — needs API-key secrets
                              │
 tag v* on main ──▶  [3] BUILD & PACKAGE (matrix)             → release-build.yml
                      ├─ build agentd wheel
                      ├─ build-runtime.ps1 (embed CPython + wheel)
                      └─ electron-builder → NSIS installer, per flavor (core + studio)
                              │
                     [4] RELEASE / CD
                      ├─ GitHub Release + upload installers/wheel
                      ├─ (optional) code-sign the .exe
                      ├─ publish .agentpkg to the marketplace registry
                      └─ (optional) auto-update feed
```

---

## Phase 0 — Groundwork & decisions (before any YAML)

- [x] **0.1** GitHub remote & branch flow confirmed. `origin → https://github.com/SaptarshiMazumder/pc-agent.git`; branches `develop` (current) + `main`, both pushed. CI keys off `develop`/`main`.
- [x] **0.2** Monorepo convention locked: every job uses `defaults.run.working-directory: v2` (or `v2/clients/desktop`). **Critical** — [conftest.py](../../tests/conftest.py) inserts `v2/` and each `plugins/*` dir onto `sys.path`, so pytest **must** run from `v2/`. (Applied in Phase 2.)
- [x] **0.3** `.gitignore` audited. **No tracked build/env junk** (node_modules/.venv/out/dist/runtime/wheel-staging all clean). Added ignore rules for `.import_linter_cache/` + `.tokensave/*.db{,-wal,-shm}` (root `.gitignore`). **⚠ Manual step still pending:** the `.tokensave` DB was already committed, so untrack it (files stay on disk):
  ```
  git rm --cached .tokensave/tokensave.db .tokensave/tokensave.db-shm .tokensave/tokensave.db-wal
  ```
- [x] **0.4** Runner-OS strategy decided (recommended defaults):
  - Unit tests (Stage 1): `ubuntu-latest` — fast/cheap; conftest already guards the Windows proactor loop with `if sys.platform == "win32"`.
  - Optional `windows-latest` on push-to-develop only (exercise Windows paths without burning PR minutes).
  - Release build (Stage 3): **`windows-latest` mandatory** — `build-runtime.ps1` is PowerShell, target is NSIS.
- [x] **0.5** Version single-source-of-truth decided: **git tag `vX.Y.Z` wins.** Stage 3 `version-check` job fails the build unless `agentd/__init__.py` `__version__` (`0.1.0`) and desktop `package.json` `version` (`0.1.0`) both equal the tag.

---

## Phase 1 — Make the repo CI-able locally first (config, no workflows yet)

*Goal: every gate CI runs must first run green locally. ruff/mypy land here.*

- [x] **1.1** Added `[tool.pytest.ini_options]` to [pyproject.toml](../../pyproject.toml): `asyncio_mode = "strict"` (NOT auto — matches the existing `@pytest.mark.asyncio` decorators), `asyncio_default_fixture_loop_scope = "function"`, `testpaths = ["tests"]`, and registered markers `live` / `browser` / `computer` / `e2e`.
- [x] **1.2** Triaged the whole suite empirically (ran it, didn't guess). Outcome differed a lot from the filename guesses — the suite is overwhelmingly hermetic. Net result: **0 `live` tests needed**, **2 stale-test clusters fixed** (`browser_persistent`, `computer` — flat config → `*_knob`), **4 real-Chromium tests marked `browser`**. Stage-1 command `pytest -m "not live and not browser and not computer"` = **704 passed, 4 deselected**. See the triage table above.
- [ ] **1.3** Add `[tool.ruff]` to pyproject (lint + format). Set rule set, `line-length`, `exclude = [".venv", "reference", "v1"]`. Run `ruff check --fix` + `ruff format` once to baseline; commit the churn **separately**.
- [ ] **1.4** Add `[tool.mypy]` — start **lenient** (`ignore_missing_imports = true`, no `--strict`), scoped to `agentd/` only (not `plugins/`, not `tests/`). Ratchet later.
- [ ] **1.5** Desktop lint: add ESLint (flat config) + tsconfig-aware plugin to `v2/clients/desktop/`, plus an `npm run lint` script. `typecheck` already exists — reuse it.
- [ ] **1.6** Commit the npm lockfile. `npm ci` **hard-fails without `package-lock.json`.** Verify it exists in `v2/clients/desktop/`; if not, `npm install` once and commit it.
- [ ] **1.7** Add a local **verify** entrypoint (`Makefile` or `scripts/verify.ps1` at `v2/`) that runs the whole Stage-1 gate: `ruff check`, `ruff format --check`, `mypy agentd`, `lint-imports`, `pytest -m "not live"`. CI calls the same steps.

---

## Phase 2 — Stage 1 workflow: **Validate** (`.github/workflows/ci.yml`)

*The 20% that catches 80% of regressions. No secrets. Every PR + push to develop/main.*

- [ ] **2.1** Triggers: `pull_request` + `push` to `[develop, main]`, `paths: ['v2/**']`. Add a `concurrency` group to cancel superseded runs.
- [ ] **2.2** Job `backend` (`ubuntu-latest`, `working-directory: v2`):
  1. `actions/checkout`
  2. `actions/setup-python@v5` (3.11) + pip cache keyed on `requirements.txt`/`pyproject.toml`
  3. `pip install -e .[dev]` (the `dev` extra pulls pytest, pytest-asyncio, import-linter, `all` extras)
  4. `ruff check .` and `ruff format --check .`
  5. `mypy agentd`
  6. `lint-imports` ← runs existing [.importlinter](../../.importlinter) contracts (clean-layers + core-not-import-plugins)
  7. `pytest -m "not live" -q` (+ optional `--junitxml`)
- [ ] **2.3** Job `desktop` (`ubuntu-latest`, `working-directory: v2/clients/desktop`):
  1. checkout → `setup-node@v4` (Node 20) + npm cache
  2. `npm ci`
  3. `npm run lint`
  4. `npm run typecheck` (dual `tsc --noEmit`)
  5. `npm run build` (electron-vite build — proves compile; **no** installer here)
- [ ] **2.4** (Optional) Add `windows-latest` to `backend` on push-to-develop only (exercise Windows paths without burning minutes on every PR).

---

## Phase 3 — Stage 2 workflow: **Integrate / live tests** (`.github/workflows/integration.yml`)

*Tests that cost money & need the network. Never runs on fork PRs (secret-leak protection).*

- [ ] **3.1** Triggers: `push` to `[develop, main]` + `workflow_dispatch` + optional nightly `schedule`. **Not** fork `pull_request`.
- [ ] **3.2** Wire secrets (repo → Settings → Secrets): `GOOGLE_API_KEY` (google-genai) + any litellm provider keys. Expose only to this workflow.
- [ ] **3.3** Steps: install deps → `playwright install --with-deps chromium` (cache binaries) → `pytest -m "live or browser"` → optionally run [scripts/e2e_probe.py](../../scripts/e2e_probe.py) + [scripts/e2e_abort.py](../../scripts/e2e_abort.py) as smoke checks against a booted daemon.
- [ ] **3.4** Guard: `if: github.event.pull_request.head.repo.fork == false`.

---

## Phase 4 — Stage 3 workflow: **Build & Package** (`.github/workflows/release-build.yml`)

*Produces the shippable artifacts. The chained build.*

- [ ] **4.1** Triggers: `push` tags `v*` + `workflow_dispatch`.
- [ ] **4.2** Job `version-check`: parse tag `vX.Y.Z`; fail unless `agentd/__init__.py` `__version__` **and** desktop `package.json` `version` both equal `X.Y.Z` (enforces 0.5).
- [ ] **4.3** Job `wheel` (`ubuntu-latest`): `pip install build` → `python -m build --wheel` from `v2/`. Runs the custom hatch hook [scripts/hatch_build.py](../../scripts/hatch_build.py) (stages `_builtin_plugins` + `_data` into the wheel). Upload the `.whl` artifact.
- [ ] **4.4** Job `installer` (`windows-latest`, `needs: [wheel]`), matrix over flavor (`core`, `figure-creator-studio`):
  1. Download the wheel artifact.
  2. Run [scripts/build-runtime.ps1](../../clients/desktop/scripts/build-runtime.ps1) → embedded CPython + wheel installed into a real venv at `runtime/agentd-env` (per the header in [electron-builder.yml](../../clients/desktop/electron-builder.yml)). Cache the CPython download.
  3. `npm ci`
  4. `npm run dist:core` **and** `npm run dist:studio` (each = `electron-vite build && electron-builder --config <flavor yml>`). Configs exist: `electron-builder.yml` + `electron-builder.studio.yml`.
  5. Upload NSIS installers (`dist/core/*.exe`, `dist/studio/*.exe`).
- [ ] **4.5** Reserve a **code-signing hook point** (Windows cert secret + `electron-builder` `win.signtoolOptions`) so the `.exe` clears SmartScreen. No-op stub until a cert exists.

---

## Phase 5 — Stage 4 workflow: **Release / CD** (publish)

*Turns artifacts into a real release + distribution.*

- [ ] **5.1** GitHub Release job (`needs: [wheel, installer]`): on `v*` tag, create a Release, attach wheel + both flavors' installers, auto-generate notes.
- [ ] **5.2** Marketplace publish (`.agentpkg`): pack agent bundles (e.g. [figure-creator/bundle.toml](../../agents/figure-creator/bundle.toml)), **sign with the license private key** (secret; uses `cryptography`), publish to the registry. Covered by existing tests: [test_distribution.py](../../tests/test_distribution.py), [test_marketplace.py](../../tests/test_marketplace.py), [test_bundle.py](../../tests/test_bundle.py), [test_licensing.py](../../tests/test_licensing.py). ⚠️ **External dependency you don't have yet** — a hosted registry endpoint. Until it exists, this job just builds + validates the `.agentpkg` locally.
- [ ] **5.3** (Optional) Auto-update feed: configure electron-builder `publish` provider (GitHub Releases or S3). Deferred unless wanted now.

---

## Phase 6 — Hardening & governance (make CI *enforce*, not just report)

- [ ] **6.1** Branch protection on `main` + `develop`: require Stage-1 `backend` + `desktop` checks before merge. *This is what makes CI matter.*
- [ ] **6.2** Dependabot (`.github/dependabot.yml`): weekly pip + npm + github-actions updates.
- [ ] **6.3** Workflow hygiene: per-job `timeout-minutes`, caching (pip/npm/playwright/CPython), `concurrency` cancellation, least-privilege `permissions:` blocks.
- [ ] **6.4** (Optional) CodeQL (JS + Python) — model from `reference/openclaw-main/.github/workflows/codeql*.yml`.
- [ ] **6.5** Status badges in the README.

---

## Secrets inventory

| Secret | Used by | Needed for |
|---|---|---|
| `GOOGLE_API_KEY` (+ other provider keys) | Stage 2 | Live LLM/search tests |
| Windows code-signing cert + password | Stage 3 | Trusted `.exe` |
| License-signing private key | Stage 4 | Signing `.agentpkg` bundles |
| Registry/publish token | Stage 4 | Marketplace + auto-update |

## Open decisions

1. **Test matrix** — ubuntu-only for speed, or ubuntu+windows on PRs?
2. **mypy strictness** — start lenient (recommended) and ratchet, or strict now?
3. **Marketplace registry** — is there a host/endpoint yet, or should Stage 4 publish be a local-validate stub?
4. **Code-signing cert** — have one, or stub the signing step?

## Suggested build order (smallest valuable increment first)

1. **Phase 1** (config: pytest/ruff/mypy + test markers) → get local `verify` green.
2. **Phase 2** (`ci.yml`) + **6.1** branch protection → regression safety net, zero secrets.
3. **Phase 3** (`integration.yml`) once key secrets are added.
4. **Phase 4** (`release-build.yml`) → real installers on tags.
5. **Phase 5–6** → publishing + hardening.
