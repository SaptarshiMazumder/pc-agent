# Browser Tool + Skill — OpenClaw Parity / Superiority Plan

**Status:** design (no code yet — review before implementing).
**Goal:** make agentd's `browser` tool **and** its `browser-automation` skill reach
**absolute capability parity with OpenClaw, then exceed it** — without importing OC's
distributed deployment infra we don't need. Hardening (SSRF / nav / act guards) is the
separate, layered concern in [`tool-hardening-policy.puml`](tool-hardening-policy.puml);
this doc is purely about **browser capability + the skill**.

> Sources compared: OC `extensions/browser/src/browser-tool.schema.ts` (the exact tool
> surface), OC `extensions/browser/skills/browser-automation/SKILL.md` (the only
> browser-specific skill OC ships), vs agentd
> `infrastructure/tools/browser/{tool.py,providers/playwright.py}` +
> `skills/browser-automation/SKILL.md`.

---

## 1. The one idea

OC's browser surface splits cleanly into **two tiers**, and conflating them is what
makes "parity" look impossibly large:

- **Tier A — browser CAPABILITY** (what the agent can do *to a page*): act kinds, snapshot
  richness, stable tab handles, screenshots, dialogs, uploads, console, pdf, profile
  attach. **This is real parity. We match all of it and add more.**
- **Tier B — distributed INFRA** (where the browser *runs*): a separate browser-control
  **server** process, `target = sandbox | host | node`, node-proxy routing, remote
  `start`/`stop` lifecycle. **This is OC's multi-host deployment model, not a browser
  capability.** agentd runs Playwright in-process; replicating Tier B is out of scope
  unless/until we want multi-host browser control. **Not a capability gap.**

So: **implement all of Tier A (parity + superiority); deliberately defer Tier B.**

---

## 2. Top-level actions

OC: `doctor status start stop profiles tabs open focus close snapshot screenshot navigate console pdf upload dialog act` (17)
agentd today: `navigate snapshot act screenshot tabs` (5; tab ops folded into `tabs.tab_action`)

| Action | OC | agentd today | Plan | Tier |
|---|:--:|:--:|---|:--:|
| `navigate` | ✅ | ✅ | keep | A |
| `snapshot` | ✅ | ✅ | enrich (see §4) | A |
| `act` | ✅ | ✅ | enrich (see §3) | A |
| `screenshot` | ✅ | ✅ (fixed png, viewport) | enrich (see §5) | A |
| `tabs` | ✅ | ✅ (positional) | stable handles (see §6) | A |
| `open` / `focus` / `close` | ✅ top-level | ✅ via `tab_action` | add stable-handle args; keep folded or promote | A |
| `console` | ✅ | ❌ | **add** — read page console logs | A |
| `pdf` | ✅ | ❌ | **add** — export page to PDF in `state_dir` | A |
| `dialog` | ✅ | ❌ | **add** — handle alert/confirm/prompt (`accept`, `promptText`) | A |
| `upload` | ✅ | ❌ | **add** — set files on a file input (`paths`, `inputRef`) | A |
| `status` | ✅ | ❌ (implicit `ensure()`) | **add** — lightweight: is the browser up, active tab/url | A |
| `doctor` | ✅ | ❌ | **add** — diagnostics: playwright present, chromium launchable, profile lock | A |
| `profiles` | ✅ | ❌ | **add** — list available profiles + login/attach state | A |
| `start` / `stop` | ✅ (remote server lifecycle) | ❌ | optional thin local version (launch/close in-process) | A- |
| `target` (sandbox/host/node) | ✅ | ❌ | **defer** — distributed infra | **B** |
| `node` proxy routing | ✅ | ❌ | **defer** — distributed infra | **B** |

---

## 3. `act` — kinds & params

**Kinds.** Near-identical; agentd already has `scrollIntoView` (good). Parity is complete
on kinds. (OC schema lists `close` as an act kind for closing via act; agentd has a
top-level/tab close — equivalent.)

**Params — agentd is missing these OC enrichments (all Tier A):**

| OC param | Meaning | agentd | Plan |
|---|---|:--:|---|
| `doubleClick` | double-click | ❌ | add to `click` |
| `button` | left/right/middle | ❌ | add to `click` (right-click menus) |
| `modifiers` | Ctrl/Shift/Alt/Meta + click | ❌ | add to `click` |
| `slowly` | per-char typing (React/contenteditable) | partial (`type` presses sequentially) | expose flag |
| `delayMs` | inter-key delay for `press`/`type` | ❌ | add |
| `fields` | **multi-field form fill in ONE act** | ❌ | **add** — big UX win for forms |
| `frame` | target an iframe | ❌ | **add** — iframes currently unreachable |
| `targetId` (per-act) | act on a specific tab | ❌ (uses active tab) | add once stable handles land (§6) |
| `element` | human-readable element hint | ❌ | optional (aids logging/labels) |

agentd already has: `ref, text, key, value(s), expression(fn), x/y, start/end_ref,
width/height, submit, time_ms, load_state, text_gone, selector, url, timeout_ms`.

---

## 4. `snapshot` — params

