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

import { MAX_CHAT_IMAGE_BYTES, MAX_FILES, readFile } from './chat'
import { AGENT_ID } from './client'
import { uploadToLibrary } from './library'
import { chatDirFor } from './workspace-files'
import { useApp } from '../state/store'

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
    `I've added reference media for this chat, not in a slot: ${paths.join(', ')}. ` +
    `If you can tell which role ${paths.length > 1 ? 'they fill' : 'it fills'}, ` +
    `move ${them} there with comfy_reference_assign; if not, ask me which in one line. ` +
    `Then keep going in this same turn: validate and run — comfy_run uploads and wires slot files itself. ` +
    `Don't ask me to paste ${them} again.`
  )
}

/** The message that asks the agent to delete files — the ONLY thing that starts a delete.
 *
 *  THE WINDOW NEVER DELETES. It could (workspace.delete is app-callable, and the Replace flow
 *  uses it for one narrow case) — it does not, because most files here are load-bearing for the
 *  job in progress in ways a button cannot know: a reference filling a slot, a validated
 *  workflow, a render that is the next input. The agent has `comfy_delete`, which checks exactly
 *  those things and refuses with a reason; putting the request through the conversation is what
 *  gets the intent, the refusal and the decision recorded where the rest of the job reads them.
 *
 *  First person, workspace-relative paths as the rail shows them, so the agent passes them to
 *  the tool as given. */
export function deletionRequest(paths: string[]): string {
  const them = paths.length > 1 ? 'these files' : 'this file'
  return (
    `Please delete ${them} from this chat's workspace: ${paths.join(', ')}. ` +
    `Use comfy_delete with those paths. If it refuses one, tell me why in a line and ask.`
  )
}

