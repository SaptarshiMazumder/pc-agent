/* A step's results, newest first: every image or clip it made, with the checker's score as ADVICE
 * (a failed one is marked, never hidden or locked).
 *
 * Click a result to PICK it — instant, no agent turn: it is what the step carries forward (a video
 * step starts from its source step's pick). It is also selected, so a typed message is about it.
 * Hovering shows what else it can do: images — Fix, More like this; clips — Edit, Extend. Every
 * tile opens full screen. */

import { Clapperboard, FastForward, ImagePlus, Loader2, Type, Wand2 } from 'lucide-react'

import { isVideo, type CampaignStep, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { MediaTileActions } from './MediaTileActions'
import { viewerItem } from './ViewableMedia'

export type TileAction = 'fix' | 'like' | 'edit' | 'extend' | 'text'

const ACTION: Record<TileAction, { icon: JSX.Element; text: string; title: string }> = {
  fix: { icon: <Wand2 size={12} />, text: 'Fix', title: 'Change one thing in this image' },
  like: { icon: <ImagePlus size={12} />, text: 'More like this', title: 'Make more images like this one' },
  edit: { icon: <Clapperboard size={12} />, text: 'Edit', title: 'The same clip with one thing changed' },
  extend: { icon: <FastForward size={12} />, text: 'Extend', title: 'Continue this clip' },
  text: { icon: <Type size={12} />, text: 'Edit text', title: 'Change the words on this design' },
}

export function ResultGrid({
  campaign,
  step,
  media,
  picking,
  onPick,
  actions,
  active,
  onAction,
}: {
  campaign: string
  step: CampaignStep
  media: Media
  /** The result a pick is being saved for. */
  picking: string
  onPick: (path: string) => void
  /** What a tile offers beyond picking. */
  actions: TileAction[]
  /** The action box open right now, and on which result. */
  active: { action: TileAction; path: string } | null
  onAction: (action: TileAction, path: string) => void
}) {
  const selectOnly = useApp((s) => s.selectOnly)
  if (!step.results.length) return null
  return (
    <div className="tiles">
      {step.results.map((r, _, all) => {
        const video = r.kind === 'video' || isVideo(r.path)
        const picked = r.path === step.pick
        const item = { path: r.path, kind: video ? ('video' as const) : ('image' as const), campaign, shot: step.id, src: media(r.path) }
        const failed = r.passed === false
        const offered = actions.filter((a) => (video ? a === 'edit' || a === 'extend' : a === 'fix' || a === 'like' || a === 'text'))
        return (
          <div
            key={r.path}
            role="button"
            tabIndex={0}
            className={`tile-still${picked ? ' chosen' : ''}${failed ? ' failed' : ''}${active?.path === r.path ? ' acting' : ''}`}
            title={`${r.path.split('/').pop()} · ${r.provider}/${r.model}${r.problems.length ? '\nchecker: ' + r.problems.join('; ') : ''}\nClick to pick it`}
            onClick={() => {
              selectOnly(item)
              if (!picked) onPick(r.path)
            }}
          >
            {video ? (
              <video src={media(r.path)} muted loop playsInline preload="metadata" onMouseEnter={(e) => void e.currentTarget.play()} onMouseLeave={(e) => e.currentTarget.pause()} />
            ) : (
              <img src={media(r.path)} alt={r.path} loading="lazy" />
            )}
            {r.score !== null && (
              <span className={`score${failed ? ' bad' : ''}`} title="The checker's opinion — advice, you decide">
                {r.score}/10{failed ? ' · check' : ''}
              </span>
            )}
            {r.text_exact === false && (
              <span className="text-wrong" title={'A word on it is wrong — ' + r.problems.join('; ')}>
                text wrong
              </span>
            )}
            <MediaTileActions
              item={item}
              title={`${step.title} · ${r.path.split('/').pop()}`}
              set={all.map((x) => viewerItem(media(x.path), x.path, `${step.title} · ${x.path.split('/').pop()}`))}
            />
            {offered.length > 0 && (
              <span className="tile-acts">
                {offered.map((a) => (
                  <span
                    key={a}
                    role="button"
                    tabIndex={0}
                    className={`tile-act${active?.action === a && active.path === r.path ? ' on' : ''}`}
                    title={ACTION[a].title}
                    onClick={(e) => {
                      e.stopPropagation()
                      onAction(a, r.path)
                    }}
                  >
                    {ACTION[a].icon} {ACTION[a].text}
                  </span>
                ))}
              </span>
            )}
            {picking === r.path ? (
              <span className="chosen-tag">
                <Loader2 size={10} className="spin" /> picking
              </span>
            ) : picked ? (
              <span className="chosen-tag">Picked</span>
            ) : r.uploaded ? (
              <span className="tile-note">yours</span>
            ) : r.fix_of ? (
              <span className="tile-note">fix</span>
            ) : r.from_clip ? (
              <span className="tile-note">edit</span>
            ) : null}
          </div>
        )
      })}
    </div>
  )
}