| OC param | Meaning | agentd | Plan | Tier |
|---|---|:--:|---|:--:|
| `mode=efficient` | compact interactive-only | ✅ | keep | A |
| `interactive` / `compact` / `depth` | tree filters | ✅ | keep | A |
| `maxChars` | char cap | ✅ (`max_chars`) | keep | A |
| `refs=aria` | **durable Playwright aria-ref ids** (self-resolving across calls) | ❌ (role+name+nth, fragile) | **add** — biggest robustness win | A |
| `urls=true` | include link hrefs | ❌ | **add** — enables direct-nav over brittle clicks | A |
| `labels=true` | overlay visual position labels | ❌ | **add** — pairs with `clickCoords` | A |
| `selector` | snapshot a sub-tree only | ❌ | **add** — big pages, scoped reads | A |
| `frame` | snapshot inside an iframe | ❌ | **add** | A |
| `snapshotFormat=aria\|ai` | raw aria vs AI-formatted | ✅ (ai-style only) | add raw `aria` option | A |
| `targetId` | snapshot a specific tab | ❌ | add with §6 | A |

> **Superiority note:** agentd's current snapshot already does AI-formatting + ref
> numbering well, and the skill's **lazy-list scroll loop** (scrollIntoView → wait
> networkidle → re-snapshot) is *better-documented than OC's*. Keep that; add aria refs
> for durability.

---

## 5. `screenshot` — params

agentd today: hardcoded `full_page=False`, PNG only, saved to `state_dir/screenshots`.

| OC param | agentd | Plan |
|---|:--:|---|
| `fullPage` | ❌ (fixed false) | add toggle |
| `type=png\|jpeg` | ❌ (png only) | add jpeg (smaller for vision) |
| `element` / `ref` | ❌ | add — screenshot one element |
| `labels` | ❌ | add — numbered overlay for coordinate clicks |
| `targetId` | ❌ | add with §6 |

---

## 6. Tabs — stable handles (a correctness gap, not just a feature)

OC uses **stable handles**: open tabs with a `label`; responses return
`suggestedTargetId` (= label, else `tabId` like `t1`); later calls pass `targetId`. Refs
stay bound to the right tab even under Chromium target replacement.

agentd uses **positional `tab_index`** — brittle: indices shift when tabs open/close, and
a snapshot ref taken on tab 2 silently applies to whatever is now at index 2.

**Plan (Tier A, recommended early):**
- Assign each page a stable `tabId` (`t1`, `t2`, …) + optional `label` on open.
- `tabs.list` returns `tabId` + `label` + `suggestedTargetId`.
- Accept `targetId` (label | tabId) on `snapshot`/`act`/`screenshot`/tab ops; keep
  `tab_index` as a compat fallback.
- Bind `ref_map` per-tab so a stale-tab ref fails fast instead of acting on the wrong page.

---

## 7. Profiles & live-Chrome attach

| | OC | agentd today | Plan |
|---|---|---|---|
| Default | isolated managed profile (`openclaw`) | own persistent profile dir | keep |
| Attach to user's live Chrome | `profile="user"` (CDP) | ❌ | **add CDP-attach `BrowserProvider`** behind the existing port (`connect_over_cdp`) |
| List profiles / state | `action="profiles"` | ❌ | add `profiles` action |

This is the **one real "live browser" gap** flagged in earlier analysis. It drops in as a
second adapter behind `BrowserProvider` (the interface already anticipates it) selected by
a new browser factory — no change to the tool.

---

## 8. Skill parity (`browser-automation/SKILL.md`)

OC ships exactly **one** browser skill. agentd's port is already strong and is **ahead** on
the lazy-list loop. Update it to cover the new capabilities so tool and skill stay in sync:

| Guidance | OC skill | agentd skill | Plan |
|---|:--:|:--:|---|
| snapshot→act→re-snapshot loop | ✅ | ✅ | keep |
| Lazy/long-list scroll loop | basic | ✅ **(better)** | keep — superiority |
| Stale-ref recovery | ✅ | ✅ | keep |
| Real-blocker reporting (login/captcha/2FA) | ✅ | ✅ | keep |
| **Stable tab handles** (label/targetId) | ✅ | ❌ | **add** (after §6) |
| **`refs="aria"` durable refs** | ✅ | ❌ | **add** (after §4) |
| **`urls=true` / `labels=true`** usage | ✅ | ❌ | **add** |
| **`profile="user"` attach** + timeout caveat | ✅ | ❌ | **add** (after §7) |
| Preflight `status`/`doctor`/`profiles` | ✅ | ❌ | **add** |
| `dialog` / `upload` / `pdf` / `console` usage | ✅ | ❌ | **add** |
| Domain note (e.g. Google Meet cam/mic) | ✅ | ❌ | optional — product-specific |

**Superiority option:** split focused sub-skills later (e.g. `web-forms`,
`scrape-pagination`) — agentd's skill loader picks up any folder, so this scales for free.
Not required for parity.

---

## 9. Build order (after the hardening skeleton, or in parallel)

1. **Stable tab handles** (§6) — correctness fix; unblocks per-tab `targetId` everywhere.
2. **Snapshot enrich** (§4): `refs=aria`, `urls`, `labels`, `selector`, `frame`.
3. **act enrich** (§3): `doubleClick`/`button`/`modifiers`/`slowly`/`delayMs`/`fields`/`frame`.
4. **screenshot enrich** (§5) + **new actions**: `console`, `pdf`, `dialog`, `upload`.
5. **`status`/`doctor`/`profiles`** (lightweight, in-process).
6. **CDP-attach provider + browser factory** (§7) → `profile="user"`.
7. **Update `browser-automation/SKILL.md`** (§8) to teach all of the above.
8. **(Deferred, Tier B)** browser-control server, `target=sandbox/host/node`, node proxy —
   only if multi-host browser control becomes a product requirement.

Each step is independent and behind the existing `BrowserProvider` port / tool schema —
nothing else in the system changes.
