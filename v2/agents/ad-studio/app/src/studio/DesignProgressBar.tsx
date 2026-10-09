/* A post's design run, as it moves: how many slides are being designed and which round each is on,
 * how many are done, when it started and when it last moved — and, when it has not moved for a
 * while, that it may have stopped. Shown at the top of the post panel. */

import { AlertTriangle, Loader2 } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { DesignProgress } from '../agentd/posts'

const STALE_S = 300 // no news for this long: the run may have died with the daemon

function ago(s: number): string {
  if (s < 60) return `${Math.max(0, Math.round(s))}s`
  return `${Math.floor(s / 60)} min`
}

export function DesignProgressBar({ progress }: { progress: DesignProgress }) {
  const [now, setNow] = useState(Date.now() / 1000)
  useEffect(() => {
    const t = window.setInterval(() => setNow(Date.now() / 1000), 1000)
    return () => window.clearInterval(t)
  }, [])
  const slides = Object.entries(progress.slides)
  const done = slides.filter(([, s]) => s.state === 'done').length
  const failed = slides.filter(([, s]) => s.state === 'failed').length
  const working = slides.filter(([, s]) => s.state === 'designing' || s.state === 'reviewing')
  const stale = now - progress.updated > STALE_S
  return (
    <div className={`design-progress${stale ? ' stale' : ''}`} role="status">
      {stale ? <AlertTriangle size={14} /> : <Loader2 size={14} className="spin" />}
      <span className="design-progress-main">
        Designing {slides.length} slide{slides.length > 1 ? 's' : ''} — {done} done{failed ? `, ${failed} failed` : ''}
        {working.length > 0 && ` · ${working.map(([n, s]) => `${n}: ${s.state === 'reviewing' ? 'reviewing' : 'drawing'} ${s.round}/${s.rounds}`).join(' · ')}`}
      </span>
      <span className="design-progress-time">
        {stale ? `no news for ${ago(now - progress.updated)} — it may have stopped` : `started ${ago(now - progress.started)} ago · model calls only, no generation`}
      </span>
    </div>
  )
}
