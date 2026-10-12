/* Products — the shop's catalogue, set up once and picked for any story: each product's photos,
 * title, kind and price. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listProducts, type ShopProduct } from '../agentd/products'
import { useApp } from '../state/store'
import { ExpandMedia } from '../studio/ExpandMedia'
import { ProductForm } from './ProductForm'

export function ProductsPage({ client }: { client: AgentdClient | null }) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const [data, setData] = useState<{ products: ShopProduct[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  const [editing, setEditing] = useState<ShopProduct | 'new' | null>(null)

  useEffect(() => {
    if (!client) return
    listProducts(client)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Products</h1>
        <p className="page-note">Set each product up once — its photos, title and price — then pick it for any story.</p>
      </header>
      {error && <div className="studio-error">Could not load the products: {error}</div>}

      <div className="product-grid">
        <button className="product-card product-card-new" disabled={!client} onClick={() => setEditing('new')}>
          <Plus size={20} />
          <span>New product</span>
        </button>
        {(data?.products || []).map((p) => (
          <button key={p.key} className="product-card" onClick={() => setEditing(p)} title="Edit">
            <span className="product-card-photo has-expand">
              {p.photos[0] && <img src={data!.media(p.photos[0])} alt="" />}
              {p.photos[0] && <ExpandMedia src={data!.media(p.photos[0])} path={p.photos[0]} title={p.title} />}
            </span>
            <b>{p.title}</b>
            <span className="product-card-meta">
              {p.kind === 'wear' ? 'Ethnic wear' : p.kind === 'jewellery' ? 'Jewellery' : 'Other'} · {p.price || 'no price'} · {p.photos.length + p.angles.length + p.in_use.length} photos
            </span>
          </button>
        ))}
      </div>

      {editing && data && (
        <div className="product-form-shade" onClick={() => setEditing(null)}>
          <div onClick={(e) => e.stopPropagation()}>
            <ProductForm
              client={client}
              product={editing === 'new' ? null : editing}
              media={data.media}
              onDone={() => {
                setEditing(null)
                bump()
              }}
              onClose={() => setEditing(null)}
            />
          </div>
        </div>
      )}
    </div>
  )
}
