---
name: ingest-a-folder
description: Use when the user points at a DIRECTORY rather than handing over files — scan it, work out what is genuinely new, ingest that, and report. Also used by the heartbeat for PAPER_PILE_INBOX.
always: false
---

# Working a folder

The user says "everything in `D:/Papers`". They are not asking you to read 400 files; they are
asking you to make the library reflect that folder. Those are different jobs, and the second one
is mostly about working out how little there is to do.

## 1. Scan before you promise anything

```
library_scan(folder='D:/Papers', recursive=true)
```

It reads and hashes; it ingests nothing. Four states come back and they are not the same job:

| state | what it means | what you do |
|---|---|---|
| `new` | never seen | read it, write a note, index it |
| `changed` | seen before, bytes differ | re-read and **replace** the note |
| `duplicate` | same bytes, different path | **skip it** — the document is already filed under another name |
| `indexed` | already done, unchanged | nothing |

**Report the size of the job before starting it.** "412 files, 38 new, 2 changed, the rest already
in the library — that is about 40 documents to read. Shall I?" A user who expected ten minutes and
gets an hour needed to know first.

**`CAPPED` in the result means the scan stopped early.** There are more files than you saw. Raise
`max_files` or scan a subfolder — and never describe a capped scan as complete.

**`unreadable` entries are reported, not ignored.** Usually permissions. Name the files.

## 2. Ingest only what the scan flagged

For each `new` or `changed` file, follow `ingest-a-document` — including the wave discipline for a
batch of five or more. The scan hands you a `suggested_slug`; use the real title from inside the
document if it differs, because the file name is often `final_v3_REAL.pdf`.

For `changed`, the note is REPLACED, not appended. The old note describes bytes that no longer
exist.

## 3. Index and file each one

After writing the note:

```
library_put(slug='attention-is-all-you-need',
            source_path='D:/Papers/whatever_it_was_called.pdf',
            title='Attention Is All You Need')
```

Two things happen, and both matter:

- **The full text is indexed**, so `library_ask` can later quote the actual sentences instead of
  your summary of them.
- **A copy of the source is filed** as `library/sources/<slug>.<ext>` — the sources end up carrying
  the same names as the notes. This is a COPY. The user's folder is never renamed, moved or
  touched, because they pointed you at it, they did not hand it over.

If `library_put` reports it could not extract any text, **that document has no note.** Say which
file and why. A note written from the file name is the one failure this agent cannot come back
from.

## 4. Report

Per document: added, replaced, skipped as duplicate, or failed and why. Then the part that is
actually worth reading — what the new documents mean for the ones already there. Run
`library_index` and add the cross-links; a folder ingest that produced forty disconnected notes
has built a folder, not a library.

## On a heartbeat

`PAPER_PILE_INBOX` is scanned the same way, with two differences: nobody can answer a question, and
nobody asked you to fill the library while they slept.

**So do not ingest.** Scan, and if there is anything new, write what you found into
`watch/<date>.md` and say so in `heartbeat_respond`. The user decides what enters the library.
Empty or unset setting → do not scan at all.
