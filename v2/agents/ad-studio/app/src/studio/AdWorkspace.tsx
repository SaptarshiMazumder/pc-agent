/* This ad's workspace: the files it starts from — uploaded here (they also go into your library) or
 * picked from the library. Select files and the box under them takes your words for them; Save puts
 * those words, with the files' paths, into the chat box — the agent reads them like anything you
 * type. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Library, Loader2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { isVideo, type Media } from '../agentd/campaigns'
import { uploadToLibrary } from '../agentd/library'
import { useApp, type WorkspaceFile } from '../state/store'
import { LibraryPicker } from './LibraryPicker'
import { viewerItem } from './ViewableMedia'
import { WorkspaceTagPopup } from './WorkspaceTagPopup'
import { ExpandMedia } from './ExpandMedia'

const NONE: WorkspaceFile[] = []

export function AdWorkspace({ client, session, media }: { client: AgentdClient | null; session: string; media: Media }) {
  const files = useApp((s) => s.starts[session]?.workspace) ?? NONE
  const setStart = useApp((s) => s.setStart)
  const appendComposer = useApp((s) => s.appendComposer)
  const pick = useRef<HTMLInputElement>(null)
  const [selected, setSelected] = useState<string[]>([])
  const [library, setLibrary] = useState(false)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [error, setError] = useState('')

  const save = (next: WorkspaceFile[]) => setStart(session, { workspace: next })
  // New files join untagged and selected, so the box asks what they are right away.
  const add = (paths: string[]) => {
    const fresh = paths.filter((p) => !files.some((f) => f.path === p))
    save([...files, ...fresh.map((path) => ({ path, tag: '' }))])
    setSelected(paths)
  }
  const upload = async (list: FileList | File[]) => {
    if (!client) return
    const chosen = Array.from(list).filter((f) => /\.(png|jpe?g|webp)$/i.test(f.name))
    if (!chosen.length) {
      setError('Images only for now (png, jpg, webp).')
      return
    }
    setBusy(true)
    setError('')
    try {
      add((await uploadToLibrary(client, chosen, '')).map((i) => i.path))
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }
  const toggle = (path: string) => setSelected((s) => (s.includes(path) ? s.filter((p) => p !== path) : [...s, path]))
  const chosenFiles = files.filter((f) => selected.includes(f.path))

  return (
    <div
      className={`ad-workspace${over ? ' over' : ''}`}
      onDragOver={(e) => {
        if (!Array.from(e.dataTransfer.types).includes('Files')) return
        e.preventDefault()
        e.stopPropagation()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return
        e.preventDefault()
        e.stopPropagation()
        setOver(false)
        void upload(e.dataTransfer.files)
      }}
    >
      {files.length === 0 ? (
        <div className="ad-workspace-empty">
          Drop images here, upload them, or pick them from your library — then say what they are: the product, stills to animate, a look to
          follow.
        </div>
      ) : (
        <div className="ad-workspace-grid">
          {files.map((f) => {
            const on = selected.includes(f.path)
            return (
              <button key={f.path} className={`ad-workspace-tile${on ? ' on' : ''}`} onClick={() => toggle(f.path)} title={f.path}>
                {isVideo(f.path) ? <video src={media(f.path)} muted preload="metadata" /> : <img src={media(f.path)} alt="" loading="lazy" />}
                <ExpandMedia src={media(f.path)} path={f.path} set={files.map((x) => viewerItem(media(x.path), x.path, x.path.split('/').pop()))} />
              </button>
            )
          })}
        </div>
      )}

      {/* Right under the images it describes. */}
      {chosenFiles.length > 0 && (
        <WorkspaceTagPopup
          files={chosenFiles}
          onTag={(words) => {
            if (words) appendComposer(`${words} (${chosenFiles.map((f) => f.path).join(', ')})`)
            setSelected([])
          }}
          onRemove={() => {
            save(files.filter((f) => !selected.includes(f.path)))
            setSelected([])
          }}
          onClose={() => setSelected([])}
        />
      )}

      <div className="new-ad-pick-row">
        <button className="ref-add" disabled={!client || busy} onClick={() => pick.current?.click()} title="Upload images — they also go into your library">
          {busy ? <Loader2 size={12} className="spin" /> : <Upload size={12} />} {busy ? 'Uploading…' : 'Upload'}
        </button>
        <button className="ref-add" disabled={!client || busy} onClick={() => setLibrary(true)} title="Images already in your library">
          <Library size={12} /> Pick from library
        </button>
        {files.length > 0 && (
          <button className="ref-add" onClick={() => setSelected(selected.length === files.length ? [] : files.map((f) => f.path))}>
            {selected.length === files.length ? 'Select none' : 'Select all'}
          </button>
        )}
      </div>
      <input
        ref={pick}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void upload(e.target.files)
          e.target.value = ''
        }}
      />
      {error && <div className="studio-error">{error}</div>}
      {over && <div className="upload-over">Drop to add to this ad's workspace</div>}

      {library && (
        <LibraryPicker
          client={client}
          kind="image"
          title="Add to this ad's workspace"
          onPick={(paths) => {
            setLibrary(false)
            add(paths)
          }}
          onClose={() => setLibrary(false)}
        />
      )}
    </div>
  )
}
