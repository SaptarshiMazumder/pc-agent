/* Sending, stopping, and attaching files.
 *
 * THE OTHER HALF OF `run-events.ts`: that folds frames arriving FROM the daemon, this is what goes
 * TO it. Kept apart because the reading half is fiddly and generic while this half is short and is
 * where an agent's own behaviour goes — a system preamble, a default attachment, a refusal to send
 * while something is unset.
 *
 * `message`, NOT `text`. `chat.send` reads `params.message` and rejects an empty one; sending
 * `text` gets a rejection that names nothing useful.
 */

import type { AgentdClient } from '@agentd/client'
import { useCallback } from 'react'

import { MAX_FILES, readFile } from './chat'
import { AGENT_ID } from './client'
import { useApp } from '../state/store'

/** Where reference media lands in the agent's workspace. NOT `uploads/` (chat attachments, which
 *  the model sees as vision): reference media is INPUT for the ComfyUI workflow, so it goes
 *  straight to the instance and the model only ever hears its filename. */
const REFERENCE_DIR = 'references'

export function useRun(client: AgentdClient | null) {
  /** Send the composer's text, with whatever files are staged. */
  const send = useCallback(
    async (text: string): Promise<void> => {
      const body = text.trim()
      const { currentSessionKey: key, sessions, patch, append } = useApp.getState()
      const session = sessions[key]
      if (!client || !session || (!body && !session.pending.length)) return

      const sending = session.pending

      // MARKED BEFORE THE AWAIT, not after. Everything up to the await runs synchronously, so a
      // flag set afterwards is still false for anything that reaches here in the same tick — and
      // the message goes out twice.
      patch(key, { pending: [], running: true })
      append(key, [{ kind: 'user', text: body, files: sending, ts: Date.now() }])

      try {
        await client.send({
          sessionKey: key,
          message: body,
          ...(sending.length ? { attachments: sending } : {}),
        })
      } catch (e) {
        // SURFACED IN THE THREAD, and `running` released. A send that failed silently leaves a
        // composer that is disabled forever, waiting for a run the daemon never started.
        patch(key, { running: false })
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: `Could not send. ${String((e as Error)?.message || e)}`,
            ts: Date.now(),
          },
        ])
      }
    },
    [client],
  )

  const abort = useCallback(async (): Promise<void> => {
    if (!client) return
    try {
      await client.abort(useApp.getState().currentSessionKey)
    } catch {
      // The run may have just ended on its own. There is nothing here worth telling the user.
    }
  }, [client])

  /** Stage files for the next send. Read into attachments now, so the composer can show them. */
  const addFiles = useCallback(async (list: FileList | File[]): Promise<void> => {
    const files = Array.from(list || [])
    if (!files.length) return
    const { currentSessionKey: key, sessions, patch } = useApp.getState()
    const session = sessions[key]
    if (!session) return
    const read = await Promise.all(files.map(readFile))
    patch(key, { pending: [...session.pending, ...read].slice(0, MAX_FILES) })
  }, [])

  const removeFile = useCallback((index: number): void => {
    const { currentSessionKey: key, sessions, patch } = useApp.getState()
    const session = sessions[key]
    if (!session) return
    patch(key, { pending: session.pending.filter((_, i) => i !== index) })
  }, [])

  /** Reference media — the SEPARATE path from chat attachments.
   *
   *  Chat attachments (`addFiles`/`send`) ride the message to the LLM as vision input. Reference
   *  media does NOT: each file is written straight to the agent's workspace `references/` via
   *  `workspace.upload` (no message, no model proxy), then ONE text turn tells the agent to push
   *  them onto the instance with `comfy_upload` and wire them into the workflow. The pixels go
   *  browser -> workspace -> ComfyUI; the model only ever sees the filenames as text. This is why
   *  a 10 MB reference image no longer bloats — or breaks — every model call. */
  const sendReferences = useCallback(
    async (list: FileList | File[]): Promise<void> => {
      const files = Array.from(list || [])
      if (!client || !files.length) return
      const { currentSessionKey: existing, newSession, append } = useApp.getState()
      const key = existing || newSession(true)

      const read = await Promise.all(files.map(readFile))
      const saved: string[] = []
      const failed: string[] = []
      for (const f of read) {
        try {
          const res: any = await client.request('workspace.upload', {
            agentId: AGENT_ID,
            path: REFERENCE_DIR,
            name: f.name,
            dataBase64: f.dataBase64,
          })
          if (res?.ok) saved.push(String(res.name || f.name))
          else failed.push(`${f.name} (${res?.error || 'failed'})`)
        } catch (e) {
          failed.push(`${f.name} (${String((e as Error)?.message || e)})`)
        }
      }

      if (failed.length) {
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: `Could not add reference media: ${failed.join('; ')}`,
            ts: Date.now(),
          },
        ])
      }
      if (!saved.length) return

      // ONE instruction turn, first person so it reads as the user's own ask. Names the files and
      // the folder so the agent can comfy_upload them without seeing a single pixel.
      const them = saved.length > 1 ? 'them' : 'it'
      const list_ = saved.join(', ')
      const msg =
        `I've added reference media to my workspace under ${REFERENCE_DIR}/: ${list_}. ` +
        `Upload ${them} to the ComfyUI instance with comfy_upload and use ${them} as the ` +
        `workflow input (the reference image / start frame / video) — don't ask me to paste ${them} again.`
      await send(msg)
    },
    [client, send],
  )

  return { send, abort, addFiles, removeFile, sendReferences }
}
