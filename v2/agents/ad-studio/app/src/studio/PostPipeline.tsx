/* A post's stages, as a pipeline: the collection it came from, its design, its words and caption,
 * and the render. Read-only — every stage is on the same screen below; this says where it stands. */

import { Check } from 'lucide-react'

import type { DesignProgress, Post, Slide } from '../agentd/posts'

interface Stage {
  name: string
  status: string
  done: boolean
  now: boolean
}

export function PostPipeline({ post, slides, progress, dirty }: { post: Post; slides: Slide[]; progress: DesignProgress | null; dirty: boolean }) {
  const designed = slides.filter((s) => s.design).length
  const rendered = post.rendered.length > 0
  const stages: Stage[] = [
    { name: 'Collection', status: `${post.collection} · ${slides.length} slides`, done: true, now: false },
    {
      name: 'Design',
      status: progress?.active ? 'Designing…' : post.design === 'canva' ? 'Designed in Canva' : `${designed} of ${slides.length} designed`,
      done: !progress?.active && (designed === slides.length || post.design === 'canva'),
      now: !!progress?.active,
    },
    { name: 'Words & caption', status: dirty ? 'Unsaved changes' : 'Edits are free', done: !dirty, now: dirty },
    { name: 'Render', status: rendered ? `${post.rendered.length} files ready` : 'Not rendered yet', done: rendered, now: false },
  ]
  return (
    <ol className="pipeline-stages post-pipeline" aria-label="Post stages">
      {stages.map((s, i) => (
        <li key={s.name} className={`stage-card static${s.done ? ' past' : ''}${s.now ? ' now shown' : ''}`}>
          {i > 0 && (
            <span className="stage-arrow" aria-hidden="true">
              →
            </span>
          )}
          <span className="stage-btn">
            <span className="stage-text">
              <span className="stage-name">
                <span className="stage-mark">{s.done ? <Check size={10} strokeWidth={3} /> : i + 1}</span>
                {s.name}
              </span>
              <span className="stage-status">{s.status}</span>
            </span>
          </span>
        </li>
      ))}
    </ol>
  )
}
