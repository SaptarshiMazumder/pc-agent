/* "Add your own files" on a collection: images and clips made elsewhere (Higgsfield's own app, a
 * phone), each batch under the product it shows. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Loader2, Upload } from 'lucide-react'
import { useRef, useState } from 'react'

import { importToCollection, type Collection } from '../agentd/posts'
import { useApp } from '../state/store'

export function CollectionFileImport({ client, collection }: { client: AgentdClient | null; collection: Collection }) {
  const bump = useApp((s) => s.bumpStudio)
  const input = useRef<HTMLInputElement>(null)
  const [product, setProduct] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const listId = `products-${collection.slug}`

  const add = async (files: File[]) => {
    if (!client || !files.length) return
    setBusy(true)
    setError('')
    try {
      await importToCollection(client, collection.slug, false, files, product.trim())
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
      if (input.current) input.current.value = ''
    }
  }

  return (
    <div className="collection-import">
      <input
        className="decision-input"
        list={listId}
        value={product}
        disabled={busy}
        placeholder="Which product they show — e.g. Serpenti silver bracelet"
        onChange={(e) => setProduct(e.target.value)}
      />
      <datalist id={listId}>
        {collection.products.map((p) => (
          <option key={p.name} value={p.name} />
        ))}
      </datalist>
      <input ref={input} type="file" multiple accept="image/png,image/jpeg,image/webp,video/mp4,video/quicktime,video/webm" hidden onChange={(e) => void add(Array.from(e.target.files || []))} />
      <button className="ref-add" disabled={busy || !client || !product.trim()} onClick={() => input.current?.click()} title={product.trim() ? 'Choose images and clips' : 'Name the product first'}>
        {busy ? <Loader2 size={13} className="spin" /> : <Upload size={13} />} Add your own files
      </button>
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
