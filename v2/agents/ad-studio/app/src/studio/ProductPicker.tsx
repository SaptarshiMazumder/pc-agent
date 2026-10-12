/* A new ad's products, from the shop's catalogue: select one or more, then "Use these" adds them to
 * the message. A product not set up yet is added right here (the same form as the Products page). */

import type { AgentdClient } from '@agentd/client'
import { Check, Plus } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listProducts, type ShopProduct } from '../agentd/products'
import { ProductForm } from '../pages/ProductForm'
import { useApp } from '../state/store'
import { ExpandMedia } from './ExpandMedia'

export function ProductPicker({
  client,
  chosen,
  onUse,
}: {
  client: AgentdClient | null
  chosen: string[]
  onUse: (products: ShopProduct[]) => void
}) {
  const tick = useApp((s) => s.studioTick)
  const bump = useApp((s) => s.bumpStudio)
  const [data, setData] = useState<{ products: ShopProduct[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  const [selected, setSelected] = useState<string[]>(chosen)
  const [adding, setAdding] = useState(false)

  useEffect(() => {
    if (!client) return
    listProducts(client)
      .then((d) => {
        setData(d)
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])

  const toggle = (key: string) => setSelected((s) => (s.includes(key) ? s.filter((k) => k !== key) : [...s, key]))
  const picked = (data?.products || []).filter((p) => selected.includes(p.key))

  return (
    <div className="product-picker">
      {error && <div className="studio-error">Could not load the products: {error}</div>}
      <div className="product-grid compact">
        <button className="product-card product-card-new" disabled={!client} onClick={() => setAdding(true)}>
          <Plus size={18} />
          <span>New product</span>
        </button>
        {(data?.products || []).map((p) => (
          <button key={p.key} className={`product-card${selected.includes(p.key) ? ' on' : ''}`} onClick={() => toggle(p.key)}>
            <span className="product-card-photo has-expand">
              {p.photos[0] && <img src={data!.media(p.photos[0])} alt="" />}
              {p.photos[0] && <ExpandMedia src={data!.media(p.photos[0])} path={p.photos[0]} title={p.title} />}
              {selected.includes(p.key) && (
                <span className="lib-picker-check">
                  <Check size={12} strokeWidth={3} />
                </span>
              )}
            </span>
            <b>{p.title}</b>
            <span className="product-card-meta">{p.price || 'no price'}</span>
          </button>
        ))}
      </div>
      {picked.length > 0 && (
        <button className="prime-btn" onClick={() => onUse(picked)}>
          Use {picked.length === 1 ? 'this product' : `these ${picked.length} products`}
        </button>
      )}
      {adding && data && (
        <div className="product-form-shade" onClick={() => setAdding(false)}>
          <div onClick={(e) => e.stopPropagation()}>
            <ProductForm client={client} product={null} media={data.media} onDone={() => { setAdding(false); bump() }} onClose={() => setAdding(false)} />
          </div>
        </div>
      )}
    </div>
  )
}
