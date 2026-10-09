/* An image or clip, full screen over the window. Fit to the screen, or at its actual size (scroll
 * to look around). Opened from a set (a post's slides, a collection), ‹ › and the arrow keys step
 * through it. Esc, the ✕ or a click on the backdrop closes it. Rendered once, in App; any tile
 * opens it through the store. */

import { ChevronLeft, ChevronRight, Maximize2, Minimize2, X } from 'lucide-react'
import { useEffect, useState } from 'react'

import { useApp } from '../state/store'

export function MediaViewer() {
  const open = useApp((s) => s.viewer)
  const step = useApp((s) => s.stepViewer)
  const close = useApp((s) => s.closeViewer)
  const viewer = open ? open.items[open.at] : null
  const many = (open?.items.length || 0) > 1
  const [actual, setActual] = useState(false)
  const [size, setSize] = useState('')

  useEffect(() => {
    setActual(false)
    setSize('')
  }, [viewer?.src])

  useEffect(() => {
    if (!viewer) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close()
      else if (e.key === 'ArrowLeft') step(-1)
      else if (e.key === 'ArrowRight') step(1)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [viewer, close, step])

  if (!viewer) return null
  return (
    <div className="viewer" role="dialog" aria-label={viewer.title} onClick={close}>
      <div className="viewer-bar" onClick={(e) => e.stopPropagation()}>
        <span className="viewer-title" title={viewer.title}>
          {viewer.title}
        </span>
        {many && (
          <span className="viewer-size">
            {open!.at + 1} / {open!.items.length}
          </span>
        )}
        {size && <span className="viewer-size">{size}</span>}
        <button className="viewer-btn" onClick={() => setActual((a) => !a)} title={actual ? 'Fit to the screen' : 'Actual size'}>
          {actual ? <Minimize2 size={15} /> : <Maximize2 size={15} />} {actual ? 'Fit' : 'Actual size'}
        </button>
        <button className="viewer-btn" onClick={close} title="Close (Esc)">
          <X size={16} />
        </button>
      </div>
      {many && (
        <>
          <button className="viewer-nav prev" onClick={(e) => (e.stopPropagation(), step(-1))} title="Previous (←)">
            <ChevronLeft size={26} />
          </button>
          <button className="viewer-nav next" onClick={(e) => (e.stopPropagation(), step(1))} title="Next (→)">
            <ChevronRight size={26} />
          </button>
        </>
      )}
      <div className={`viewer-stage${actual ? ' actual' : ''}`}>
        {viewer.kind === 'video' ? (
          <video
            src={viewer.src}
            controls
            autoPlay
            loop
            playsInline
            onClick={(e) => e.stopPropagation()}
            onLoadedMetadata={(e) => setSize(`${e.currentTarget.videoWidth}×${e.currentTarget.videoHeight}`)}
          />
        ) : (
          <img
            src={viewer.src}
            alt={viewer.title}
            onClick={(e) => e.stopPropagation()}
            onLoad={(e) => setSize(`${e.currentTarget.naturalWidth}×${e.currentTarget.naturalHeight}`)}
          />
        )}
      </div>
    </div>
  )
}
