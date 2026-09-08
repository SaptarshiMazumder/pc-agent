/* One artifact, opened OVER something else — the overlay form of FileViewer.
 *
 * WHY BOTH STILL EXIST. The studio's centre pane shows the selected file in place, and that is
 * where reading a workflow belongs. But the render gallery and the per-turn artifact strip show
 * THUMBNAILS (`thumbnailUrl` — bounded, server-rendered, so the original is never fetched until
 * asked for), and clicking one has to reveal the full-size original without navigating away from
 * the grid you were scanning. That is a modal, and it is the click-through half of the thumbnail
 * rule: the small image is free, the big one is deliberate.
 *
 * THE RENDERING ITSELF IS NOT DUPLICATED. Everything about how a file is displayed — pretty-printed
 * JSON, inline media, the download fallback for binaries — lives in FileViewer, once. This adds a
 * backdrop, Escape-to-close, and nothing else.
 */

import { X } from 'lucide-react'
import { useEffect } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import { FileViewer } from './FileViewer'

export function FileModal({ file, onClose }: { file: Artifact; onClose: () => void }) {
  // Escape closes, wherever focus is — the reflex everyone already has.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [onClose])

  return (
    <div className="fm-backdrop" onClick={onClose}>
      {/* The panel swallows its own clicks so a click INSIDE never closes what you just opened. */}
      <div className="fm-panel" onClick={(e) => e.stopPropagation()} role="dialog" aria-modal="true">
        <button className="fm-close" onClick={onClose} title="Close (Esc)">
          <X size={16} strokeWidth={1.8} />
        </button>
        <FileViewer file={file} />
      </div>
    </div>
  )
}
