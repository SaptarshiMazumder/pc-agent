# Tool Distribution & Tiers — Architecture Design

**Status:** design (no code yet — review before implementing).
**Goal:** separate tools into **distribution tiers** — **core** (shipped, mandatory),
**bundled** (included with a given install), **on-demand** (fetched later) — layered cleanly
on the [plugin catalog](plugin-catalog-architecture.md). This doc covers the **separation +
provisioning mechanism only**. Pricing/payment/billing are deliberately **out of scope**; the
one seam where a commercial product would attach licensing is named (§7) and left unbuilt.

---

## 1. The one idea

Three things people usually conflate, kept apart:

```
  what CODE is present   ≠   what THIS INSTALL provides   ≠   what the OPERATOR enabled
     (Installed)                  (Provisioned)                    (Enabled)
```

A tool only runs when **all** of those are true (plus per-agent scope). The plugin system
already gives us *Installed* and *Enabled*; tiers add one gate in the middle — **Provisioned**
— and **none of it is encoded in the folder tree**: a plugin is always just `plugins/<id>/`;
its *tier* is metadata, its *availability* is a provisioning set.

---

## 2. The four gates (a tool runs iff all pass)

| # | Gate | Question | Controlled by | Built? |
|---|---|---|---|---|
| 1 | **Installed** | is the code physically on disk? | distribution (installer / fetch) | ✅ discovery |
| 2 | **Provisioned** | is it part of THIS install's tier? | the install's **package profile** | ❌ **new (this doc)** |
| 3 | **Enabled** | did the operator turn it on? | `agentd.config.json` toggles | ✅ enablement |
| 4 | **Agent-scoped** | may *this agent* use it? | `agent.toml` allow/deny | ✅ select_tools |

Gate 2 slots **between** Installed and Enabled at the same catalog chokepoint — so it composes
with zero rework. *Provisioned* = "made available to this install"; *Enabled* = "the operator's
preference among what's provisioned." Keep them distinct (§7).

---

## 3. The tiers (distinguished by metadata, not folders)

| Tier | What | Installed how | Provisioned how |
|---|---|---|---|
| **core** (A, B) | the mandatory built-ins | shipped **in the app package** (`infrastructure/tools/`) | always — never gated |
| **bundled** (C, D) | extras included with this install | fetched **at setup** per the package profile | listed in the profile |
| **addon** (E, F) | optional, acquired later | **not shipped**; fetched on demand into `plugins/` | added to the profile when acquired |

`core` is internal (always installed, always provisioned). `bundled` and `addon` are ordinary
**plugins** — same `plugin.toml`, same loader — separated *only* by (a) **when** their code
arrives and (b) **whether** the profile lists them. A plugin never "knows" its tier matters;
the **profile** decides.

---

## 4. Manifest additions (`plugin.toml`)

Two optional, descriptive fields — pure metadata, no behavior:

```toml
id   = "videoedit"
name = "Video Editor"
kind = "native"            # native | mcp  (unchanged)
tier = "addon"             # "core" | "bundled" | "addon"   (default "bundled")
sku  = "videoedit"         # stable id used by the profile + registry (default = id)
# source = "registry"      # where to fetch an addon from (phase 3) — registry | url | git
```

`tier`/`sku` are *advisory* (for tooling/UX/registry); the **authoritative** gate is the
profile (§5), not the manifest — so a plugin can't self-promote its tier.

---

## 5. The package profile — the provisioning source of truth

A per-install descriptor listing the **plugin ids this install provides**. `core` is implicit
(always). Everything else must be listed to pass Gate 2.

```jsonc
// provisioned set — in agentd.config.json (dev) OR a separate, authoritative file (packaged)
{
  "provisioned": ["videoedit", "translate"]   // bundled + acquired addons, by id/sku
  // null / absent  => DEV MODE: every discovered plugin is provisioned (no gating)
}
```

- **Dev / self-hosted:** `provisioned` absent → no Gate-2 gating; whatever's in `plugins/`
  loads. (Frictionless for building/testing — same as today.)
- **Packaged install:** `provisioned` is the explicit, authoritative list the installer wrote.
  A plugin present on disk but **not** in the list does **not** load (installed ✓, provisioned ✗).

