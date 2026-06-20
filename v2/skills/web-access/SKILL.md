---
name: web-access
description: Use whenever a task involves the web — searching, looking something up, fetching a page, researching, or finding/accessing information on a website or online service. It picks the right tool (web_search vs web_fetch vs browser) and prevents wasted, failed, or fabricated results. ALWAYS applies when the request needs a signed-in account, private data, a social network, or any site that blocks anonymous access.
always: true
---

# Web Access

How to get information from the web reliably. Read this before any web search/fetch/lookup.

## Pick the right tool — by what the task needs, not by habit

| The task needs…                                                                                                              | Use            | Why                                                 |
| ---------------------------------------------------------------------------------------------------------------------------- | -------------- | --------------------------------------------------- |
| Current public info, "what/who/when" facts                                                                                   | **web_search** | finds public pages anonymously                      |
| The readable content of a specific **public** URL                                                                            | **web_fetch**  | one-shot anonymous GET                              |
| Anything behind a **login**, your **own accounts**, a **social network**, private dashboards, or a site that blocks crawlers | **browser**    | only a signed-in, real browser session can reach it |
| Clicking, typing, forms, multi-step flows, reading _your_ messages                                                           | **browser**    | interaction + your session                          |

If the goal is "find people / profiles / messages / listings on a specific site that requires login," that is a **browser** task on that site's own search page **while logged in** — it is NOT a web_search task. Public search engines cannot list content that lives behind a login, so web_search/web_fetch will return nothing usable.

**Do not use the browser as a Google/Bing proxy.** When the target is a specific platform, navigate directly to **that platform's own search** (e.g. its `/search` page) while signed in — do NOT open `google.com/search` or `bing.com/search`, and do NOT type `site:` / operator queries into any search box. Driving Google/Bing in the browser gets consent walls and CAPTCHAs and gives poor results. If you are not logged into the platform, you cannot search it — say so and ask the user to log in (or open the platform's login page for them); do not fall back to scraping search engines.

**In a site's own search box, use PLAIN KEYWORDS + the site's FILTERS — not a boolean string.** Most site search bars ignore or mangle `AND`/`OR`/`NOT`/parentheses/quotes, so a crammed-in boolean query silently does NOT filter — it just fuzzy-matches and looks broken. Enter the core terms plainly, then narrow with the site's structured filters or the corresponding URL parameters — iterate with filters, not operators.

## web_search queries: natural language only

Describe what you want in plain words (a short phrase stating what you're looking for). Do **not** use search operators (`site:`, quotes, `OR`, `intitle:`) — the backend is semantic and mishandles them.

## If a page needs login — pause and ask, then wait

When a page shows a sign-in wall for content the task needs (you'll see an `[auth]` note on the snapshot, or an obvious login/auth page):

1. **STOP.** Do not retry, reword the search, or open Google/Bing.
2. **Ask the user, then END YOUR TURN and wait.** Say plainly, e.g.: "<page> needs you to sign in. I've opened the window — please log in there and reply **done**, or say **skip** and I'll give you what I can from public sources." Do not take further tool actions this turn.
3. **On the user's next message, branch:**
   - **"done" / "logged in" / similar** → re-`snapshot` the SAME page (the browser stayed open between turns, now signed in) and continue the task.
   - **"skip" / "no" / declines** → fall back: give the best answer from public sources and state what you couldn't access. Never fabricate.

This is a normal turn boundary — ending your turn to ask IS the pause; the persistent browser keeps the session for when they return.

## When content is out of reach — degrade gracefully, don't stall or flail

If web_search or web_fetch comes back **empty, blocked, or login-walled**:

1. **Switch to the browser tool** (the right tool for gated content). Do not re-run the same search reworded more than once or twice.
2. **Never shell out to SCRAPE** (`exec` + curl/powershell/python against search engines or sites) — they block it, it wastes time, and it loops. The browser is the answer, not a scraping script. (Opening a URL for the user — see below — is fine; that's not scraping.)
3. If the browser can't reach it either (e.g. not logged into the required account), **do NOT just stop and demand a login.** Prefer, in this order:
   - **Give the best answer you can from what you DID gather** (public sources), and clearly state what you could not access and why. A useful partial answer beats a refusal.
   - **Hand the page to the user** when they need to act on it themselves (log in, 2FA, a purchase): open it in their own default browser via `exec` — `Start-Process "<url>"` (Windows), `open "<url>"` (macOS), `xdg-open "<url>"` (Linux). This pops a tab on their screen for them; you can't see it afterward, so tell them what to do there.
   - **Only block outright** when the task is impossible without the gated data (e.g. "read MY messages") — then say exactly what's needed (log in, or set `AGENTD_BROWSER_CDP_URL` to attach to your live Chrome).

## Never fabricate

If you cannot find and verify a real URL, name, or fact, **say so**. Do **not** construct or guess URLs from names (e.g. inventing a profile/page URL you never actually opened), and do not present unverified results as if confirmed. A short honest "I couldn't verify these" beats five plausible-looking fake links.

## Report with evidence

Give real, opened URLs and quote what you actually saw (title, snippet, a fact from the page). If a result came from a logged-in browser session, note that.
