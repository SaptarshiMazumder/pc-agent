/* Where each product in a collection was found — the store, the link, the price as you write it.
 * The post's caption credits only these; a product without a source is asked about. */

import type { AgentdClient } from '@agentd/client'
import { Save } from 'lucide-react'
import { useEffect, useState } from 'react'

import { updateCollection, type Collection, type ProductSource } from '../agentd/posts'
import { useApp } from '../state/store'

export function ProductSourcesEditor({ client, collection }: { client: AgentdClient | null; collection: Collection }) {
  const bump = useApp((s) => s.bumpStudio)
  const [rows, setRows] = useState<ProductSource[]>(collection.products)
  const [error, setError] = useState('')
  const key = JSON.stringify(collection.products)
  useEffect(() => setRows(collection.products), [key]) // eslint-disable-line react-hooks/exhaustive-deps
  if (!rows.length) return null

  const save = async (i: number) => {
    if (!client) return
    setError('')
    try {
      await updateCollection(client, collection.slug, 'product', { product: rows[i] })
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }
  const edit = (i: number, patch: Partial<ProductSource>) => setRows(rows.map((r, k) => (k === i ? { ...r, ...patch } : r)))
  const saved = (i: number) => JSON.stringify(rows[i]) === JSON.stringify(collection.products.find((p) => p.name === rows[i].name))

  return (
    <div className="product-sources">
      <span className="strip-label">Products and where they were found</span>
      {rows.map((p, i) => (
        <div key={p.name} className="product-source">
          <span className="product-source-name" title={p.name}>
            {p.name}
          </span>
          <input className="decision-input" value={p.found_at} placeholder="Store" onChange={(e) => edit(i, { found_at: e.target.value })} />
          <input className="decision-input" value={p.link} placeholder="Link" onChange={(e) => edit(i, { link: e.target.value })} />
          <input className="decision-input short" value={p.price} placeholder="Price (optional)" onChange={(e) => edit(i, { price: e.target.value })} />
          <button className="ref-x" disabled={saved(i) || !client} onClick={() => void save(i)} title="Save">
            <Save size={12} />
          </button>
        </div>
      ))}
      {error && <div className="studio-error">{error}</div>}
    </div>
  )
}
