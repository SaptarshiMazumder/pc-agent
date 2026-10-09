/* One collection on the Posts page: a compact row (a mosaic of its first pictures, its name and
 * counts, "Make a post" — which opens a new chat with it chosen); "Edit" opens it: its items in order
 * (move, take out), adding your own files, its products and where they are from, rename, delete.
 * Changes are free and instant. */

import type { AgentdClient } from '@agentd/client'
import { ArrowLeft, ArrowRight, ChevronDown, ChevronUp, Megaphone, Pencil, Trash2, X } from 'lucide-react'
import { useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { updateCollection, type Collection } from '../agentd/posts'
import { useApp } from '../state/store'
import { ViewableMedia, viewerItem } from '../studio/ViewableMedia'
import { CollectionFileImport } from './CollectionFileImport'
import { ProductSourcesEditor } from './ProductSourcesEditor'

export function CollectionCard({ client, collection, media, onPost }: { client: AgentdClient | null; collection: Collection; media: Media; onPost: (c: Collection) => void }) {
  const bump = useApp((s) => s.bumpStudio)
  const [open, setOpen] = useState(false)
  const [renaming, setRenaming] = useState(false)
  const [name, setName] = useState(collection.name)
  const [error, setError] = useState('')
  const act = async (what: () => Promise<void>) => {
    if (!client) return
    setError('')
    try {
      await what()
      bump()
    } catch (e) {
      setError(String((e as Error)?.message || e))
    }
  }
  const paths = collection.items.map((i) => i.path)
  const views = collection.items.map((i) => viewerItem(media(i.path), i.path, `${collection.name} · ${i.product}`))
  const move = (i: number, by: -1 | 1) => {
    const next = [...paths]
    ;[next[i], next[i + by]] = [next[i + by], next[i]]
    void act(() => updateCollection(client!, collection.slug, 'reorder', { paths: next }))
  }

  return (
    <article className={`collection-panel${open ? ' open' : ''}`}>
      <header className="collection-head">
        <div className="collection-mosaic" onClick={() => setOpen(!open)}>
          {collection.items
            .slice(0, 4)
            .map((item) => (item.kind === 'video' ? <video key={item.path} src={media(item.path)} muted preload="metadata" /> : <img key={item.path} src={media(item.path)} alt="" loading="lazy" />))}
        </div>
        {renaming ? (
          <>
            <input className="decision-input" value={name} autoFocus onChange={(e) => setName(e.target.value)} />
            <button
              className="ref-add"
              disabled={!name.trim()}
              onClick={() =>
                void act(() =>
                  updateCollection(client!, collection.slug, 'rename', {
                    name,
                  }),
                ).then(() => setRenaming(false))
              }
            >
              Save
            </button>
          </>
        ) : (
          <>
            <span className="camp-name">{collection.name}</span>
            <span className="collection-meta">
              {collection.items.length} items · {collection.products.length} products
            </span>
            <button className="ref-x" onClick={() => setRenaming(true)} title="Rename">
              <Pencil size={11} />
            </button>
          </>
        )}
        <span className="grow" />
        <button
          className="ref-x"
          title="Delete the collection (its images and clips stay in their campaigns)"
          onClick={() => window.confirm(`Delete the collection “${collection.name}”?`) && void act(() => updateCollection(client!, collection.slug, 'delete'))}
        >
          <Trash2 size={12} />
        </button>
        <button className="ref-add" onClick={() => setOpen(!open)} title={open ? 'Close it' : 'Its items, your own files, its products'}>
          {open ? <ChevronUp size={13} /> : <ChevronDown size={13} />} {open ? 'Done' : 'Edit'}
        </button>
        <button className="prime-btn" disabled={!collection.items.length} onClick={() => onPost(collection)}>
          <Megaphone size={14} /> Make a post
        </button>
      </header>
      {open && (
        <>
          <div className="collection-items">
            {collection.items.map((item, i) => (
              <figure key={item.path} className="collection-item">
                <ViewableMedia item={views[i]} set={views} />
                <figcaption>
                  <span title={item.campaign || 'your own file'}>{item.product}</span>
                  <span className="collection-item-acts">
                    <button className="ref-x" disabled={i === 0} onClick={() => move(i, -1)} title="Earlier">
                      <ArrowLeft size={10} />
                    </button>
                    <button className="ref-x" disabled={i === paths.length - 1} onClick={() => move(i, 1)} title="Later">
                      <ArrowRight size={10} />
                    </button>
                    <button
                      className="ref-x"
                      onClick={() =>
                        void act(() =>
                          updateCollection(client!, collection.slug, 'remove', {
                            paths: [item.path],
                          }),
                        )
                      }
                      title="Take it out"
                    >
                      <X size={10} />
                    </button>
                  </span>
                </figcaption>
              </figure>
            ))}
            {!collection.items.length && <div className="ref-none">Empty — add images and clips from a campaign's Generations tab, or your own files below.</div>}
          </div>
          <CollectionFileImport client={client} collection={collection} />
          <ProductSourcesEditor client={client} collection={collection} />
        </>
      )}
      {error && <div className="studio-error">{error}</div>}
    </article>
  )
}
