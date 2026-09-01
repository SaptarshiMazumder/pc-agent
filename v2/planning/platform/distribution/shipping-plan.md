# Shipping plan — one signed engine, per-agent installers, self-service publish

**Goal (user's words, 2026-08-08):**

> 1. bob installed agentd.exe from our website
> 2. he created an agent amazon-sales agent using agent-builder
> 3. he wants to publish the exe installer of this to marketplace
> 4. sally sees an ad about this agent and goes to our web marketplace and downloads the
>    amazon-sales.exe agent and starts using it right away after logging in
> 5. sally knows not a thing about agentd.exe (she doesn't have to, it came packaged with the
>    installer she downloaded)
> 6. on sally's pc it creates an app called Amazon Sales, and double clicking it opens the custom
>    ui of the desktop app created by bob. sally knows nothing about our agentd, she never sees
>    the UI of it or has any knowledge of the runtime

**Status (2026-08-09):** P0 (payload selection at runtime) shipped in `ad15d5d`. **P1–P5 are now
BUILT** and in the working tree — see the per-phase notes below. What remains is the publish
SERVICE itself (Terraform + Lambda, P4's server half), creator identity, and the certificate.

**Where the engine contract lives.** In the three files that implement it, each fully commented,
and deliberately not in a fourth markdown copy:

| | file |
|---|---|
| writer (engine records itself) | `clients/desktop/build/engine-register.nsh` |
| reader (stub finds the engine) | `agent_runtime/infrastructure/products/templates/stub.nsi` |
| reader (diagnostics) | `agent_runtime/infrastructure/products/engine_registry.py` |

Related, do not duplicate: `marketplace-publish-plan.md` (the publish service and creator
identity, still accurate), `product-distribution-plan.md` (the local-first product story that
got us here).

---

## The shape: engine + stub

A per-agent installer today is a **full 250 MB copy** of the client and the embedded Python
runtime. Ten agents on one machine is ten runtimes, ten SmartScreen reputations to earn, and a
250 MB upload per publish. It also means an installer can only be produced by someone with
node + electron-builder + a built CPython tree — which Bob, on the web, is not.

So a product splits in two:

| | what it is | size | who signs it | who builds it |
|---|---|---|---|---|
| **engine** | the shared client + embedded daemon | ~250 MB | ONE certificate, once | us, on release |
| **payload** | `distribution.toml` + `bundles/<id>.agentpkg` + `icon.ico` | ~50 KB | the registry's ed25519 key | the publisher |
| **stub** | tiny installer: ensure engine → write payload → shortcut | ~200 KB | the same ONE certificate | the publish service |

`--app-dir <payload>` already makes the engine *be* that agent (`main/flavor.ts`, shipped). The
missing half is everything that **produces** payloads and stubs — and it must run without a
build toolchain, because eventually it runs in a Lambda.

This fixes four things at once: disk duplication, reputation dilution across N binaries, the
250 MB publish, and the fact that a creator needs no toolchain.

---

## Three decisions that drive the code

### 1. Product derivation moves to Python. One source of truth.

The rules "agent.toml → a product" live today in `clients/desktop/scripts/gen-app-flavor.mjs`:
display name precedence, version precedence, icon precedence, `appId`, whether `[platform]` is
inherited. That file is **Node, inside the desktop client, and needs electron-vite +
electron-builder + `runtime/cpython`**. A publish service has none of it.

Writing a second copy in Python is the wrong answer — the precedence chains are exactly the
thing that drifted before (three different versions for one product: agent.toml 1.1.0, registry
1.0.0, exe 0.1.5). So the derivation becomes ONE domain service in `agent_runtime`, and
`gen-app-flavor.mjs` becomes a thin caller of it.

That single move is what lets the same code serve `agentd product build`, `publish_agent`, and
the Lambda.

### 2. Engine discovery is a published contract, not a guessed path.

The engine installer registers itself:

```
HKCU\Software\agentd\Engine
    Path     = <install dir>
    Exe      = <install dir>\<Engine>.exe
    Version  = 0.2.0
```

Stubs read that key. No stub ever hardcodes `%LOCALAPPDATA%\Programs\...`. `agentd doctor`
reports it. One key, documented in `PROTOCOL.md`, additive-only.

### 3. The stub pins the engine by digest, not by signature.

NSIS cannot verify ed25519, and pretending otherwise would be theatre. The engine's URL +
sha256 are baked into the stub **at build time, read from the signed index**. Trust anchors:
the index signature at build time, and Authenticode on the engine when the certificate arrives.
The stub refuses to install an engine whose digest does not match, and says the URL it tried.

### Non-negotiable: ONE engine, upgrade-forward.

Every agent shares one daemon and one single-instance guard; two engine versions on a machine
would fight over the port and the rendezvous file. So the engine is never side-by-side
versioned. The consequence, which must be enforced in review: **the engine↔payload contract is
additive-only.** A payload written by an older publisher must keep working on a newer engine
forever. A stub declares `engine_min_version`; the engine never declares a maximum.

---

## P1 — the product core (Python, local, no AWS)

Pure-to-impure, ports at every boundary. Nothing here knows about NSIS except one adapter.

```
domain/product.py
    ProductSpec            frozen: id, agent_id, name, version, app_id, icon_rel,
                           default_agent, preinstalled, platform (accounts/proxy urls),
                           engine_min_version
    ProductRules.derive(agent_toml, defaults) -> ProductSpec
        the precedence chains, pure, no I/O — the JS logic, moved and tested

application/interfaces/product_builder.py
    PayloadWriter.write(spec, source, out_dir) -> PayloadManifest
    StubBuilder.build(spec, payload_dir, engine_ref, out_path) -> Path
    EngineCatalog.resolve(platform) -> EngineRef(version, url, sha256)

application/services/build_product_service.py
    derive -> pack-or-copy the .agentpkg -> write payload -> resolve engine -> build stub
    the ONLY place that order lives

infrastructure/products/payload_writer_fs.py     distribution.toml + bundles/ + icon.ico
infrastructure/products/nsis_stub_builder.py     + templates/stub.nsi
infrastructure/products/engine_catalog_index.py  reads the signed index's [engine] block
cli/commands/product.py                          agentd product payload|stub|build <id>
```

- `source` is an agent dir **or** a prebuilt `.agentpkg` — third-party intake is not a special
  case, it is the same port with a different argument (today's `gen:app --pkg`).
- Ports, not concrete classes, because the Lambda will swap `nsis_stub_builder` for a
  cross-compiled builder and the tests use fakes. `BuildProductService` never learns.
- `agentd product build <id>` is the whole Bob path, offline: payload + stub, no electron.
- Tests: `ProductRules` and `BuildProductService` fully unit-tested with fakes; the NSIS adapter
  gets an integration test that skips when `makensis` is absent.

**No hardcoding, specifically:** engine URL, digest and minimum version come from the catalog
port; the platform block comes from config with the core flavor as *a* source, not *the* source;
paths derive from `runtime_paths`, never a literal.

## P2 — the engine

- `flavors/core` gains an NSIS `customInstall` / `customUnInstall` that writes and removes the
  registry contract from decision 2. Nothing else about the core installer changes.
- `index.json` gains a signed `engine` block (platform → version, url, sha256), reusing
  `InstallerAsset`. The engine becomes a registry asset like any other, so `EngineCatalog` has
  something real to read and updating the engine never means rebuilding stubs.
- `agentd doctor` reports engine presence + version — the first question every support thread
  will ask.
- `PROTOCOL.md`: the registry key, the payload layout, and the additive-only rule.

## P3 — what the stub does when Sally double-clicks it

All of it in `stub.nsi`. No part of this logic gets a second home in TypeScript.

1. Engine installed and `Version >= engine_min_version`? → step 3.
2. Otherwise download the engine, **verify sha256**, run it silently (`/S`). On any failure:
   stop with a message naming the URL. Never leave a shortcut pointing at nothing.
3. Write the payload to `%LOCALAPPDATA%\agentd\apps\<id>\`.
4. Start-menu + desktop shortcut → `<Engine>.exe --app-dir <payload>`, AUMID `spec.app_id` (so
   each product is its own taskbar button), plus its own Add/Remove Programs entry.
5. Uninstall removes the payload, the shortcut and the ARP entry — **never the engine**, and
   never another product's payload.

Sally sees "Amazon Sales" in her Start menu and Bob's UI when she clicks it. Nothing in that
window says agentd.

## P4 — publish, HTTP-first (this is also a retro-fix)

`publish_agent` currently needs `publisher_keyfile` **and** S3 write credentials on the
creator's machine. Per the standing decision ("i dont need direct upload to s3 directly") a
creator must need neither.

```
application/interfaces/bundle_publisher.py
    BundlePublisher.publish(agent_dir, spec, dry_run) -> PublishReport
infrastructure/marketplace/s3_registry_publisher.py     wraps today's run_publish (operator CLI)
infrastructure/marketplace/http_registry_publisher.py   POST to the publish service
```

`publish_agent` depends on the **port** and picks the adapter from the scheme of
`publish_target` (`s3://` → S3, `https://` → HTTP). The tool loses its key requirement without
losing any of the guards, because the S3 adapter is still literally `run_publish`.

The service itself is `marketplace-publish-plan.md` P0–P1 unchanged — a Lambda behind the
existing ALB, creator identity in P0 — with one addition: **the service calls the same
`BuildProductService`**, so one upload produces the `.agentpkg` *and* the per-agent stub, and
the marketplace Download button has something to serve. That is only possible because of
decision 1.

## P5 — a UI COMPONENT tier, of which sign-in is the first entry

**Not seven hand-edits.** The requirement (user's words, 2026-08-09):

> i dont want u to hardcode login to all agents, rather, any agent created using agent builder
> should be ABLE to create login feature, simply by reusing the login component, just like agent
> builder allows reusing other templates or components

### The actual gap

Agent Builder has exactly ONE tier of reuse: whole-app templates. `UiTemplates` (one entry,
`chat-app`) copied by `ScaffoldUiService`, which **refuses over an existing `ui/`** — correctly,
because an existing app is somebody's work.

So there is no unit smaller than an entire app, and "add sign-in to an agent that already has a
UI" has no route at all: re-scaffold and destroy the author's work, or hand-edit. Sign-in is not
special. It is simply the first thing that wants the missing tier.

### The component tier

Same registry shape as `UiTemplates` — a frozen descriptor plus a catalogue — so the tool's
description is generated from `describe()` and the SECOND component is offered automatically
with no code change.

```
domain/ui_component.py
    Insertion       anchor, snippet, detect      # where it goes, the canonical code, is-it-there
    UiComponent     id, title, summary,
                    files      own files from templates/components/<id>/
                    borrowed   from the live ui/ (the SDK — one copy, cannot drift)
                    scripts    <script src> tags to ensure in index.html
                    styles     a token block to append to style.css
                    insert     tuple[Insertion, ...] woven into app.js
                    requires   SDK symbols that must exist in the vendored copy
    SIGN_IN = UiComponent(id="sign-in", ...)
    class UiComponents:  ids() / get() / describe() / default_id
```

```
application/services/add_component_service.py
    plan(agent_id, component_id) -> ComponentPlan     pure decision, writes nothing
    apply(plan, confirm_overwrite) -> ComponentResult
presentation/add_ui_component_tool.py                 add_ui_component(agent_id, component)
```

Three properties, each of them load-bearing:

- **Idempotent.** A step whose `detect` already matches reports `already-present` instead of
  inserting a second copy. Running it on `game-master` (which has sign-in) does nothing. This is
  what makes it safe for the model to call whenever it is unsure.
- **Never guesses at unknown code.** If an app.js has no anchor — hand-written, or older than
  the anchor — the tool still does every deterministic step (files, script tags, style tokens,
  SDK refresh) and then returns the exact snippet and where it belongs, as an instruction. It
  does not regex its way into code it does not recognise. Mechanism ranking applied honestly: a
  deterministic patch where the shape is known, an instruction where it is not, never a fragile
  edit that half-works.
- **Never clobbers.** Same rule as `scaffold_ui`: a component file that exists and differs is
  named, and replacing it needs `confirm_overwrite`.

`scaffold_ui` gains `components: string[]`, so a NEW agent composes template + components in one
call — through the same service, not a second code path.

### Sign-in as a descriptor

| field | value | why |
|---|---|---|
| `files` | *(none)* | the mechanism ships in the SDK (`gate.ts`); a copy under `templates/` would be a second version of the gate in one product |
| `borrowed` | `vendor/agentd-client.js` | one source, refreshed at add time |
| `scripts` | that vendor path, before `app.js` | the gate is undefined otherwise |
| `styles` | the `--gate-*` token block | so the modal matches the agent's theme instead of looking bolted on |
| `insert` | `await agentd.mountSignInGate()` after the components anchor | renders nothing on a BYOK build, when the device is already connected, or when a stored session works — so it is safe unconditionally and has nothing to configure |
| `requires` | `mountSignInGate` | drives the SDK-freshness check below |

### The duplication this exposes (retro-fix, do it here)

The canonical sign-in snippet lives in **three** places today and is shared by none of them: the
literal in `templates/chat-app/app.js`, the `_GATE_CALL` / `_GATE_MECHANISM` regexes in
`ui_rules.py`, and — about to be — whatever the component inserts. Three copies of one fact is
the exact class of drift the validator exists to catch.

So: **the descriptor owns** the mechanism name, the detect pattern and the snippet. `UiRules` is
*given* the catalogue by the composition root — precisely as its own docstring already argues for
`events` / `kinds` / `methods` ("told the real vocabulary rather than keeping their own copy") —
and a test asserts the template contains the descriptor's snippet.

Two rules then generalise for free:

- `UI_NO_SIGN_IN`'s `fix` can name the tool: `add_ui_component(<agent>, "sign-in")`.
- `UI_SDK_PREDATES_SIGN_IN` becomes **one** rule for N components: every installed component's
  `requires` symbols must be present in the vendored SDK. Adding component #2 needs no new rule.

### Then the seven agents

`agent-builder`, `comfyui-workflow-architect`, `expense-summarizer`, `figure-creator`,
`inbox-triage`, `weather`, `linkedin-job-finder` get sign-in **by running the tool** — which is
also how the mechanism gets proved on seven real, differently-shaped apps before any outside
author touches it. `game-master` re-runs as a no-op, which is the idempotency test.

## P6 — deploy (your commands)

Push the branch; deploy `web` + `daemon`; `terraform apply` for the Lambda, its role, the
DynamoDB lock table and the ALB rule. Authored here, handed over as a command card — as always.

---

## Retro-fixes, done inside the phases

1. **Tracked build junk carrying one machine's absolute path.**
   `clients/desktop/electron.vite.config.1785921037643.mjs` is committed and contains
   `file:///C:/Users/<user>/...`. `git rm` it; gitignore
   `electron.vite.config.*.timestamp*.mjs` and `electron.vite.config.[0-9]*.mjs`. (P1)
2. **Product derivation duplicated in JS** — the whole of decision 1. (P1)
3. **`gen-app-flavor.mjs` reads `flavors/core/distribution.toml`** to inherit `[platform]`. A
   repo-relative path that does not exist server-side. Config wins; the core flavor becomes one
   source among several. (P1)
4. **`publish_agent` demands a private key and S3 write.** → the `BundlePublisher` port. (P4)
5. **Uninstall can delete repo source.** A dev-mode payload install wrote into `agents_dir`,
   which in a checkout IS the repo; a later Uninstall click deleted 12 tracked files. Uninstall
   must refuse to remove a directory that lives inside a git work tree. (P3)
6. **`build-runtime.ps1` does not clean its target** — two stale `.pyc` survived a rebuild.
   Given the `ModuleNotFoundError` this class of staleness already caused, clean the target. (P2)
7. **Verify a product can never nest inside a payload.** `dist-app.mjs` delivers into
   `agents/<id>/clients/desktop/`; `bundle_io.EXCLUDED_DIRS` excludes `clients/`. Assert it in a
   test rather than trusting it. (P1)
8. **The sign-in snippet exists in three unshared places** — template literal, validator regex,
   and (pending) the component. One descriptor owns it; `UiRules` is given the catalogue. (P5)
9. **`templates/chat-app/app.js` needs a components anchor**, so the common path is a
   deterministic insert rather than an instruction to the model. Adding it is what makes every
   *future* component automatic instead of hand-placed. (P5)

---

## Order, and what it costs you

**P1 → P2 → P3** are entirely local and end-to-end testable on one machine: build a stub for
`game-master`, wipe `~/.agentd`, run the stub, and that is Sally's flow minus the download.
Nothing to deploy, nothing to sign, no AWS.

Then **P5** (cheap, and it unblocks testing cloud mode on anything but game-master), then
**P4**, then **P6**.

**The certificate is orthogonal.** Every phase above works unsigned — Sally sees a SmartScreen
warning and clicks through. Buying the certificate later changes one line in the engine's build
config, and because of the engine+stub split there is exactly one binary to sign instead of N.
