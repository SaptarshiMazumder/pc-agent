# Paper Pile — a WORKBENCH sample

A reference agent for the shape where **the user arrives with a pile, not a question** — and
which keeps working when nobody is looking at it.

## What to copy from it

**The window opens on the work, not on a prompt.** Drop zone and queue on the left, library in
the middle. Chat exists as a third view because "which of these disagree?" is a real question —
but it is not the front door.

**Per-item state, and one failure never stops the batch.** See `useQueue.ts`. A single global
spinner over forty files tells the user nothing they can act on; aborting on the first bad file
means fixing one thing and rerunning everything to find the next.

**The store is the product.** Every document becomes `workspace/library/<slug>.md`. The app reads
that, `library_search` queries it, and it is still there in six months. An agent that summarised
into the transcript would have to re-read every PDF to answer "which mention X?" — and would
answer differently each time.

**Three tools the APP calls, not just the model.** `library_index`, `library_search` and
`library_links` are `tools.invoke` — no chat turn, no model call, no tokens. That is why the list
is instant and the search is honest: it can only return what is really stored.

**Two kinds of search, deliberately.** Typing filters titles locally; Enter runs full text across
every note. "Where is that paper" and "what did I read about X" are different questions.

**A view the list cannot give you.** `library_links` parses the `[[slug]]` links between notes, so
the Connections tab shows orphans (filed, connected to nothing) and broken links (a reference to
a note that was never written). Both accumulate silently and are invisible in a list.

**It works while nobody is watching.** `heartbeat = "12h"` plus `HEARTBEAT.md`: check the watched
topics, and only report work that bears on a note already in the library. See below.

**It is given a folder, not a file.** `library_scan` hashes what is in a directory and reports
what is *new* there — so a scan is idempotent and can therefore be run on a heartbeat. Pointing
the agent at the same folder twice is a no-op, which is the whole reason the feature works.

**It answers from the source, not from its own summary.** `library_put` chunks a document's full
text into SQLite (FTS5 + float32 vectors); `library_ask` retrieves the passages that bear on a
question so the answer can cite real sentences. Six months later the note is a summary — the index
is the evidence.

**It fans out.** Forty documents is forty sequential turns unless each gets its own reader —
`spawn_subagent`, one per document, then the parent does the cross-linking because no child ever
saw the other thirty-nine.

## The RAG, and the honesty rule around it

No new dependencies: `sqlite3` (FTS5 confirmed present), the daemon's own `documents.extract_text`
for PDF/DOCX/XLSX/PPTX, and its `embeddings.build_embed_fn` for vectors.

| Piece | What it demonstrates |
|---|---|
| `library_database.py` | Content hashes (idempotent scans), chunk text, and vectors as float32 BLOBs. Brute-force cosine in Python — a personal library is thousands of chunks, so no vector extension is needed. |
| `document_chunker.py` | Packs whole paragraphs to a budget instead of cutting a fixed window, because half a sentence is what gets quoted back as evidence. |
| `document_embedder.py` | Owns the one sanctioned fallback, and names it. |
| `semantic_search_tool.py` | Returns **passages, not an answer** — a tool that synthesised one would be a second, invisible model call whose reasoning nobody can inspect. |

**The fallback is the part worth copying.** Embeddings need a provider and a key. Without one,
retrieval drops to BM25 — genuinely weaker. So every result carries `mode` and `mode_reason`, and
the agent is required to say when a search was lexical. Falling back is correct; falling back
silently teaches the user their agent "doesn't understand synonyms" when the truth is that nobody
set a key.

Two related refusals, both deliberate:

- **A failed embedding retires the embedder for the run** and puts the real error in
  `unavailable_reason`. `build_embed_fn` returns a callable whenever a model *name* is configured —
  it cannot know whether a key exists — so without this, ingesting forty documents means forty
  network timeouts to learn the same fact once. The error is carried, not swallowed.
