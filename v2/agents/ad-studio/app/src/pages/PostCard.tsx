/* One post on the Posts page: its cover, name, format, slides, how far it has got, the collection it
 * came from and when — click to open it (its chat and panel); download it once rendered. */

import { Download, Film } from 'lucide-react'

import type { Media } from '../agentd/campaigns'
import type { Post } from '../agentd/posts'
import { MediaTileActions } from '../studio/MediaTileActions'
import { viewerItem } from '../studio/ViewableMedia'

function stage(p: Post): string {
  if (p.rendered.length) return p.design === 'canva' ? 'Rendered · Canva' : 'Rendered'
  if (p.slides.some((s) => s.design)) return 'Designed'
  return 'Planned'
}

function cover(p: Post): string {
  const still = p.rendered.find((f) => !f.endsWith('.mp4'))
  return still || p.slides.find((s) => s.preview)?.preview || p.slides.find((s) => s.kind === 'image')?.item || ''
}

export function PostCard({ post, media, collection, onOpen }: { post: Post; media: Media; collection: string; onOpen: () => void }) {
  const pic = cover(post)
  // The full view steps through what it made — the rendered files, else the slides' designs.
  const files = post.rendered.length ? post.rendered : post.slides.map((s) => s.preview || s.item)
  const views = files.map((f, i) => viewerItem(`${media(f)}&v=${post.rendered_at}`, f, `${post.name} · ${String(i + 1).padStart(2, '0')}`))
  return (
    <article className="camp-card" role="button" tabIndex={0} onClick={onOpen} onKeyDown={(e) => e.key === 'Enter' && onOpen()} title="Open this post">
      <div className="camp-cover">
        {pic ? <img src={`${media(pic)}&v=${post.rendered_at}`} alt="" loading="lazy" /> : <div className="post-cover-empty"><Film size={22} /></div>}
        <span className="gate-badge">{stage(post)}</span>
        {views.length > 0 && <MediaTileActions item={{ path: files[0], kind: views[0].kind, campaign: '', shot: '', src: views[0].src }} title={views[0].title} selectable={false} set={views} />}
      </div>
      <div className="camp-body">
        <span className="camp-name">{post.name}</span>
        <span className="camp-sub">
          {post.format} · {post.slides.length} slides · {collection}
        </span>
        <span className="camp-sub">{post.rendered_at ? new Date(post.rendered_at * 1000).toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : 'not rendered yet'}</span>
        {post.rendered.length > 0 && (
          <a
            className="ref-add post-card-dl"
            href={media(`posts/${post.slug}/${post.slug}.zip`)}
            download={`${post.slug}.zip`}
            onClick={(e) => e.stopPropagation()}
          >
            <Download size={13} /> Download
          </a>
        )}
      </div>
    </article>
  )
}