This is the clean separation knob: **what's on disk** vs **what this install is allowed to run**.

---

## 6. The registry / hub (where bundled + addon come from)

A vendor-hosted source of plugin packages, fetched by id/sku — the analogue of OpenClaw's
**ClawHub** (`publishToClawHub`, `install: { npmSpec }`). It serves:
- **bundled** plugins at **setup** (installer pulls the profile's list), and
- **addon** plugins **on demand** (fetched into `plugins/<id>/` later).

The registry is just a **delivery mechanism** — it puts code on disk (Gate 1). It does **not**
decide Gate 2; the profile does. (Keeping "delivery" and "authorization" separate is the point.)

---

## 7. The one seam left for a commercial product (out of scope here)

> **Provisioned ≠ a user-editable toggle.**

For a paid model, the `provisioned` set must be **authoritative and tamper-resistant** — a
**signed** descriptor verified locally (vendor public key) and/or a server check — so a user
can't self-extend it by editing a file. That enforcement attaches **exactly at Gate 2** and is
**deliberately not designed here** (no money/licensing in this doc). The architecture simply
leaves the seam: swap the plain `provisioned` list for a signed/served one, nothing else moves.

For dev/open/self-hosted use, the plain list (or "absent = all") is correct and sufficient.

---

## 8. Composition with the catalog (one added check)

Gate 2 is a single filter in plugin **discovery**, before load — so a non-provisioned plugin is
never imported (its deps never load), exactly like the existing per-plugin enable gate:

```text
# infrastructure/plugins/discovery.py  (extends today's _gate)
provisioned = resolve_provisioned(config)        # list[str] | None  (None => dev: all)
def _provisioned(pid):  return provisioned is None or pid in provisioned
# a plugin loads iff:  _provisioned(id)  AND  _gate(config, id, manifest.enabled)
```

Then the rest is unchanged: discovered → catalog → `apply_enablement` (Gate 3) → per-agent
`select_tools` (Gate 4). **core** tools skip Gates 1–2 entirely (they're in-package).

---

## 9. Layering (respects the import-linter contract)

| Layer | Piece | Why |
|---|---|---|
| **config.py** | `provisioned: list[str] | None`, (later) `registry_url` | the install's profile — JSON-configurable for free |
| **infrastructure/plugins/discovery.py** | provisioning gate (extend `_gate`) | one extra check, before import |
| **infrastructure/plugins/registry.py** *(phase 3)* | fetch a plugin by id/sku into `plugins/` | the delivery mechanism |
| **main / CLI** *(phase 3)* | `agentd plugin install <id>` — fetch → provision → enable | the acquisition flow |

No new domain/application concepts are needed — provisioning is a discovery-time gate over the
existing plugin ids. (A pure `is_provisioned` helper can live beside `apply_enablement` if we
want it IO-free and unit-tested in isolation.)

---

## 10. Walk-through (your scenario, money removed)

- **Install "Pro":** installer ships **core A, B** (in-package) + writes
  `provisioned = ["C", "D"]` + fetches **C, D** from the registry into `plugins/`. All gates
  pass → A, B, C, D run.
- **Add E later:** `agentd plugin install E` → fetch **E** into `plugins/` → append `"E"` to
  `provisioned`. Now E passes Gate 2 → enable → it runs. **F** stays absent (not installed, not
  provisioned).
- **Dev box:** no `provisioned` set → everything in `plugins/` just loads.

---

## 11. Phases

1. **Provisioning gate.** `provisioned` config field + the discovery check (`None` = dev/all).
   → *separate core/bundled/addon by an install-controlled list; nothing on disk runs unless
   provisioned.*
2. **Manifest `tier`/`sku`** + a package-profile shape (descriptive metadata + tooling/UX).
3. **Registry + `agentd plugin install`.** Fetch bundled at setup / addons on demand into
   `plugins/`, update the profile.
4. **(commercial, separate doc)** signed/served entitlement at Gate 2 — the only piece that
   needs the money/licensing model. The seam is already here.

> Phases 1–3 are the **distribution & separation** architecture — buildable and useful with no
> commerce at all (a self-hosted user just gets clean core/bundled/addon tiers + a plugin
> registry). Phase 4 is where a paid product would later attach, without moving anything else.
