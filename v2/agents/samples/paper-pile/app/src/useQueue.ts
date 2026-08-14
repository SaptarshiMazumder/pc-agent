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

export function useQueue(
  client: AgentdClient,
  ask: (text: string) => Promise<void>,
  onIngested: () => void,
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
          // is judgement, not a function call. The skill holds the procedure; this just points
          // the agent at the file.
          await ask(`Ingest the document at inbox/${saved} into the library.`)
          patch(next.id, { state: 'done', note: 'added to the library' })
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

  return { items, add, run, clearFinished, busy: running.current }
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
