/* Pick from the library — your uploads and every campaign's generations — for whatever you are doing:
 * product photos, references, a first frame, a stage's own result. It only CHOOSES; what a pick
 * means is the caller's (`onPick` gets the workspace paths, and the media function to show them). */

import type { AgentdClient } from '@agentd/client'
import { Check, Library, Loader2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listLibrary, type LibraryItem } from '../agentd/library'
import { ExpandMedia } from './ExpandMedia'

type Source = 'all' | 'upload' | 'campaign'

export function LibraryPicker({
  client,
  kind,
  multiple = true,
  title = 'Pick from your library',
  onPick,
  onClose,
}: {
  client: AgentdClient | null
  /** What can be picked. */
  kind: 'image' | 'video' | 'any'
  multiple?: boolean
  title?: string
  onPick: (paths: string[], media: Media) => void
  onClose: () => void
}) {
  const [data, setData] = useState<{ items: LibraryItem[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  const [source, setSource] = useState<Source>('all')
  const [chosen, setChosen] = useState<string[]>([])

  useEffect(() => {
    if (!client) return
    listLibrary(client)
      .then((d) => setData({ items: d.items, media: d.media }))
      .catch((e) => setError(String(e?.message || e)))
  }, [client])
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onClose])

  const items = useMemo(
    () =>
      (data?.items || []).filter((i) => (kind === 'any' || i.kind === kind) && (source === 'all' || i.source === source)).sort((a, b) => b.added - a.added),
    [data, kind, source],
  )
  const toggle = (path: string) =>
    setChosen((c) => (c.includes(path) ? c.filter((p) => p !== path) : multiple ? [...c, path] : [path]))

  return (
    <div className="lib-picker" role="dialog" aria-label={title} onClick={onClose}>
      <div className="lib-picker-body" onClick={(e) => e.stopPropagation()}>
        <header className="lib-picker-head">
          <span className="strip-label">
            <Library size={13} /> {title}
          </span>
          <div className="seg-switch small" role="radiogroup" aria-label="Which items">
            {(
              [
                ['all', 'All'],
                ['upload', 'Your uploads'],
                ['campaign', 'Generations'],
              ] as const
            ).map(([s, label]) => (
              <button key={s} role="radio" aria-checked={source === s} className={source === s ? 'on' : ''} onClick={() => setSource(s)}>
                {label}
              </button>
            ))}
          </div>
          <button className="ref-x" onClick={onClose} title="Close (Esc)">
            <X size={14} />
          </button>
        </header>

        {error && <div className="studio-error">Could not read the library: {error}</div>}
        {!data && !error ? (
          <div className="lib-picker-empty">
            <Loader2 size={16} className="spin" /> Loading…
          </div>
        ) : items.length === 0 ? (
          <div className="lib-picker-empty">
            Nothing here yet{kind !== 'any' ? ` — no ${kind === 'image' ? 'images' : 'clips'}` : ''}. Upload on the Library page.
          </div>
        ) : (
          <div className="lib-picker-grid">
            {items.map((i) => {
              const on = chosen.includes(i.path)
              return (
                <button key={i.path} className={`lib-picker-tile${on ? ' on' : ''}`} onClick={() => toggle(i.path)} title={i.product || i.path}>
                  {i.kind === 'video' ? <video src={data!.media(i.path)} muted preload="metadata" /> : <img src={data!.media(i.path)} alt="" loading="lazy" />}
                  <ExpandMedia src={data!.media(i.path)} path={i.path} title={i.product || undefined} />
                  {on && (
                    <span className="lib-picker-check">
                      <Check size={12} strokeWidth={3} />
                    </span>
                  )}
                  {i.source === 'upload' && <span className="tile-note">yours</span>}
                </button>
              )
            })}
          </div>
        )}

        <footer className="lib-picker-foot">
          <span className="page-note">{chosen.length ? `${chosen.length} chosen` : multiple ? 'Click to choose one or more' : 'Click to choose one'}</span>
          <button className="prime-btn" disabled={!chosen.length || !data} onClick={() => data && onPick(chosen, data.media)}>
            Use {chosen.length > 1 ? `these ${chosen.length}` : 'it'}
          </button>
        </footer>
      </div>
    </div>
  )
}