- **A document whose text will not extract is an ERROR, not an empty note.** `extract_text` returns
  `None` for both "unsupported" and "corrupt", so falling back to reading a PDF's raw bytes yields
  a few characters of header that chunk and index perfectly happily — leaving an entry that looks
  like a document and contains none of it. (This sample's own test caught exactly that bug.)

## The window it ships

An agent with its own `entry` **replaces** the built-in window and inherits everything that window
provided. Skipping any of it looks like a broken app, not a missing feature:

| Section | RPC | Why it cannot be skipped |
|---|---|---|
| Settings | `config.get` / `config.set` | Declaring `[[settings]]` with nowhere to type them means the heartbeat reads an empty watch list forever and reports "nothing to do" — which looks exactly like working. |
| Chat history | `sessions.list/history/rename/delete` | The transcripts are already on disk. An app that ignores them makes every session look like the agent's first. |
| Artifacts | `workspace.list/delete` | The notes, filed sources and digests are real files; this is the view that proves it. |

Two wire details worth stealing, both verified against the gateway rather than assumed:

- `config.set` reports a refusal as **data** — `{saved: false, error}` — not as a thrown error. Catch
  only exceptions and you show "Saved" for a save that never happened.
- `workspace.list` returns `{entries: [], error}` — a failure and an empty workspace differ by one
  field. Ignore it and an unreadable workspace renders as "no files".

## The autonomy, and why it is shaped like this

| Piece | What it demonstrates |
|---|---|
| `HEARTBEAT.md` | An unattended tick with **a cheap no-op path first** — empty watch list → `heartbeat_respond(outcome='nothing-to-do', notify=false)` and stop. Most ticks are this. |
| `memory_search` before `web_search` | Anything the user already dismissed is not a finding. Re-proposing it is how a watcher becomes noise. |
| `remember` | Stores **judgements** ("not interested in benchmark tables"), never events ("ran a tick"). |
| `web_fetch` after `web_search` | A title and a snippet are not evidence. Unfetchable → not a finding. |
| Writes to `watch/<date>.md` | A tick whose only output was a notification did work that is gone by morning. |
| `notify=true` only for relevance | Interesting-but-unrelated is a note, not an interruption. |
| Never ingests what it found | A watch finding is a **proposal**. The user decides what enters their library. |
| `library_scan` on `PAPER_PILE_INBOX` | A tick may scan the watched folder, but **never ingests** from it — a finding is a proposal, written to `watch/<date>.md`. |
| `cron` + `weekly-digest` skill | A scheduled run reports what **moved**, omits empty sections, and `report_outcome`s so a silent failure is distinguishable from a quiet week. |

**`[subagents] allow = []` is not a mistake.** The allowlist gates delegation to a *named other*
agent; spawning a copy of yourself is always permitted. So an empty list reads exactly as
intended: fan out to your own readers, never hand work to another agent.

**Sub-agents are OFF by default** — `subagents_enabled = False`, enabled with `AGENTD_SUBAGENTS=1`
— and when off, `spawn_subagent` is not registered at all. This is why the ingest skill checks for
the tool before promising a parallel read: an agent that announces a capability it does not have
reads to the user as the model lying. When on, `subagent_max = 4` concurrent and
`subagent_max_depth = 1` (children cannot spawn further).

> Worth copying: the cap does **not** queue. Spawns past it return the plain sentence
> `sub-agent limit reached (4 concurrent); try again when some finish.` as an ordinary tool
> result — not an error. An agent that reads that as "done" silently drops the document. The
> skill therefore fans out in waves and keeps an explicit outstanding list, which is the general
> shape for any capped parallel tool.

**The two `[[settings]]`** (`PAPER_PILE_WATCH`, `PAPER_PILE_DIGEST_DAY`) are how the user
configures all of that without editing a file. They are declared in `agent.toml`; the values live
on the machine and never ship in the package.

> `validate_agent` warns that nothing references `${PAPER_PILE_WATCH}` — correct and expected
> here: the agent reads it from the environment during a heartbeat rather than substituting it
> into an MCP command line.

## React + Vite

```
app/    source — edit here, then `npm install && npm run build`
ui/     BUILT output — what the daemon serves and what ships. Committed.
```

`base: './'` so assets resolve under `/apps/<id>/`. `outDir: '../ui'`. The SDK is imported as
`@agentd/client` and bundled, so there is no vendored copy to drift.

**Nobody installing this agent needs Node** — they get `ui/`. Node is only needed to *change* it.

> `validate_agent` also warns about a missing `ui/vendor/agentd-client.js` and a missing sign-in
> panel. Both are `UiRules` not yet understanding a bundled React build; the SDK is inside
> `ui/assets/index-*.js`.

## Run it

It lives in `agents/samples/`, which the registry does not scan. Copy the folder into `agents/`
to run it, or read it where it is.
