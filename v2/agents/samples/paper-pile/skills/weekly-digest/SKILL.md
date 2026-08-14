---
name: weekly-digest
description: Use for the scheduled weekly digest, or when asked what changed recently — what entered the library, what it contradicted, and what is still open.
always: false
---

# The weekly digest

Once a week, write what actually **moved** — not a list of what was added. "You added 6 papers"
is something the user can see; "three of them argue the opposite of the one you saved in March"
is why they keep the agent.

## Scheduling it

The user sets `PAPER_PILE_DIGEST_DAY`. If it is set and no cron job exists yet, create one:

```
cron(action='create', schedule='weekly on <day> at 08:00',
     task='Write this week's digest following the weekly-digest skill.')
```

Empty setting → no digest, and do not create the job. One job, not one per week: check
`cron(action='list')` before creating.

## What goes in it

Write to `digests/<date>.md`. Four sections, and **any that is empty is omitted** — a
digest with three headings and nothing under them teaches the user to stop opening it.

**Added.** Each new note in one line: what it claims, not what it is about.

**Changed your picture.** The findings that bear on notes you already had. This is the section
that justifies the whole agent, so it goes first when it is not empty:
- *X contradicts the scaling claim in [[y]] — same benchmark, opposite conclusion.*

**Still open.** Questions an existing note left explicitly unanswered and nothing has answered.
Only questions the notes actually state — not ones you think are interesting.

**Nothing to report.** If all three above are empty, write exactly that and stop. A quiet week
honestly reported is worth more than a padded one.

## Rules

- **Read the notes, do not recall them.** `library_index` and `library_search` — never write a
  digest from memory of the conversation, because the memory is of what you SAID, and the notes
  are what you actually stored.
- **A contradiction claim must name both sides.** If you cannot cite the sentence in each note,
  it is not a contradiction, it is a hunch.
- **`report_outcome`** at the end of a scheduled run — `done` with what you wrote, or `blocked`
  with why. A cron run that reports nothing is indistinguishable from one that never fired.
