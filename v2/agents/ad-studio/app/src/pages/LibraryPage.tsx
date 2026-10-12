/* Library — everything in one place: every campaign's stills and clips, and your own uploads.
 * Upload (the button, or drop files anywhere on the page) — no questions asked, a label is optional.
 * Click a tile to select it; the bar at the bottom puts the selection into a collection (an existing
 * one or a new one), and "Make a post" starts a post from it. The expand button opens one full
 * screen. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Check, Expand, FolderPlus, Loader2, Megaphone, Pencil, Trash2, Upload, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { libraryToCollection, listLibrary, removeFromLibrary, uploadToLibrary, type LibraryCampaign, type LibraryItem } from '../agentd/library'
import { listCollections, updateCollection, type Collection } from '../agentd/posts'
import { useApp } from '../state/store'
import { viewerItem } from '../studio/ViewableMedia'

type Filter = 'all' | 'generations' | 'uploads' | 'images' | 'clips'
const FILTERS: { id: Filter; label: string }[] = [
  { id: 'all', label: 'All' },
  { id: 'generations', label: 'Generations' },
  { id: 'uploads', label: 'Your uploads' },
  { id: 'images', label: 'Images' },
  { id: 'clips', label: 'Clips' },
]
const NEW = '__new__'

function when(ts: number): string {
  return ts ? new Date(ts * 1000).toLocaleString([], { month: 'short', day: 'numeric' }) : ''
}

export function LibraryPage({ client, onMakePost }: { client: AgentdClient | null; onMakePost: (c: Collection) => void }) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const openViewer = useApp((s) => s.openViewer)
  const input = useRef<HTMLInputElement>(null)
  const [data, setData] = useState<{ items: LibraryItem[]; campaigns: LibraryCampaign[]; media: Media } | null>(null)
  const [collections, setCollections] = useState<Collection[]>([])
  const [filter, setFilter] = useState<Filter>('all')
  const [campaign, setCampaign] = useState('')
  const [chosen, setChosen] = useState<string[]>([])
  const [label, setLabel] = useState('')
  const [target, setTarget] = useState(NEW)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState('')
  const [dragging, setDragging] = useState(false)
  const [added, setAdded] = useState<Collection | null>(null)
  const [error, setError] = useState('')
  // Renaming the collection picked in the bar: its new name while being edited ('' = not renaming).
  const [renaming, setRenaming] = useState('')
  // The bar's target defaults to the first collection ONCE — never again on a refresh, or a
  // "new collection" being named would snap back under the user's typing.
  const defaulted = useRef(false)

  useEffect(() => {
    if (!client) return
    Promise.all([listLibrary(client), listCollections(client)])
      .then(([l, c]) => {
        setData(l)
        setCollections(c.collections)
        if (!defaulted.current && c.collections.length) {
          defaulted.current = true
          setTarget((t) => (t === NEW ? c.collections[0].slug : t))
        }
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [client, tick])

  const names = useMemo(() => Object.fromEntries((data?.campaigns || []).map((c) => [c.id, c.name])), [data])
  const shown = useMemo(() => {
    let rows = data?.items || []
    if (campaign) rows = rows.filter((r) => r.campaign === campaign)
    if (filter === 'generations') rows = rows.filter((r) => r.source === 'campaign')
    if (filter === 'uploads') rows = rows.filter((r) => r.source === 'upload')
    if (filter === 'images') rows = rows.filter((r) => r.kind === 'image')
    if (filter === 'clips') rows = rows.filter((r) => r.kind === 'video')
    return rows
  }, [data, filter, campaign])
  const views = shown.map((r) => viewerItem(data!.media(r.path), r.path, r.product || r.path.split('/').pop() || r.path))

  const run = async (what: string, job: () => Promise<void>) => {
    if (!client) return
    setBusy(what)
    setError('')
    try {
      await job()
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy('')
    }
  }
  const upload = (files: File[]) =>
    files.length &&
    run(`Uploading ${files.length}…`, async () => {
      await uploadToLibrary(client!, files, label.trim())
      setFilter('uploads')
      if (input.current) input.current.value = ''
    })
  const toCollection = () =>
    run('Adding…', async () => {
      const isNew = target === NEW
      const c = await libraryToCollection(client!, isNew ? name.trim() : target, isNew, chosen)
      setAdded(c)
      setChosen([])
      setName('')
      setTarget(c.slug)
    })
  const toggle = (path: string) => setChosen((c) => (c.includes(path) ? c.filter((p) => p !== path) : [...c, path]))

  return (
    <div
      className={`page library-page${dragging ? ' dragging' : ''}`}
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={(e) => e.currentTarget === e.target && setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        void upload(Array.from(e.dataTransfer.files || []))
      }}
    >
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Library</h1>
        <p className="page-note">Everything your campaigns made, and your own files. Select any of them and put them in a collection — then make a post from it.</p>
      </header>

      <div className="collection-import library-upload">
        <input className="decision-input" value={label} disabled={!!busy} placeholder="What they show (optional) — e.g. Red Banarasi saree" onChange={(e) => setLabel(e.target.value)} />
        <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm" hidden onChange={(e) => void upload(Array.from(e.target.files || []))} />
        <button className="prime-btn" disabled={!!busy || !client} onClick={() => input.current?.click()}>
          {busy.startsWith('Uploading') ? <Loader2 size={14} className="spin" /> : <Upload size={14} />} {busy.startsWith('Uploading') ? busy : 'Upload your files'}
        </button>
        <span className="page-note">or drop them anywhere here</span>
      </div>

      <div className="gens-bar">
        <div className="gens-filters" role="radiogroup" aria-label="Show">
          {FILTERS.map((f) => (
            <button key={f.id} role="radio" aria-checked={filter === f.id} className={`filter-chip${filter === f.id ? ' on' : ''}`} onClick={() => setFilter(f.id)}>
              {f.label}
            </button>
          ))}
          <select className="library-campaign" value={campaign} onChange={(e) => setCampaign(e.target.value)}>
            <option value="">Every campaign</option>
            {(data?.campaigns || []).map((c) => (
              <option key={c.id} value={c.id}>
                {c.name}
              </option>
            ))}
          </select>
        </div>
        <span className="gens-total">{shown.length} shown</span>
      </div>
      {error && <div className="studio-error">{error}</div>}

      <div className="gens-grid library-grid">
        {shown.map((r, i) => {
          const on = chosen.includes(r.path)
          return (
            <figure key={r.path} className={`gen${on ? ' selected' : ''}`}>
              <div role="button" tabIndex={0} className="gen-media" title={on ? 'Selected — click to unselect' : 'Click to select'} onClick={() => toggle(r.path)}>
                {r.kind === 'video' ? (
                  <video src={data!.media(r.path)} muted loop playsInline preload="metadata" onMouseEnter={(e) => void e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} />
                ) : (
                  <img src={data!.media(r.path)} alt="" loading="lazy" />
                )}
                <span className={`tile-select${on ? ' on' : ''}`}>{on && <Check size={12} />}</span>
                <span
                  role="button"
                  tabIndex={0}
                  className="tile-expand"
                  title="Full view"
                  onClick={(e) => {
                    e.stopPropagation()
                    openViewer(views[i], views)
                  }}
                >
                  <Expand size={13} />
                </span>
              </div>
              <figcaption>
                <span className="camp-name">{r.product || (r.source === 'upload' ? 'Your upload' : '')}</span>
                <span className="collection-meta">
                  {r.source === 'upload' ? 'uploaded' : names[r.campaign] ? `from ${names[r.campaign]}` : r.campaign} · {r.kind === 'video' ? 'clip' : 'image'} · {when(r.added)}
                </span>
                {r.source === 'upload' && (
                  <button
                    className="ref-x library-remove"
                    title="Remove this upload from the library"
                    onClick={() => window.confirm('Remove this upload from the library?') && void run('Removing…', () => removeFromLibrary(client!, r.path))}
                  >
                    <Trash2 size={11} />
                  </button>
                )}
              </figcaption>
            </figure>
          )
        })}
        {data && !shown.length && <div className="ref-none">Nothing here yet — make something in an ad, or upload your own files.</div>}
      </div>

      {(chosen.length > 0 || added) && (
        <div className="library-bar">
          {chosen.length > 0 ? (
            <>
              <FolderPlus size={14} />
              <span>{chosen.length} selected → add to</span>
              {renaming ? (
                <>
                  <input className="decision-input" autoFocus value={renaming} onChange={(e) => setRenaming(e.target.value)} placeholder="The collection's name" />
                  <button
                    className="ref-add"
                    disabled={!renaming.trim() || !!busy}
                    onClick={() =>
                      client &&
                      void updateCollection(client, target, 'rename', { name: renaming.trim() })
                        .then(() => {
                          setRenaming('')
                          bump()
                        })
                        .catch((e) => setError(String((e as Error)?.message || e)))
                    }
                  >
                    <Check size={11} /> Save name
                  </button>
                  <button className="ref-x" title="Keep the name" onClick={() => setRenaming('')}>
                    <X size={11} />
                  </button>
                </>
              ) : (
                <>
                  <select value={target} onChange={(e) => setTarget(e.target.value)}>
                    {collections.map((c) => (
                      <option key={c.slug} value={c.slug}>
                        {c.name}
                      </option>
                    ))}
                    <option value={NEW}>A new collection…</option>
                  </select>
                  {target !== NEW && (
                    <button className="ref-x" title="Rename this collection" onClick={() => setRenaming(collections.find((c) => c.slug === target)?.name || '')}>
                      <Pencil size={11} />
                    </button>
                  )}
                </>
              )}
              {target === NEW && <input className="decision-input" autoFocus value={name} placeholder="Name the new collection" onChange={(e) => setName(e.target.value)} />}
              <button className="prime-btn" disabled={!!busy || (target === NEW && !name.trim())} onClick={() => void toCollection()}>
                {busy === 'Adding…' ? <Loader2 size={14} className="spin" /> : <FolderPlus size={14} />} Add
              </button>
              <button className="ref-x" title="Clear the selection" onClick={() => setChosen([])}>
                <X size={11} />
              </button>
            </>
          ) : (
            added && (
              <>
                <Check size={14} />
                <span>
                  In “{added.name}” — {added.items.length} items.
                </span>
                <button className="prime-btn" onClick={() => onMakePost(added)}>
                  <Megaphone size={14} /> Make a post
                </button>
                <button className="ref-x" title="Close" onClick={() => setAdded(null)}>
                  <X size={11} />
                </button>
              </>
            )
          )}
        </div>
      )}
    </div>
  )
}
