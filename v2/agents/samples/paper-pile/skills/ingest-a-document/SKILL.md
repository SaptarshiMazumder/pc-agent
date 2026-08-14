---
name: ingest-a-document
description: Use when a document arrives — an uploaded PDF or file, or a pasted link — and it needs to become a note in the library. The exact procedure and note format.
always: false
---

# Turning a document into a note

One document, one note, one file: `library/<slug>.md`. The note is the product — the
library tools read it, the app renders it, and in six months it is the only thing left. The PDF
may be gone; the note has to stand alone.

## The procedure

1. **Get the text.**
   - An uploaded file → `read` it. It text-extracts `.pdf`, `.docx`, `.xlsx` and `.pptx`, so
     there is nothing to install and no parsing to write.
   - A link → `web_fetch` it.
   - If extraction returns nothing usable (a scanned image with no text layer), **say so and
     stop**. Do not write a note guessing from the filename — a library entry that invents its
     own contents is worse than a missing one, because it will be believed.

2. **Choose the slug.** Kebab-case, from the real title: `attention-is-all-you-need.md`. If a
   file with that slug already exists, you are looking at a document already in the library —
   say so and ask before overwriting.

3. **Write the note.** Exactly this shape — the header keys are PARSED by `library_index`:

```markdown
---
title: Attention Is All You Need
source: https://arxiv.org/abs/1706.03762
added: 2026-08-14
tags: transformers, attention, architecture
---

One paragraph: what this document actually claims, in your own words.

## Key points
- The specific claims, with numbers where the document gives numbers.
- What is asserted versus what is demonstrated — they are not the same.

## Connects to
- [[other-slug]] — one line on how they relate, and whether they agree.
```

4. **Index the full text.** The note is your summary; the index is the evidence.

```
library_put(slug='attention-is-all-you-need', source_path='<the file>', title='<real title>')
```

   Without this, every later question can only be answered from your own summary — so anything
   the note did not anticipate ("what sample size?") means re-reading the PDF. With it,
   `library_ask` retrieves the actual sentences. It also files a COPY of the source as
   `library/sources/<slug>.<ext>`, so the sources carry the same names as the notes; the
   original is never moved or renamed.

   If it reports that no text could be extracted, **there is no note for that document.** Delete
   the note if you already wrote one, and tell the user which file and why.

5. **Cross-link.** Before finishing, `library_index` to see what is already there and add
   `[[slug]]` links where documents genuinely relate. A link that only says "both about ML" is
   noise; a link that says "contradicts the scaling claim in [[other]]" is why the library
   exists.

## Rules

- **Never summarise from the title or the abstract alone.** If you only read the abstract, the
  note says so.
- **Numbers come from the document.** If a figure is not in the text you extracted, it does not
  go in the note.
- **`source` is where it came from** — the URL, or the original filename for an upload. It is
  how the user gets back to the real thing when the note is not enough.
- **`added` is today's date**, so "what did I add this week" is answerable.
- One note per document. Two papers in one PDF are two notes.

## A batch: read them in PARALLEL

One document is one turn. Forty documents read one after another is forty sequential turns, and
the last one lands long after the user has stopped watching.

**First, check you actually have `spawn_subagent`.** It is off on most installs. If it is not in
your tools, read the pile sequentially, oldest first, and say so up front — "44 documents, I will
work through them in order" — so the user knows what they are waiting for. Do not announce a
parallel read you cannot perform.

`spawn_subagent` gives each document its own reader with its own context. Give it a COMPLETE,
standalone task, because the child sees none of this conversation:

> Read `inbox/attention-is-all-you-need.pdf` and write its library note following the
> `ingest-a-document` skill. Return only the slug you wrote, or the reason you could not.

### Fan out in waves, not all at once

**There is a cap on concurrent children — four by default.** Spawning forty in one turn does not
queue them: the ones past the cap come straight back with

> sub-agent limit reached (4 concurrent); try again when some finish.

**That sentence is not a result. It means that document was never read.** It arrives as an
ordinary tool result, not an error, which is exactly why it gets mistaken for one — and a
document counted as done that nobody opened is the worst outcome this skill has, because it looks
identical to a success.

So: spawn a wave, wait for it to come back, spawn the next. Keep an explicit list of what is
still outstanding, and a document only leaves that list when a child returns a slug or a real
reason. If you hit the limit message, that document goes back on the list for the next wave.

### When they are all back

1. `library_index` once, to see everything that landed.
2. Add the **cross-links** yourself. A child that never saw the other thirty-nine documents
   cannot know that its paper contradicts one of them — connecting them is the parent's job and
   the reason the batch is worth more than its parts.
3. Report per document: added, or why not. Never a bare "done" for a batch — the one that failed
   is the one the user needs to hear about. If the outstanding list is not empty, say which
   documents are still unread rather than reporting the batch complete.

**Do not fan out for two or three documents.** A subagent costs a whole run; below about five
the coordination costs more than it saves.
