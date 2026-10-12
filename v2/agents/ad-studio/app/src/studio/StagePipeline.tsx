/* The campaign's stages as a pipeline — the recipe's order plus any stage the user added — each
 * card showing what it made and what it picked, so what one stage hands the next is on screen.
 *
 * EVERY STAGE IS A BUTTON, always: done, skipped or not started, clicking it opens that stage with
 * everything it can do. The current stage (the first one not done) is marked; "All generations"
 * shows everything the campaign made, across stages. */

import { Check, Clapperboard, FileText, FolderHeart, Grid3x3, Image as ImageIcon, Images, Minus } from 'lucide-react'

import { isVideo, type CampaignStep, type Media } from '../agentd/campaigns'
import { ExpandMedia } from './ExpandMedia'

const ICON: Record<CampaignStep['action'], JSX.Element> = {
  brief: <FileText size={16} />,
  sheet: <Grid3x3 size={16} />,
  product_sheet: <Grid3x3 size={16} />,
  images: <ImageIcon size={16} />,
  video: <Clapperboard size={16} />,
}

function status(s: CampaignStep): string {
  if (s.status === 'skipped') return 'Skipped'
  if (s.action === 'brief') return s.status === 'done' ? 'Written' : 'Not written yet'
  if (!s.results.length) return 'Not made yet'
  return `${s.results.length} made${s.pick ? ' · 1 picked' : ''}`
}

export function StagePipeline({
  steps,
  current,
  shown,
  all,
  media,
  onShow,
  onAll,
  collection,
  onCollection,
}: {
  steps: CampaignStep[]
  current: string
  /** The stage on screen ('' while All generations is). */
  shown: string
  /** All generations is on screen. */
  all: boolean
  media: Media
  onShow: (step: string) => void
  onAll: () => void
  /** The story's collection: how many it holds and whether it is on screen; null = no story. */
  collection: { count: number; on: boolean } | null
  onCollection: () => void
}) {
  // A video shot's first frame is part of its clip: it is opened from the clip's stage.
  const frames = new Set(steps.filter((s) => s.action === 'video' && s.source.endsWith('-frame')).map((s) => s.source))
  const shownHere = (s: CampaignStep) => !all && !collection?.on && (s.id === shown || (s.source === shown && frames.has(shown)))
  return (
    <nav className="pipeline" aria-label="Stages">
      <ol className="pipeline-stages">
        {steps.filter((s) => !frames.has(s.id)).map((s, i) => (
          <li
            key={s.id}
            className={`stage-card${s.status === 'done' ? ' past' : ''}${s.id === current || s.source === current ? ' now' : ''}${shownHere(s) ? ' shown' : ''}${s.status === 'skipped' ? ' skipped' : ''}`}
          >
            {i > 0 && (
              <span className="stage-arrow" aria-hidden="true">
                →
              </span>
            )}
            <button className="stage-btn" onClick={() => onShow(s.id)} aria-current={shownHere(s) ? 'step' : undefined}>
              <span className="stage-thumb has-expand">
                {s.pick ? isVideo(s.pick) ? <video src={media(s.pick)} muted playsInline preload="metadata" /> : <img src={media(s.pick)} alt="" /> : ICON[s.action]}
                {s.pick && <ExpandMedia src={media(s.pick)} path={s.pick} title={s.title} />}
              </span>
              <span className="stage-text">
                <span className="stage-name">
                  <span className="stage-mark">{s.status === 'done' ? <Check size={10} strokeWidth={3} /> : s.status === 'skipped' ? <Minus size={10} /> : i + 1}</span>
                  {s.title}
                </span>
                <span className={`stage-status${s.stale ? ' stale' : ''}`}>{s.stale ? 'Its pick is out of date' : status(s)}</span>
              </span>
            </button>
          </li>
        ))}
      </ol>
      <div className="stage-ends">
        {collection && (
          <button className={`stage-all${collection.on ? ' on' : ''}`} onClick={onCollection} aria-pressed={collection.on}>
            <FolderHeart size={15} />
            <span>Collection · {collection.count}</span>
          </button>
        )}
        <button className={`stage-all${all ? ' on' : ''}`} onClick={onAll} aria-pressed={all}>
          <Images size={15} />
          <span>All generations</span>
        </button>
      </div>
    </nav>
  )
}
