/* "New collection" on the Posts page: a collection started from your own files (made elsewhere —
 * Higgsfield's app, a phone). Name it, say which product the files show, pick them; more products,
 * and where each was found, are added on its card. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { FolderPlus, Loader2, X } from 'lucide-react'
import { useRef, useState } from 'react'

import { importToCollection } from '../agentd/posts'
import { useApp } from '../state/store'

export function NewCollection({ client }: { client: AgentdClient | null }) {
  const bump = useApp((s) => s.bumpStudio)
  const input = useRef<HTMLInputElement>(null)
  const [open, setOpen] = useState(false)
  const [name, setName] = useState('')
  const [product, setProduct] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const ready = !!name.trim() && !!product.trim()

  const make = async (files: File[]) => {
    if (!client || !files.length) return
    setBusy(true)
    setError('')
    try {
      await importToCollection(client, name.trim(), true, files, product.trim())
      setOpen(false)
      setName('')
      setProduct('')
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  if (!open)
    return (
      <button className="ref-add" onClick={() => setOpen(true)} disabled={!client}>
        <FolderPlus size={13} /> New collection from your own files
      </button>
    )
  return (
    <div className="act-box new-collection">
      <div className="gen-panel-head">
        <span className="strip-label">A new collection from your own images and clips</span>
        <button className="ref-x" onClick={() => setOpen(false)} title="Close">
          <X size={11} />
        </button>
      </div>
      <div className="collection-import">
        <input className="decision-input" value={name} disabled={busy} placeholder="Its name — e.g. Bracelet finds" onChange={(e) => setName(e.target.value)} />
        <input className="decision-input" value={product} disabled={busy} placeholder="The product these files show — e.g. Serpenti silver bracelet" onChange={(e) => setProduct(e.target.value)} />
        <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm" hidden onChange={(e) => void make(Array.from(e.target.files || []))} />
        <button className="prime-btn" disabled={busy || !client || !ready} onClick={() => input.current?.click()} title={ready ? 'Choose the files' : 'Name the collection and the product first'}>
          {busy ? <Loader2 size={14} className="spin" /> : <FolderPlus size={14} />} Choose files
        </button>
      </div>
      <p className="start-note">Add the other products' files, and where each was found, on the collection's card.</p>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
