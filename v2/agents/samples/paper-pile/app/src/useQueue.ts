/* The ingest queue — the mechanism behind the workbench shape.
 *
 * Two rules, and each exists because the obvious alternative is worse:
 *
 * PER-ITEM STATE, NOT A GLOBAL SPINNER. One "processing…" over forty files tells the user
 * nothing they can act on: they cannot see that file 12 failed while 13–40 succeeded, so the
 * whole batch becomes suspect and gets run again.
 *
 * ONE FAILURE NEVER STOPS THE BATCH. A bad file is a fact about that file. Aborting means the
 * user fixes one thing, reruns everything, and discovers the next bad one — an afternoon of that
 * instead of one pass and a short list.
 *
 * Concurrency is 1 on purpose: each item is a full model turn that reads a document and writes a
 * note. Running four at once would quadruple the cost of a mistake in the prompt and interleave
 * four sets of tool rows in one conversation.
 */

import { useCallback, useRef, useState } from 'react'
import type { AgentdClient } from '@agentd/client'

export type ItemState = 'queued' | 'uploading' | 'working' | 'done' | 'failed'

export interface QueueItem {
  id: string
  name: string
  size: number
  state: ItemState
  note: string
}

const MAX_BYTES = 25 * 1024 * 1024

/** How long ONE document may hold the queue.
 *
 *  Concurrency is 1, so a single pathological file — a scanned PDF the agent keeps trying to read
 *  — stalls all the others indefinitely, and the only signal is a row that says "working" forever.
 *  A ceiling turns that into one failed row and a queue that keeps moving. Generous on purpose: a
 *  long paper legitimately takes minutes. */
const TURN_TIMEOUT_MS = 4 * 60 * 1000

export function useQueue(
  client: AgentdClient,
  ask: (text: string) => Promise<void>,
  onIngested: () => void,
  abort: () => Promise<void>,
) {
  const [items, setItems] = useState<QueueItem[]>([])
  const running = useRef(false)

  const patch = useCallback((id: string, change: Partial<QueueItem>) => {
    setItems((prev) => prev.map((it) => (it.id === id ? { ...it, ...change } : it)))
  }, [])

  const add = useCallback((files: FileList | File[]) => {
    const list = Array.from(files ?? [])
    setItems((prev) => [
      ...prev,
      ...list.map((f) => ({
        id: `${f.name}-${f.size}-${Math.random().toString(36).slice(2, 8)}`,
        name: f.name || 'file',
        size: f.size,
        // Refused HERE, with the name on screen, rather than as an opaque failure four steps
        // later when the daemon rejects the upload.
        state: (f.size > MAX_BYTES ? 'failed' : 'queued') as ItemState,
        note: f.size > MAX_BYTES ? `too large (${Math.round(f.size / 1048576)} MB)` : '',
        file: f,
      })),
    ] as QueueItem[])
    // Keep the File objects out of React state — they are not serialisable and never rendered.
    for (const f of list) fileStore.set(`${f.name}-${f.size}`, f)
  }, [])

  const run = useCallback(async () => {
    if (running.current) return
    running.current = true
    try {
      // Re-read from state each step: the user may drop more files while this is running, and
      // they should join the same pass rather than need a second click.
      for (;;) {
        const next = await new Promise<QueueItem | undefined>((resolve) =>
          setItems((prev) => {
            resolve(prev.find((i) => i.state === 'queued'))
            return prev
          }),
        )
        if (!next) break

        const file = findFile(next)
        if (!file) {
          patch(next.id, { state: 'failed', note: 'the file is no longer available' })
          continue
        }
        try {
          patch(next.id, { state: 'uploading' })
          const dataBase64 = await readBase64(file)
          const res: any = await client.request('workspace.upload', {
            name: next.name,
            path: 'inbox',
            dataBase64,
          })
          if (!res || res.ok === false) throw new Error(res?.error || 'upload failed')
          // USE THE NAME THE DAEMON SAVED, not the one we sent. Uploads dedupe on collision
          // (`report.pdf` -> `report (2).pdf`), so a second drop of the same filename would
          // otherwise send the agent to read a file that is not the one we just wrote.
          const saved = String(res.name || next.name)

          patch(next.id, { state: 'working' })
          // The INGEST is a chat turn on purpose: reading a document and deciding what matters
          // is judgement, not a function call. The skill holds the procedure; this names the two
          // outcomes that must happen, because "ingest it" left the indexing implicit and a note
          // without an index is a document that cannot be searched by what it says.
          const timedOut = await withTimeout(ask(
            `Ingest the document at inbox/${saved} into the library, following the ` +
              `ingest-a-document skill. Write its note AND call library_put with ` +
              `source_path='inbox/${saved}' so the full text is indexed for search. If this ` +
              `document is already in the library, replace its note and re-index it rather than ` +
              `adding a second copy. Reply with the slug you used, or the reason you could not.`,
          ))
          if (timedOut) {
            // Abort, or the abandoned run keeps going and its tool calls interleave with the next
            // document's turn in the same session.
            await abort()
            patch(next.id, {
              state: 'failed',
              note: `no result after ${TURN_TIMEOUT_MS / 60000} minutes — skipped. Open Ask to see where it got stuck.`,
            })
            continue
          }

          // VERIFY, DO NOT ASSUME. The turn has ended, which is not the same as the work having
          // happened — the agent may have refused a scanned PDF with no text layer, and reporting
          // "added to the library" for it is the exact failure this whole app is built to avoid.
          const landed = await didLand(client, saved)
          if (landed.state === 'present') {
            patch(next.id, { state: 'done', note: `in the library as ${landed.file}` })
          } else if (landed.state === 'absent') {
            patch(next.id, {
              state: 'failed',
              note: 'the agent did not add it — open Ask to see why',
            })
          } else {
            patch(next.id, { state: 'done', note: 'ingested — could not confirm, check Library' })
          }
          onIngested()
        } catch (e) {
          patch(next.id, { state: 'failed', note: (e as Error)?.message ?? String(e) })
        }
      }
    } finally {
      running.current = false
    }
  }, [client, ask, patch, onIngested])

  const clearFinished = useCallback(() => {
    // Only the finished ones. Clearing work in flight would make rows vanish under the user
    // while their turns carry on regardless.
    setItems((prev) => prev.filter((i) => i.state !== 'done' && i.state !== 'failed'))
  }, [])

  /** Give up on the batch: stop the run in flight and drop everything not started. The rows that
   *  already finished stay, because what happened, happened. */
  const stop = useCallback(async () => {
    await abort()
    setItems((prev) =>
      prev
        .filter((i) => i.state !== 'queued')
        .map((i) =>
          i.state === 'working' || i.state === 'uploading'
            ? { ...i, state: 'failed' as ItemState, note: 'stopped' }
            : i,
        ),
    )
    running.current = false
  }, [abort])

  return { items, add, run, stop, clearFinished, busy: running.current }
}

