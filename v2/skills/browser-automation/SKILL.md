---
name: browser-automation
description: Use when a task needs a signed-in/authenticated web session (your own accounts, private dashboards, social networks, messages) or interactive browsing, or when web_search/web_fetch come back blocked/empty/login-walled. Covers multi-step flows, login checks, tab management, forms, file uploads, long or lazy-loaded lists, iframes, dialogs, and stale-ref/timeout recovery.
---

# Browser Automation

Use this skill whenever you need the `browser` tool for anything beyond a single
page check. It is the operating loop that keeps multi-step browsing reliable.

## Operating loop

1. **Read before you click.** Take `action="snapshot"` first; only use a `ref`
   from the latest snapshot of the **same tab**. Refs are durable aria refs
   (e.g. `e7`, or `f1e2` inside an iframe) and resolve until the page changes.
2. **Act narrowly.** Prefer `action="act"` with a `ref`. After navigation, a
   modal change, or a form submission, snapshot again before the next action —
   refs from an old snapshot of a changed page are stale.
3. **Reveal more on long / lazy-loaded lists** (job boards, feeds, search
   results): the first snapshot only shows what is rendered. To gather more,
   `act` `kind="scrollIntoView"` on the last visible item (or `kind="evaluate"`
   `expression="window.scrollBy(0, 2000)"`), then `act` `kind="wait"`
   `load_state="networkidle"`, then snapshot again. Repeat until you have enough
   — do not stop at the first screenful.
4. **Avoid blind waits.** Wait for a visible UI state, not a fixed sleep:
   `load_state="networkidle"`, `text=...`, or `selector=...`.
5. **Cut noise on large pages** with `mode="efficient"`; raise `depth`/`max_chars`
   or scope with `selector=` when you need more. Add `urls=true` when link text is
   ambiguous (snapshot then includes each link's target).

## Tabs — use stable handles, never indices

- Open important tabs with a `label`: `{ "action": "open", "url": "...", "label": "meet" }`.
- Snapshots/responses report a `tabId` (e.g. `t2`) and `suggestedTargetId`.
- Pass `target_id` (the label or tabId) on later `snapshot`/`act`/`screenshot`/
  `focus`/`close` so actions stay on the intended tab.
- Before creating a tab for a named task, `action="tabs"` and reuse a matching
  label/URL if still usable. Close duplicates by `target_id`.

## Forms, uploads, dialogs, iframes

- **Whole forms in one act:** `act` `kind="fill"` with
  `fields=[{ "ref": "e5", "text": "Alice" }, { "selector": "#email", "text": "a@b.com" }]`.
- **File upload:** `action="upload"`, `input_ref` (or `selector`) of the file
  `<input>`, and `paths=["/abs/file"]`.
- **Dialogs** (alert/confirm/prompt) are auto-accepted so nothing hangs. To
  change behaviour, call `action="dialog"` with `accept=false` (dismiss) or
  `prompt_text="..."` BEFORE the action that triggers it; the last dialog's text
  is reported back.
- **iframes** appear in the normal full snapshot with frame-encoded refs
  (`f1e2`); just use that ref — no special frame handling needed. For raw CSS
  inside a frame, pass `frame="<iframe css>"`.

## Diagnostics & login state

- `action="status"` — is the browser up, which tabs/urls, captured downloads.
- `action="doctor"` — when the browser setup itself may be broken.
- `action="profiles"` — current profile/attach mode and how to change it.
- `action="console"` — recent page console logs (debugging a broken page).
- `action="screenshot"` — `full_page=true`, `element="<css>"`, or `labels=true`
  (adds `[box]` coordinates you can feed to `act` `kind="clickCoords"`).
- `action="pdf"` — save the page to PDF (headless only).

## Recovery

- **Stale / unknown ref:** run a fresh `snapshot` of that `target_id` and use a
  ref from it. Never reuse a ref across navigations or across tabs.
- **Real blockers:** if the page needs login, a captcha, 2FA, or a permission
  dialog, stop and tell the user exactly what is needed. Do not claim you are not
  logged in just because a permission/onboarding dialog is showing — inspect the
  visible UI first.

## Existing user browser

By default the tool uses its own persistent profile (logged in once via
`python -m agentd.main.browser_login`). To drive the user's **already-running
Chrome** with their live cookies, start Chrome with `--remote-debugging-port=9222`
and set `AGENTD_BROWSER_CDP_URL=http://localhost:9222` (chosen at startup;
`action="profiles"` reports the active mode).

## Reporting

Report results with evidence from the page (titles, URLs, a final snapshot or
screenshot), and keep going until the task is actually done rather than finishing
on a plan.
