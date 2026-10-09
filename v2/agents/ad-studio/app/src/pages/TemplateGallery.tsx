/* The design templates on the Posts page: the built-in starters and the ones you saved from slides
 * you liked — what each suits, and its preview. The agent starts designs from them. */

import type { AgentdClient } from '@agentd/client'
import { useEffect, useState } from 'react'

import type { Media } from '../agentd/campaigns'
import { listTemplates, type DesignTemplate } from '../agentd/posts'
import { useApp } from '../state/store'
import { ViewableMedia, viewerItem } from '../studio/ViewableMedia'

export function TemplateGallery({ client }: { client: AgentdClient | null }) {
  const tick = useApp((s) => s.studioTick)
  const [data, setData] = useState<{ templates: DesignTemplate[]; media: Media } | null>(null)
  const [error, setError] = useState('')
  useEffect(() => {
    if (!client) return
    listTemplates(client)
      .then((d) => setData(d))
      .catch((e) => setError(String(e?.message || e)))
  }, [client, tick])
  const views = (data?.templates || []).map((t) => viewerItem(data!.media(t.thumb), t.thumb, `${t.name} — ${t.description}`))
  return (
    <section className="template-gallery">
      <span className="eyebrow-red">Design templates</span>
      <p className="page-note">Starting points the agent adapts to your pictures and words — or designs freely. Save a slide you love as your own.</p>
      {error && <div className="studio-error">{error}</div>}
      <div className="template-grid">
        {(data?.templates || []).map((t, i) => (
          <figure key={t.slug} className="template-card" title={t.description}>
            <ViewableMedia item={views[i]} set={views} />
            <figcaption>
              <span className="camp-name">{t.name}</span>
              <span className="collection-meta">
                {t.kind}
                {t.origin === 'saved' ? ' · yours' : ''}
              </span>
            </figcaption>
          </figure>
        ))}
      </div>
    </section>
  )
}
