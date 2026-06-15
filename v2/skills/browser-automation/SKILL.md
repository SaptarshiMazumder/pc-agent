---
name: browser-automation
description: Use when controlling web pages with the browser tool, especially multi-step flows, login checks, tab management, long or lazy-loaded lists, or recovery from stale refs/timeouts.
---

# Browser Automation

Use this skill whenever you need the `browser` tool for anything beyond a single
page check. It is the operating loop that keeps multi-step browsing reliable.

## Operating loop

1. **Read before you click.** Take `action="snapshot"` first; only use a `ref`
   from the latest snapshot.
2. **Act narrowly.** Prefer `action="act"` with a `ref`. After navigation, a
   modal change, or a form submission, snapshot again before the next action —
   refs from an old snapshot are stale.
3. **Reveal more on long / lazy-loaded lists** (job boards, feeds, search
   results): the first snapshot only shows what is currently rendered. To gather
   more, `act` `kind="scrollIntoView"` on the last visible item (or
   `kind="evaluate"` `fn="window.scrollBy(0, 2000)"`), then `act` `kind="wait"`
   `load_state="networkidle"`, then snapshot again. Repeat until you have enough
   items — do not stop at the first screenful.
4. **Avoid blind waits.** Wait for a visible UI state, not a fixed sleep:
   `load_state="networkidle"`, `text=...`, or `selector=...`.
5. **Cut noise on large pages** with `mode="efficient"` snapshots; raise
   `limit`/`depth` when you need more of the tree.

## Recovery

- **Stale / unknown ref:** run a fresh `snapshot` and use a ref from that
  snapshot. Never reuse a ref across navigations.
- **Real blockers:** if the page needs login, a captcha, 2FA, or a permission
  dialog, stop and tell the user exactly what is needed. Do not claim you are
  not logged in just because a permission or onboarding dialog is showing —
  inspect the visible UI first.

## Reporting

Report results with evidence from the page (titles, URLs, a final snapshot or
screenshot), and keep going until the task is actually done rather than
finishing on a plan.
