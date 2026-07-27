---
name: monthly-report
description: Produce the standard monthly spending report — inventory the expense files (bank/, cards/, loose files), parse and dedupe transactions, categorize, total the month, render a matplotlib chart, and present the report in the house format. Use whenever the user asks to summarize/report spending for a month or period.
---

# Monthly spending report

The repeatable procedure. Do the steps in order; don't skip the inventory.

## 1. Establish the period

Default: the current calendar month. An explicit month/range in the request wins.
Derive `YYYY-MM` — you'll need it for file matching and the chart filename.

## 2. Inventory the files

- List `bank/` and `cards/` (and the workspace root for loose expense-looking files).
- Candidates for the period: filename contains the `YYYY-MM`, OR file rows contain dates
  in the period (filenames lie — verify by reading headers + a few rows).
- Tell the user which files you are using. If NONE match the period, STOP and report that
  (offer the nearest month you did find). Do not proceed on unrelated data.

## 3. Parse

Read each candidate with `read`, then extract rows with `exec` (python + csv/pandas).
Known column layouts:

| layout | date | merchant | amount |
|---|---|---|---|
| English export | `date` | `merchant` | `amount_jpy` |
| Yucho debit | `利用日時` | `利用店舗` | `利用金額` |
| Rakuten card | `利用日` | `利用店名` | `利用金額` |

Anything else: inspect the header, map columns by meaning, and note the mapping in your
report. Amounts: strip `¥`, commas, and whitespace; treat negative/refund rows as negative.

## 4. Dedupe

Same (date, amount, merchant-normalized) appearing in more than one source = one
transaction. Keep the card-file copy, drop the bank copy. Report the count dropped.

## 5. Categorize + aggregate

Apply the category rules from AGENTS.md (exact category list lives there).
Compute: total; per-category totals + % + counts; top 5 merchants; biggest transaction;
prior-month comparison when prior files exist.

## 6. Chart

Via `exec`, python + matplotlib (headless — use the Agg backend):

- default: horizontal bar, categories sorted by total, values labeled `¥N,NNN`
- save to `reports/<YYYY-MM>-spending.png` (create `reports/` if needed)
- honor a requested chart type (pie, month-over-month trend) instead of the default

## 7. Present

Use the report format from AGENTS.md (headline, category table, top merchants, notables,
chart path). Cite source filenames. State row counts: parsed / skipped / deduped — the
user should be able to audit you.
