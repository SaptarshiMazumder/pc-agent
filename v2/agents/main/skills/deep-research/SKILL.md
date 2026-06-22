---
name: deep-research
description: Use for any research task that needs depth and breadth across many sources — "research X", "find out everything about", "compare A vs B", "give me a thorough/comprehensive report on", market/competitor/background research, or any question where one page isn't enough. Produces a well-organized, source-cited answer.
---

# Deep Research

Turn a broad question into a thorough, well-organized, **cited** report by searching widely, reading many sources **in parallel**, cross-checking, and iterating until coverage is solid.

## The loop (plan → fan-out → read → iterate → synthesize → verify)

1. **Plan.** Call `update_plan` first. Decompose the question into 3–8 concrete **sub-questions** / angles (e.g. for "compare A vs B": features, pricing, reliability, reviews, ecosystem). Name the tool each step uses.

2. **Fan out searches IN PARALLEL.** In a SINGLE turn, emit **multiple `web_search` calls at once** — one per sub-question — using **natural-language** queries (no `site:`/operators). The runtime runs them concurrently, so this is fast. Aim for breadth: several distinct queries, not one.

3. **Read the best sources IN PARALLEL.** From the results, pick the most relevant/authoritative URLs (prefer primary sources, docs, reputable outlets; dedupe domains). In a single turn, emit **multiple `web_fetch` calls at once** to read them. Use `browser` instead for a page that's login-walled/JS-only/blocked (see the web-access rules).

4. **Extract + track citations.** For each source, pull the specific facts/figures/quotes that answer a sub-question, and **record the exact URL** you actually opened. Never carry a claim without the source it came from.

5. **Iterate (multiple rounds).** After the first pass, identify **gaps, contradictions, and weak spots** — then run another parallel batch of `web_search`/`web_fetch` to fill them. Repeat for **2–4 rounds** (or until new searches stop adding anything). Do not stop after one round; depth comes from iteration.

6. **Cross-check.** A claim that matters (numbers, dates, "best/worst", safety) should be supported by **≥2 independent sources**. Flag disagreements explicitly rather than picking one silently.

7. **Synthesize a well-organized report.** Structure it: a short **summary/answer up front**, then sections per theme/sub-question, then (if relevant) a comparison table, caveats, and a **Sources** list. Put **inline citations** (the real URL) next to each non-obvious claim.

8. **Verify before finalizing.** Call `verify_answer` (or self-check): every sub-question addressed? every key claim backed by a real, opened source? no gaps silently dropped?

## Hard rules

- **Breadth via parallelism:** batch multiple `web_search`/`web_fetch` calls per turn — don't do them one at a time. That's what makes it fast and wide.
- **Never fabricate.** Only cite URLs you actually opened and read. If you couldn't verify something, say "unverified" — do not invent sources, figures, or links.
- **Prefer primary/authoritative sources** over SEO blogspam; note when a source is marketing/opinion.
- **Report what you couldn't find**, don't paper over gaps.
- **Scale to the ask:** a quick "look into X" = 1–2 rounds; "comprehensive report" = 3–4 rounds and more sources.

## Output shape (default)

```
## <answer in 2–4 sentences>

### <Theme / sub-question 1>
- finding … (https://source-url)
- finding … (https://other-source)

### <Theme 2>
…

### Caveats / open questions
- …

### Sources
1. https://…  — what it covered
2. https://…
```
