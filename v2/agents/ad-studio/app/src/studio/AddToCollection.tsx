/* "Add to collection": the generations selected in this campaign go into a collection — an existing
 * one, or a new one named here — where the Posts tab makes Instagram posts from them. Free and
 * instant; the selection is cleared once they are in. */

import type { AgentdClient } from '@agentd/client'
import { FolderPlus, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import { addToCollection, listCollections, type Collection } from '../agentd/posts'
import { useApp } from '../state/store'

const NEW = '__new__'

export function AddToCollection({ client, campaign, paths }: { client: AgentdClient | null; campaign: string; paths: string[] }) {
  const clearSelection = useApp((s) => s.clearSelection)
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const [collections, setCollections] = useState<Collection[]>([])
  const [target, setTarget] = useState(NEW)
  const [name, setName] = useState('')
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [error, setError] = useState('')

  useEffect(() => {
    if (!client) return
    listCollections(client)
      .then((d) => {
        setCollections(d.collections)
        if (d.collections.length) setTarget((t) => (t === NEW ? d.collections[0].slug : t))
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  if (!paths.length) return null
  const add = async () => {
    if (!client) return
    setBusy(true)
    setError('')
    try {
      const isNew = target === NEW
      const c = await addToCollection(client, isNew ? name.trim() : target, isNew, paths.map((path) => ({ path, campaign })))
      clearSelection()
      setName('')
      setTarget(c.slug)
      setNote(`Added to “${c.name}” — ${c.items.length} items. Make a post from it in the Posts tab.`)
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="add-collection">
      <FolderPlus size={14} />
      <span>
        {paths.length} selected → add to
      </span>
      <select value={target} disabled={busy} onChange={(e) => setTarget(e.target.value)}>
        {collections.map((c) => (
          <option key={c.slug} value={c.slug}>
            {c.name} ({c.items.length})
          </option>
        ))}
        <option value={NEW}>A new collection…</option>
      </select>
      {target === NEW && (
        <input className="decision-input" value={name} disabled={busy} placeholder="Its name — e.g. Puja finds" onChange={(e) => setName(e.target.value)} />
      )}
      <button className="prime-btn" disabled={busy || !client || (target === NEW && !name.trim())} onClick={() => void add()}>
        {busy ? <Loader2 size={14} className="spin" /> : <FolderPlus size={14} />} Add
      </button>
      {note && !error && <span className="add-collection-note">{note}</span>}
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
