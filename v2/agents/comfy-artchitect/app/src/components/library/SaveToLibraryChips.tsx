/* The chips under a finished run: "Save jacket-reel to Library", "Save 3 renders".
 *
 * THIS IS THE MAIN DOOR INTO THE LIBRARY. A run has just ended and the workflow and the renders
 * are right there in the rail — this is the moment a person decides something is worth keeping,
 * so the offer sits under the message that finished the job rather than on a tab nobody visits.
 *
 * WHAT IS OFFERED: every workflow role this chat holds (api + editor json, gathered by name) and
 * this chat's renders as one batch. WHAT IS NOT OFFERED AGAIN: anything the Library already
 * holds from this chat that is newer than the file — the catalogue is read once when the chips
 * mount, so a saved reel does not keep asking. A chip that was pressed reads "Saved" until the
 * files change under it.
 */

import { BookmarkPlus, Check } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import type { Artifact } from '../../agentd/artifacts'
import { readIndex, saveFromChat, type LibraryItem } from '../../agentd/library'
import { chatDirFor, relOfChatFile } from '../../agentd/workspace-files'
import { useApp } from '../../state/store'
import { collectWorkflows, workflowFiles, type Workflow } from '../workflows/WorkflowCard'

import './library.css'

const MEDIA = new Set(['image', 'video', 'audio'])

function inDir(a: Artifact, dir: string): boolean {
  return a.path.replace(/\\/g, '/').includes(`/${dir}/`)
}

/** A Library record from this chat that is at least as new as the file: already kept. */
function keptAlready(items: LibraryItem[], sessionKey: string, name: string, kind: 'workflow' | 'reference', modified?: number): boolean {
  return items.some((i) => {
    if (i.kind !== kind || i.origin !== 'saved' || i.from?.chat !== sessionKey) return false
    if (i.name !== name && i.name !== name.replace(/\.[^.]+$/, '')) return false
    if (!modified) return true
    const at = i.kind === 'workflow' ? i.versions[i.versions.length - 1]?.at : i.created
    return !at || Date.parse(at) / 1000 >= modified
  })
}

export function SaveToLibraryChips({
  client,
  files,
  sessionKey,
  chatTitle,
}: {
  client: AgentdClient | undefined
  /** This chat's merged file list (App.tsx). */
  files: Artifact[]
  sessionKey: string
  chatTitle: string
}) {
  const [kept, setKept] = useState<LibraryItem[] | null>(null)
  const [done, setDone] = useState<Set<string>>(() => new Set())
  const [busy, setBusy] = useState('')
  const [error, setError] = useState('')
  const bump = useApp((s) => s.bumpWorkspace)

  useEffect(() => {
    let alive = true
    if (!client) return
    readIndex(client)
      .then((idx) => alive && setKept(idx.items))
      .catch(() => alive && setKept([]))
    return () => {
      alive = false
    }
  }, [client, files.length])

  const workflows = useMemo(
    () => collectWorkflows(files.filter((a) => inDir(a, chatDirFor('workflows', sessionKey)))),
    [files, sessionKey],
  )
  const renders = useMemo(
    () => files.filter((a) => MEDIA.has(a.kind) && inDir(a, chatDirFor('outputs', sessionKey))),
    [files, sessionKey],
  )

  if (!client || kept === null) return null

  const toChatFiles = (list: Artifact[]) =>
    list
      .map((a) => ({ rel: relOfChatFile(a.path, sessionKey) || '', name: a.name, kind: a.kind, path: a.path }))
      .filter((f) => f.rel)

  const save = async (key: string, list: Artifact[]): Promise<void> => {
    setBusy(key)
    setError('')
    try {
      await saveFromChat(client, toChatFiles(list), { chat: sessionKey, title: chatTitle })
      setDone((prev) => new Set(prev).add(key))
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }

  const wfChips = workflows.filter(
    (wf: Workflow) => wf.api && !keptAlready(kept, sessionKey, wf.name, 'workflow', wf.api.modified),
  )
  const unsavedRenders = renders.filter((a) => !keptAlready(kept, sessionKey, a.name, 'reference', a.modified))

  if (!wfChips.length && !unsavedRenders.length) return null

  return (
    <div className="keep">
      <span className="keep-lead">Keep for later:</span>
      {wfChips.map((wf) => {
        const key = `wf:${wf.name}`
        const saved = done.has(key)
        return (
          <button
            key={key}
            type="button"
            className={`keep-chip${saved ? ' is-done' : ''}`}
            disabled={!!busy || saved}
            onClick={() => void save(key, workflowFiles(wf))}
            title="Copy this workflow into your Library, where every conversation can use it"
          >
            {saved ? <Check size={13} strokeWidth={2} /> : <BookmarkPlus size={13} strokeWidth={1.8} />}
            {saved ? `${wf.name} saved` : busy === key ? 'Saving…' : `Save ${wf.name} to Library`}
          </button>
        )
      })}
      {unsavedRenders.length > 0 && (
        <button
          type="button"
          className={`keep-chip${done.has('renders') ? ' is-done' : ''}`}
          disabled={!!busy || done.has('renders')}
          onClick={() => void save('renders', unsavedRenders)}
          title="Copy the renders into your Library as references"
        >
          {done.has('renders') ? <Check size={13} strokeWidth={2} /> : <BookmarkPlus size={13} strokeWidth={1.8} />}
          {done.has('renders')
            ? 'renders saved'
            : busy === 'renders'
              ? 'Saving…'
              : `Save ${unsavedRenders.length} render${unsavedRenders.length === 1 ? '' : 's'}`}
        </button>
      )}
      {error && <span className="keep-error">{error}</span>}
    </div>
  )
}
