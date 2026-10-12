/* One product, added or edited: its photos (the main one first, angles, worn or in use — uploaded or
 * picked from the library), title, kind, price and description. Free and instant. */

import type { AgentdClient } from '@agentd/client'
import { Library, Loader2, Trash2, Upload, X } from 'lucide-react'
import { useRef, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { uploadToLibrary } from '../agentd/library'
import { removeProduct, saveProduct, type ShopProduct } from '../agentd/products'
import { ExpandMedia } from '../studio/ExpandMedia'
import { LibraryPicker } from '../studio/LibraryPicker'

type Group = 'photos' | 'angles' | 'in_use'
const GROUPS: { key: Group; label: string; help: string }[] = [
  { key: 'photos', label: 'Product photos', help: 'The first is the main photo.' },
  { key: 'angles', label: 'Angles', help: 'Back, side, close-ups.' },
  { key: 'in_use', label: 'Worn / in use', help: 'On a person, styled.' },
]

export function ProductForm({
  client,
  product,
  media,
  onDone,
  onClose,
}: {
  client: AgentdClient | null
  /** null = a new product. */
  product: ShopProduct | null
  media: Media
  onDone: () => void
  onClose: () => void
}) {
  const [draft, setDraft] = useState<Partial<ShopProduct>>(
    product || { title: '', kind: 'wear', price: '', description: '', photos: [], angles: [], in_use: [] },
  )
  const [picking, setPicking] = useState<Group | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const pick = useRef<HTMLInputElement>(null)
  const [uploadTo, setUploadTo] = useState<Group>('photos')

  const add = (group: Group, paths: string[]) => setDraft((d) => ({ ...d, [group]: [...(d[group] || []), ...paths.filter((p) => !(d[group] || []).includes(p))] }))
  const drop = (group: Group, path: string) => setDraft((d) => ({ ...d, [group]: (d[group] || []).filter((p) => p !== path) }))
  const run = async (what: () => Promise<void>) => {
    setBusy(true)
    setError('')
    try {
      await what()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="product-form" role="dialog" aria-label={product ? `Edit ${product.title}` : 'New product'}>
      <header className="product-form-head">
        <h3>{product ? product.title : 'New product'}</h3>
        <button className="ref-x" onClick={onClose} title="Close">
          <X size={14} />
        </button>
      </header>

      {GROUPS.map((g) => (
        <section key={g.key} className="product-photos">
          <span className="strip-label">
            {g.label} <span className="page-note">— {g.help}</span>
          </span>
          <div className="product-photo-row">
            {(draft[g.key] || []).map((p) => (
              <span key={p} className="product-photo has-expand">
                <img src={media(p)} alt="" />
                <ExpandMedia src={media(p)} path={p} />
                <button className="ref-x product-photo-x" onClick={() => drop(g.key, p)} title="Remove">
                  <X size={10} />
                </button>
              </span>
            ))}
            <button
              className="ref-add"
              disabled={!client || busy}
              onClick={() => {
                setUploadTo(g.key)
                pick.current?.click()
              }}
            >
              <Upload size={11} /> Upload
            </button>
            <button className="ref-add" disabled={!client || busy} onClick={() => setPicking(g.key)}>
              <Library size={11} /> From library
            </button>
          </div>
        </section>
      ))}
      <input
        ref={pick}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple
        hidden
        onChange={(e) => {
          const files = Array.from(e.target.files || [])
          e.target.value = ''
          if (files.length && client) void run(async () => add(uploadTo, (await uploadToLibrary(client, files, draft.title || '')).map((i) => i.path)))
        }}
      />

      <div className="product-fields">
        <label className="step-choice">
          <span className="strip-label">Title</span>
          <input value={draft.title || ''} onChange={(e) => setDraft({ ...draft, title: e.target.value })} placeholder="Maroon Banarasi silk saree" />
        </label>
        <label className="step-choice">
          <span className="strip-label">Kind</span>
          <select value={draft.kind || 'wear'} onChange={(e) => setDraft({ ...draft, kind: e.target.value as ShopProduct['kind'] })}>
            <option value="wear">Ethnic wear</option>
            <option value="jewellery">Jewellery</option>
            <option value="other">Other</option>
          </select>
        </label>
        <label className="step-choice">
          <span className="strip-label">Price</span>
          <input value={draft.price || ''} onChange={(e) => setDraft({ ...draft, price: e.target.value })} placeholder="₹1,499 — as you'd write it" />
        </label>
      </div>
      <label className="step-choice">
        <span className="strip-label">Description</span>
        <textarea rows={3} value={draft.description || ''} onChange={(e) => setDraft({ ...draft, description: e.target.value })} placeholder="Fabric, work, what it's for — optional" />
      </label>

      {error && <div className="studio-error">{error}</div>}
      <footer className="product-form-foot">
        {product ? (
          <button
            className="ref-add"
            disabled={!client || busy}
            onClick={() => void run(async () => {
              await removeProduct(client!, product.key)
              onDone()
            })}
          >
            <Trash2 size={11} /> Delete
          </button>
        ) : (
          <span />
        )}
        <button
          className="prime-btn"
          disabled={!client || busy || !(draft.title || '').trim() || !(draft.photos || []).length}
          onClick={() => void run(async () => {
            const { added: _added, ...fields } = draft as ShopProduct
            await saveProduct(client!, product ? { ...fields, key: product.key } : fields)
            onDone()
          })}
          title={!(draft.photos || []).length ? 'Add at least one product photo' : ''}
        >
          {busy ? <Loader2 size={14} className="spin" /> : null} Save
        </button>
      </footer>

      {picking && (
        <LibraryPicker
          client={client}
          kind="image"
          title="Photos from your library"
          onPick={(paths) => {
            add(picking, paths)
            setPicking(null)
          }}
          onClose={() => setPicking(null)}
        />
      )}
    </div>
  )
}
