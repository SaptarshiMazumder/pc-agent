/* One slide of a post: what it shows, the words on it, and — for a clip — its speed and fades.
 * Edits are kept in the panel until Save; nothing here renders. */

import { ArrowDown, ArrowUp, Palette, Trash2 } from 'lucide-react'
import { useState } from 'react'

import type { Slide, SlideDesignState, TextCue } from '../agentd/posts'
import type { ViewerItem } from '../state/store'
import { ViewableMedia } from './ViewableMedia'

const SPEEDS = [1, 1.1, 1.25, 1.5]

export function SlideEditor({
  n,
  slide,
  views,
  status,
  first,
  last,
  onChange,
  onMove,
  onRemove,
  onRedesign,
  busy,
}: {
  n: number
  slide: Slide
  /** Every slide of the post as the full view shows it — this one is number `n`. */
  views: ViewerItem[]
  /** Where this slide is in a design run that is moving now. */
  status?: SlideDesignState
  first: boolean
  last: boolean
  onChange: (slide: Slide) => void
  onMove: (by: -1 | 1) => void
  onRemove: () => void
  /** Ask the agent to design (or redesign) this slide, with the user's words. */
  onRedesign: (notes: string) => void
  busy: boolean
}) {
  const [notes, setNotes] = useState('')
  const cue = (i: number, patch: Partial<TextCue>) => onChange({ ...slide, cues: slide.cues.map((c, k) => (k === i ? { ...c, ...patch } : c)) })
  return (
    <div className="slide-row">
      <span className="slide-n">{String(n).padStart(2, '0')}</span>
      <div className="slide-thumb">
        <ViewableMedia item={views[n - 1]} set={views} />
        {status && (
          <span className={`slide-design-state s-${status.state}`} title={status.problems.length ? `Still to fix:\n${status.problems.join('\n')}` : ''}>
            {status.state === 'designing' || status.state === 'reviewing'
              ? `${status.state === 'reviewing' ? 'Reviewing' : 'Drawing'} ${status.round}/${status.rounds}`
              : status.state === 'done'
                ? `${status.problems.length} to check`
                : status.state}
          </span>
        )}
      </div>
      <div className="slide-body">
        <span className="strip-label">{slide.note || (slide.kind === 'video' ? 'clip' : 'still')}</span>
        {slide.cues.map((c, i) => (
          <div key={i} className="slide-cue">
            <input className="decision-input" value={c.text} onChange={(e) => cue(i, { text: e.target.value })} />
            <span className="slide-cue-time" title="When the words show, in seconds on this slide">
              {c.end ? `${c.start}–${c.end}s` : c.start ? `from ${c.start}s` : 'stays'}
              {c.to_y > 0 ? ' · moves up' : ''}
            </span>
          </div>
        ))}
        <div className="slide-cue">
          <input
            className="decision-input"
            value={notes}
            disabled={busy}
            placeholder={slide.design ? 'Redesign — e.g. cleaner, the price bigger, a collage' : 'Design this slide — anything you want'}
            onChange={(e) => setNotes(e.target.value)}
          />
          <button
            className="ref-add"
            disabled={busy}
            title={slide.design ? 'Redesign this slide' : 'Design this slide'}
            onClick={() => {
              onRedesign(notes)
              setNotes('')
            }}
          >
            <Palette size={12} /> {slide.design ? 'Redesign' : 'Design'}
          </button>
        </div>
        {slide.kind === 'video' && (
          <div className="slide-clip">
            <label>
              Speed{' '}
              <select value={slide.edit.speed} onChange={(e) => onChange({ ...slide, edit: { ...slide.edit, speed: Number(e.target.value) } })}>
                {[...new Set([...SPEEDS, slide.edit.speed])].sort().map((s) => (
                  <option key={s} value={s}>
                    {s}×
                  </option>
                ))}
              </select>
            </label>
            <label>
              Fade out{' '}
              <input
                type="number"
                min={0}
                max={2}
                step={0.1}
                value={slide.edit.fade_out}
                onChange={(e) => onChange({ ...slide, edit: { ...slide.edit, fade_out: Math.max(0, Number(e.target.value) || 0) } })}
              />
              s
            </label>
          </div>
        )}
      </div>
      <div className="slide-acts">
        <button className="ref-x" disabled={first} onClick={() => onMove(-1)} title="Earlier">
          <ArrowUp size={12} />
        </button>
        <button className="ref-x" disabled={last} onClick={() => onMove(1)} title="Later">
          <ArrowDown size={12} />
        </button>
        <button className="ref-x" onClick={onRemove} title="Take it out of the post">
          <Trash2 size={12} />
        </button>
      </div>
    </div>
  )
}