/** Resolve to `true` if the promise did not settle in time. The work is NOT cancelled here —
 *  the caller decides what to do about it, because aborting a run is its business, not a timer's. */
function withTimeout(work: Promise<void>): Promise<boolean> {
  let timer: ReturnType<typeof setTimeout>
  return Promise.race([
    work.then(() => false),
    new Promise<boolean>((resolve) => {
      timer = setTimeout(() => resolve(true), TURN_TIMEOUT_MS)
    }),
  ]).finally(() => clearTimeout(timer))
}

/** Did this upload actually become a note?
 *
 *  THREE OUTCOMES, NOT TWO. "Present", "absent", and "could not check" are different facts, and
 *  collapsing the third into "absent" accuses the agent of failing whenever the CHECK breaks.
 *  Only `absent` is evidence of a failed ingest.
 *
 *  It asks `library_index` — the notes on disk — rather than reading the transcript, because the
 *  agent's own account of what it did is exactly what should not be trusted here. Matched on
 *  `source`, which the ingest skill sets to where the document came from. */
type Landing = { state: 'present'; file: string } | { state: 'absent' } | { state: 'unknown' }

async function didLand(client: AgentdClient, savedName: string): Promise<Landing> {
  let text: string
  try {
    const res: any = await client.request('tools.invoke', { name: 'library_index', params: {} })
    text = String(res?.text ?? res?.content?.[0]?.text ?? '')
  } catch {
    return { state: 'unknown' }
  }
  // Prose (an empty library) is a real answer: nothing landed. Anything else unparseable is not.
  if (!text.trim().startsWith('{')) return { state: 'absent' }
  try {
    const docs = JSON.parse(text).documents ?? []
    const hit = docs.find((d: any) => String(d.source || '').includes(savedName))
    return hit ? { state: 'present', file: String(hit.file || hit.title || savedName) } : { state: 'absent' }
  } catch {
    return { state: 'unknown' }
  }
}

/** Files live outside React state — see `add`. */
const fileStore = new Map<string, File>()
const findFile = (item: QueueItem) => fileStore.get(`${item.name}-${item.size}`)

function readBase64(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader()
    reader.onload = () => resolve(String(reader.result).split(',')[1] ?? '')
    reader.onerror = () => reject(reader.error)
    reader.readAsDataURL(file)
  })
}
