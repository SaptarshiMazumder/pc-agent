---
name: inbox-triage-logic
description: Use when running the morning inbox triage, or when asked to check for emails needing replies.
always: false
---

# Inbox Triage (Browser Mode)

1. Use the `browser` tool to navigate to the user's webmail (e.g., Gmail or Outlook). 
2. Because the environment is configured to attach to the user's active Chrome, it will open a new tab in their EXISTING browser window. 
3. If they are already signed into their email in that browser, it will use that active session instantly.
4. Once the inbox is visible, read the emails directly from the screen. **Crucially, ONLY look at emails that have NOT been replied to.**
   - Skip any email that has a "replied" arrow/icon next to it.
   - You can also type "is:unread" (in Gmail) or filter by "Unread" to ensure you only see emails that haven't been dealt with.
5. **Important for getting enough emails:** The first screen only shows a few recent emails. To gather more, you MUST scroll down. Use `act` `kind="scrollIntoView"` on the last visible email in the list, then `act` `kind="wait"` `load_state="networkidle"`, and then take a new snapshot. Repeat this 2-3 times to ensure you have captured a large batch of emails before you start filtering.
6. From those unhandled emails across all the pages you scrolled, determine which ones genuinely need a human reply (e.g., questions, urgent requests, action items).
7. Ignore newsletters, automated alerts, and FYI-only emails.
8. Output a concise summary formatted as a markdown list. For each email that needs a reply, state:
   - **Sender:** [Who sent it]
   - **Subject:** [Subject]
   - **Why it needs a reply:** [Brief reason]
9. If running on a heartbeat tick, perform the triage and notify the user with the summary if there are actionable items.