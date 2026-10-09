/* Posts — five tabs:
 *   Posts        every post made, as cards; click one to open it (its chat and the post panel)
 *   Collections  the pictures and clips gathered for posts; open one to edit it, or make a post
 *   References   pictures of designs worth following — the structure new designs take
 *   Templates    the design templates the agent starts from
 *   Brand        the name, sign-off line, voice, fonts and colours every post carries
 * "New post" opens a new chat that shows the collections to make a post from. */

import type { AgentdClient } from '@agentd/client'
import { Megaphone } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listCollections, listPosts, type Collection, type Post } from '../agentd/posts'
import { useApp } from '../state/store'
import { BrandForm } from './BrandForm'
import { CollectionCard } from './CollectionCard'
import { NewCollection } from './NewCollection'
import { PostCard } from './PostCard'
import { ReferenceLibrary } from './ReferenceLibrary'
import { TemplateGallery } from './TemplateGallery'

type Tab = 'posts' | 'collections' | 'references' | 'templates' | 'brand'
const TABS: { id: Tab; label: string }[] = [
  { id: 'posts', label: 'Posts' },
  { id: 'collections', label: 'Collections' },
  { id: 'references', label: 'References' },
  { id: 'templates', label: 'Templates' },
  { id: 'brand', label: 'Brand' },
]
const TAB_KEY = 'ad-studio.posts-tab'

function remembered(): Tab {
  try {
    const t = localStorage.getItem(TAB_KEY) as Tab | null
    return t && TABS.some((x) => x.id === t) ? t : 'posts'
  } catch {
    return 'posts'
  }
}

export function PostsPage({
  client,
  onPost,
  onNewPost,
  onOpenPost,
}: {
  client: AgentdClient | null
  onPost: (c: Collection) => void
  onNewPost: () => void
  onOpenPost: (p: Post) => void
}) {
  const tick = useApp((s) => s.studioTick)
  const [tab, setTabState] = useState<Tab>(remembered)
  const [data, setData] = useState<{ collections: Collection[]; posts: Post[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  const setTab = (t: Tab) => {
    setTabState(t)
    try {
      localStorage.setItem(TAB_KEY, t)
    } catch {
      /* a private window: the tab is simply not remembered */
    }
  }
  useEffect(() => {
    if (!client) return
    Promise.all([listCollections(client), listPosts(client)])
      .then(([c, p]) => {
        setData({ collections: c.collections, posts: p.posts, media: c.media })
        setError('')
      })
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])
  const names = Object.fromEntries((data?.collections || []).map((c) => [c.slug, c.name]))

  return (
    <div className="page">
      <header className="page-top">
        <span className="eyebrow-red">Studio</span>
        <h1>Posts</h1>
        <div className="page-actions">
          <button className="prime-btn" onClick={onNewPost}>
            <Megaphone size={14} /> New post
          </button>
        </div>
      </header>
      <nav className="studio-tabs posts-tabs" role="tablist">
        {TABS.map((t) => (
          <button key={t.id} role="tab" aria-selected={tab === t.id} className={`studio-tab${tab === t.id ? ' on' : ''}`} onClick={() => setTab(t.id)}>
            {t.label}
            {t.id === 'posts' && data ? <span className="tab-count">{data.posts.length}</span> : null}
            {t.id === 'collections' && data ? <span className="tab-count">{data.collections.length}</span> : null}
          </button>
        ))}
      </nav>
      {error && <div className="studio-error">{error}</div>}

      {tab === 'posts' && (
        <>
          <div className="grid-cards">
            {(data?.posts || []).map((p) => (
              <PostCard key={p.slug} post={p} media={data!.media} collection={names[p.collection] || p.collection} onOpen={() => onOpenPost(p)} />
            ))}
          </div>
          {data && !data.posts.length && (
            <div className="ref-none">
              No posts yet. Gather pictures in a collection (Collections tab, or a campaign's Generations → Add to collection), then press New post.
            </div>
          )}
        </>
      )}

      {tab === 'collections' && (
        <>
          <p className="page-note">
            Pictures and clips gathered for posts — from any campaign (its Generations tab → select → Add to collection) or your own files.
          </p>
          <div className="page-actions">
            <NewCollection client={client} />
          </div>
          <div className="collection-list">
            {(data?.collections || []).map((c) => (
              <CollectionCard key={c.slug} client={client} collection={c} media={data!.media} onPost={onPost} />
            ))}
            {data && !data.collections.length && <div className="ref-none">No collections yet.</div>}
          </div>
        </>
      )}

      {tab === 'references' && <ReferenceLibrary client={client} />}

      {tab === 'templates' && <TemplateGallery client={client} />}

      {tab === 'brand' && <BrandForm client={client} />}
    </div>
  )
}
