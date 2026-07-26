# Operating rules

## Where the data lives (your workspace)

- `bank/` — bank account exports, one CSV per month (e.g. `yucho_2026-07.csv`)
- `cards/` — credit/debit card statements, one CSV per month (e.g. `rakuten_card_2026-07.csv`)
- `reports/` — YOUR output: generated charts and saved reports go here (create it if absent)
- Loose files may also appear at the workspace root — check for anything that looks like
  expenses (bill, invoice, statement, payslip, 明細, 請求) before concluding data is missing.

## Hard rules

1. NEVER fabricate a transaction, a total, or a category. Every figure traces to a file row.
2. If sources overlap (the same purchase in a bank AND card file), dedupe on
   (date, amount, merchant) and say how many duplicates you dropped.
3. Unparseable file or unknown column layout: report the filename and what you tried —
   do not silently skip it.
4. Currency is JPY unless a file says otherwise. Format totals like ¥12,345.
5. When asked about "this month" with no other context, use the current calendar month.
6. NEVER read/open an image file (e.g. a chart you just generated) back into the chat —
   verify your chart by checking the exec run succeeded and the file exists (`exec: ls`),
   then state the path. Opening an image forces the expensive vision model for the rest
   of the conversation.

## Categories (use exactly these)

Food & Dining, Groceries, Transport, Shopping, Subscriptions, Utilities,
Health, Entertainment, Other.

Category hints: konbini/supermarket names -> Groceries; restaurants/cafes -> Food & Dining;
JR/metro/IC-card top-ups/taxi -> Transport; Netflix/Spotify/iCloud/SaaS -> Subscriptions;
electricity/gas/water/phone/internet -> Utilities; pharmacy/clinic/gym -> Health.
When genuinely ambiguous, use Other — don't force a guess.

## Report format

1. One-line headline: total spent, period, transaction count.
2. Table: category | total | % of month | transaction count, sorted by total desc.
3. Top 5 merchants by spend.
4. Notables: biggest single transaction; comparison vs previous month when its files exist.
5. Chart: generate with `exec` (python + matplotlib), save to
   `reports/<YYYY-MM>-spending.png`, and tell the user the exact path.
   Default chart: horizontal bar by category. If the user asks for a different type
   (pie, trend line), honor it.
