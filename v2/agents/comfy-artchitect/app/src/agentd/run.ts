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

/** THE MAP FROM CHAT TO ITS MEDIA IS A FOLDER: references/<chat-key>/. The workspace is the
 *  ACCOUNT's, shared by every conversation, so a flat references/ showed a new chat every file
 *  every earlier chat had added — and the agent, told to use "the reference", picked one. Keyed
 *  by the session key the daemon already carries into every tool call, so comfy_upload can find
 *  this chat's folder with nothing new crossing the wire. Chat keys are already path-safe
 *  ("chat-<time>-<rand>"); the replace is there for any key that is not, and the plugin applies
 *  the SAME rule so the two sides name the same folder. */
export const referenceDirFor = (sessionKey: string) =>
  `${REFERENCE_DIR}/${sessionKey.replace(/[^A-Za-z0-9._-]/g, '_') || '_'}`

/** The one turn that tells the agent what was added and what to do with it.
 *
 *  A FUNCTION BECAUSE IT HAS TWO CALLERS NOW. Media added between turns is announced at once;
 *  media added DURING a turn is held and announced when that turn ends. Two copies of this
 *  sentence would be two things to keep in step, and the agent's behaviour depends on its exact
 *  wording — "don't ask me to paste it again" is load-bearing.
 *
 *  First person, so it reads as the user's own ask, and FULL paths (this chat's folder included)
 *  so the agent can comfy_upload them verbatim without seeing a pixel or guessing which folder
 *  belongs to this conversation. */
export function referenceInstruction(paths: string[]): string {
  const them = paths.length > 1 ? 'them' : 'it'
  return (
    `I've added reference media for this chat: ${paths.join(', ')}. ` +
    `Upload ${them} to the ComfyUI instance with comfy_upload and use ${them} as the ` +
    `workflow input (the reference image / start frame / video) — don't ask me to paste ${them} again.`
  )
}

export function useRun(client: AgentdClient | null) {
  /** Send the composer's text, with whatever files are staged. */
  const send = useCallback(
    async (text: string): Promise<void> => {
      const body = text.trim()
      const { currentSessionKey: key, sessions, patch, append } = useApp.getState()
      const session = sessions[key]
      if (!client || !session || (!body && !session.pending.length)) return

      const sending = session.pending
      const wireAttachments = sending.map(({ name, mimeType, dataBase64 }) => ({
        name,
        mimeType,
        dataBase64,
      }))
      const displayAttachments = sending.map(({ name, mimeType, thumbnailDataUrl }) => ({
        name,
        mimeType,
        ...(thumbnailDataUrl ? { thumbnailDataUrl } : {}),
      }))

      // MARKED BEFORE THE AWAIT, not after. Everything up to the await runs synchronously, so a
      // flag set afterwards is still false for anything that reaches here in the same tick — and
      // the message goes out twice.
      patch(key, { pending: [], running: true, awaitingGpu: false })
      append(key, [{ kind: 'user', text: body, files: displayAttachments, ts: Date.now() }])

      try {
        await client.send({
          sessionKey: key,
          message: body,
          ...(wireAttachments.length ? { attachments: wireAttachments } : {}),
        })
      } catch (e) {
        // SURFACED IN THE THREAD, and `running` released. A send that failed silently leaves a
        // composer that is disabled forever, waiting for a run the daemon never started. Put the
        // original attachments back too: the display item only retains small previews, so this is
        // the last recoverable copy of the bytes the user selected.
        patch(key, { pending: sending, running: false })
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
    // Apply the cap before reading or decoding. A large drag must not briefly hold every full
    // image and every decoded bitmap merely to discard most of them after Promise.all settles.
    const accepted = files.slice(0, Math.max(0, MAX_FILES - session.pending.length))
    if (!accepted.length) return
    const read = await Promise.all(accepted.map((file) => readFile(file)))
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
      const dir = referenceDirFor(key)

      // Reference files are uploaded immediately and never rendered in this window, so avoid
      // decoding a throwaway local thumbnail for what may be a very large source image.
      const read = await Promise.all(files.map((file) => readFile(file, false)))
      const saved: string[] = []
      const failed: string[] = []
      for (const f of read) {
        try {
          const res: any = await client.request('workspace.upload', {
            agentId: AGENT_ID,
            path: dir,
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
      const paths = saved.map((name) => `${dir}/${name}`)

      /* THE UPLOAD IS DONE; SAYING SO MAY HAVE TO WAIT. A turn cannot be sent while one is
         already running, and that is the only half of this that ever needed to wait — so the
         paths go in the queue and `flushReferences` announces them the moment the run ends.
         Re-read rather than closed over: the upload above was awaited, and a run can have
         started (or finished) while it was in flight. */
      const now = useApp.getState().sessions[key]
      if (now?.running) {
        useApp.getState().patch(key, {
          pendingReferences: [...(now.pendingReferences || []), ...paths],
        })
        // SAID IN THE THREAD, because otherwise this is a click with no visible consequence for
        // however long the turn lasts — indistinguishable from the button having done nothing.
        append(key, [
          {
            kind: 'system',
            tone: 'info',
            text: `Added ${paths.join(', ')} — the agent is mid-turn, so I'll hand ${
              paths.length > 1 ? 'them' : 'it'
            } over as soon as this one finishes.`,
            ts: Date.now(),
          },
        ])
        return
      }
      await send(referenceInstruction(paths))
    },
    [client, send],
  )

  /** Hand over whatever `sendReferences` had to hold. Called by the window when a run ends —
   *  App owns WHEN (it is watching the run), this owns WHAT IS SAID.
   *
   *  The queue is cleared BEFORE the send, not after: `send` sets `running` true again, and a
   *  flush that cleared afterwards would still see a full queue on the re-render in between and
   *  announce the same files twice. Same order as the GPU resume beside it, for the same reason. */
  const flushReferences = useCallback(async (): Promise<void> => {
    const { currentSessionKey: key, sessions, patch } = useApp.getState()
    const queued = sessions[key]?.pendingReferences || []
    if (!queued.length) return
    patch(key, { pendingReferences: [] })
    await send(referenceInstruction(queued))
  }, [send])

  return { send, abort, addFiles, removeFile, sendReferences, flushReferences }
}
