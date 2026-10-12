/* A result beside the real product — full screen, side by side — so the user can see with their own
 * eyes whether the border, the stones, the colour came out right. The accuracy check's findings sit
 * under it; a failed one offers "Redo with fixes" (the fix box, pre-filled with them). */

import { Wand2, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { isVideo, type Media, type StepResult } from '../agentd/campaigns'

export function CompareView({
  result,
  photos,
  media,
  onRedo,
  onClose,
}: {
  result: StepResult
  /** The product photos every run references. */
  photos: string[]
  media: Media
  /** Opens the fix box pre-filled with the check's findings; absent when there is nothing to fix. */
  onRedo?: () => void
  onClose: () => void
}) {
  const [at, setAt] = useState(0)
  useEffect(() => {
    const key = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', key)
    return () => window.removeEventListener('keydown', key)
  }, [onClose])
  const photo = photos[at] || ''
  const failed = result.passed === false

  return (
    <div className="compare-view" role="dialog" aria-label="Compare with the product" onClick={onClose}>
      <div className="compare-body" onClick={(e) => e.stopPropagation()}>
        <header className="compare-head">
          <span className="strip-label">Compare with the product</span>
          <button className="ref-x" onClick={onClose} title="Close (Esc)">
            <X size={14} />
          </button>
        </header>
        <div className="compare-panes">
          <figure className="compare-pane">
            <figcaption>The product — photo {at + 1} of {photos.length}</figcaption>
            {photo && <img src={media(photo)} alt="product photo" />}
            {photos.length > 1 && (
              <div className="compare-thumbs">
                {photos.map((p, i) => (
                  <button key={p} className={i === at ? 'on' : ''} onClick={() => setAt(i)} title={`Photo ${i + 1}`}>
                    <img src={media(p)} alt="" />
                  </button>
                ))}
              </div>
            )}
          </figure>
          <figure className="compare-pane">
            <figcaption>
              The result{result.score !== null ? ` — ${result.score}/10` : ''}
              {failed ? ' · missed the product' : result.passed ? ' · product exact' : ''}
            </figcaption>
            {isVideo(result.path) ? <video src={media(result.path)} controls autoPlay muted loop playsInline /> : <img src={media(result.path)} alt="result" />}
          </figure>
        </div>
        {result.problems.length > 0 && (
          <div className="compare-findings">
            <span className="strip-label">What the check found</span>
            <ul>
              {result.problems.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
            {onRedo && (
              <button className="prime-btn" onClick={onRedo}>
                <Wand2 size={14} /> Redo with fixes
              </button>
            )}
          </div>
        )}
      </div>
    </div>
  )
}