export function useRun(client: AgentdClient | null) {
  /** Send the composer's text, with whatever files are staged. */
  const send = useCallback(
    async (text: string, opts: { origin?: 'reference' } = {}): Promise<void> => {
      const body = text.trim()
      const { currentSessionKey: key, sessions, patch, append } = useApp.getState()
      const session = sessions[key]
      if (!client || !session || (!body && !session.pending.length)) return

      const sending = session.pending
      // A send INTO a live run (the daemon queues it) must not release the composer if it
      // fails: the run that was going is still going.
      const wasRunning = session.running
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
          // The window's own announcement is not the user answering anything — the daemon
          // must not take it for the answer to a checkpoint. See checkpoint_marker.
          ...(opts.origin ? { origin: opts.origin } : {}),
        })
      } catch (e) {
        // SURFACED IN THE THREAD, and `running` released. A send that failed silently leaves a
        // composer that is disabled forever, waiting for a run the daemon never started. Put the
        // original attachments back too: the display item only retains small previews, so this is
        // the last recoverable copy of the bytes the user selected.
        patch(key, { pending: sending, running: wasRunning })
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
    const dropped = Array.from(list || [])
    if (!dropped.length) return
    const { currentSessionKey: key, sessions, patch, append } = useApp.getState()
    const session = sessions[key]
    if (!session) return
    /* NOT AN IMAGE => THE LIBRARY, AND SAY SO. A chat attachment is something for the model to
       look at; a workflow JSON or a text file pasted here used to become a path nobody could
       open, and the agent asked for the contents to be pasted again. The file goes where the
       agent CAN read it — the shared Library, under Uploaded — and the thread says where it went
       and how to refer to it. Images keep the old door. */
    const others = dropped.filter((f) => !f.type.startsWith('image/'))
    if (others.length) {
      if (client) {
        try {
          const added = await uploadToLibrary(client, others)
          useApp.getState().bumpWorkspace()
          append(key, [
            {
              kind: 'system',
              tone: 'info',
              text:
                `${added.map((i) => `${i.name} (${i.kind})`).join(', ')} went to your Library — ` +
                `chat attachments are only looked at, and the agent reads files from the Library. ` +
                `Just mention ${added.length > 1 ? 'them' : 'it'} by name, or type @ to pick.`,
              ts: Date.now(),
            },
          ])
        } catch (e) {
          append(key, [
            {
              kind: 'system',
              tone: 'error',
              text: `Could not add ${others.map((f) => f.name).join(', ')} to the Library: ${String((e as Error)?.message || e)}`,
              ts: Date.now(),
            },
          ])
        }
      }
    }
    const all = dropped.filter((f) => f.type.startsWith('image/'))
    if (!all.length) return
    // THE HARD CAP ON A CHAT IMAGE, said in the thread rather than silently dropped: a paste that
    // vanishes reads as "the paste failed", and the one thing the person needs to hear is that
    // this is the wrong door for a big image — the reference button is the right one.
    const tooBig = all.filter((f) => f.size > MAX_CHAT_IMAGE_BYTES)
    if (tooBig.length) {
      const mb = (b: number) => `${(b / (1024 * 1024)).toFixed(1)} MB`
      append(key, [
        {
          kind: 'system',
          tone: 'error',
          text:
            tooBig.map((f) => `${f.name} (${mb(f.size)})`).join(', ') +
            ` ${tooBig.length > 1 ? 'are' : 'is'} over the ${mb(MAX_CHAT_IMAGE_BYTES)} chat limit. ` +
            'Chat images are only looked at by the agent — a generation input goes in the References panel on the left.',
          ts: Date.now(),
        },
      ])
    }
    const files = all.filter((f) => f.size <= MAX_CHAT_IMAGE_BYTES)
    if (!files.length) return
    // Apply the cap before reading or decoding. A large drag must not briefly hold every full
    // image and every decoded bitmap merely to discard most of them after Promise.all settles.
    const accepted = files.slice(0, Math.max(0, MAX_FILES - session.pending.length))
    if (!accepted.length) return
    const read = await Promise.all(accepted.map((file) => readFile(file)))
    patch(key, { pending: [...session.pending, ...read].slice(0, MAX_FILES) })
  }, [client])

  const removeFile = useCallback((index: number): void => {
    const { currentSessionKey: key, sessions, patch } = useApp.getState()
    const session = sessions[key]
    if (!session) return
    patch(key, { pending: session.pending.filter((_, i) => i !== index) })
  }, [])

  /** Put ONE reference file in the workspace — into a SLOT (named by its role) or under its own
   *  name. The SEPARATE path from chat attachments: those ride the message to the LLM as vision;
   *  a reference is workflow INPUT, written straight to `references/<chat>/` via
   *  `workspace.upload`, and the model only ever hears about it by role or name. The pixels go
   *  browser -> workspace -> ComfyUI (the run tool uploads them); a 10 MB reference never bloats
   *  a model call.
   *
   *  A SLOT HOLDS ONE FILE. The daemon dedupes names on collision ("garment (2).jpg") rather than
   *  overwriting, so the previous holder of the role — whatever its extension — is deleted first,
   *  by the names the caller read off the listing. */
  const addReference = useCallback(
    async (file: File, role: string | null, replacing: string[] = []): Promise<string> => {
      if (!client) throw new Error('not connected')
      const { currentSessionKey: existing, newSession, append } = useApp.getState()
      const key = existing || newSession(true)
      const dir = chatDirFor('references', key)
      const ext = (file.name.match(/\.[A-Za-z0-9]+$/) || [''])[0].toLowerCase()
      const name = role ? `${role}${ext}` : file.name
      const read = await readFile(file, false)
      try {
        for (const old of replacing) {
          await client.request('workspace.delete', { agentId: AGENT_ID, path: `${dir}/${old}` })
        }
        const res: any = await client.request('workspace.upload', {
          agentId: AGENT_ID,
          path: dir,
          name,
          dataBase64: read.dataBase64,
        })
        if (!res?.ok) throw new Error(String(res?.error || 'upload failed'))
        // The folder changed; the rail re-reads it (agentd/workspace-files.ts).
        useApp.getState().bumpWorkspace()
        const saved = String(res.name || name)
        if (!role) {
          // A file with no slot: the agent has to be told it exists, since nothing else names it.
          // Queued if a run is in flight; sent at once otherwise (see flushReferences).
          const path = `${dir}/${saved}`
          const now = useApp.getState().sessions[key]
          if (now?.running) {
            useApp.getState().patch(key, { pendingReferences: [...(now.pendingReferences || []), path] })
          } else {
            await send(referenceInstruction([path]), { origin: 'reference' })
          }
        }
        return saved
      } catch (e) {
        append(key, [
          {
            kind: 'system',
            tone: 'error',
            text: `Could not add ${file.name}: ${String((e as Error)?.message || e)}`,
            ts: Date.now(),
          },
        ])
        throw e
      }
    },
    [client, send],
  )

  /** Hand over the unslotted files `addReference` had to hold. Called by the window when a run ends —
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
    await send(referenceInstruction(queued), { origin: 'reference' })
  }, [send])

  /** Ask the agent to delete files the user ticked in the rail. Sends at once — the button
   *  is disabled while a run is going (App.tsx), so there is nothing to queue: a delete request
   *  landing mid-turn, between an emit and its run, is exactly the case worth refusing. */
  const requestDeletion = useCallback(
    async (paths: string[]): Promise<void> => {
      if (!paths.length) return
      await send(deletionRequest(paths))
    },
    [send],
  )

  return { send, abort, addFiles, removeFile, addReference, flushReferences, requestDeletion }
}
