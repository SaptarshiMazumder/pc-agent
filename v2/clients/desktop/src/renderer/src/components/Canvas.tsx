import { useEffect } from 'react'
import { X, ExternalLink, Download } from 'lucide-react'

import { kindLabel } from '../lib/canvasFile'
import { useApp } from '../state/store'
import { CanvasBody } from './canvasViewers'

/** The Claude-style right-side Canvas: a resizable panel that opens a produced file for
 *  rich in-app view/edit. The viewer is chosen per file type (see canvasViewers). */
export default function Canvas(): JSX.Element | null {
  const artifact = useApp((s) => s.canvas.artifact)
  const width = useApp((s) => s.canvas.width)
  const close = useApp((s) => s.closeCanvas)
  const setWidth = useApp((s) => s.setCanvasWidth)

  useEffect(() => {
    if (!artifact) return
    const onKey = (e: KeyboardEvent): void => { if (e.key === 'Escape') close() }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [artifact, close])

  if (!artifact) return null

  function startResize(e: React.MouseEvent): void {
    e.preventDefault()
    const move = (ev: MouseEvent): void => setWidth(window.innerWidth - ev.clientX)
    const up = (): void => {
      window.removeEventListener('mousemove', move)
      window.removeEventListener('mouseup', up)
      document.body.style.cursor = ''
      document.body.style.userSelect = ''
    }
    window.addEventListener('mousemove', move)
    window.addEventListener('mouseup', up)
    document.body.style.cursor = 'col-resize'
    document.body.style.userSelect = 'none'
  }

  return (
    <aside className="canvas" style={{ width }}>
      <div className="canvas-resize" onMouseDown={startResize} title="drag to resize" />
      <div className="canvas-head">
        <div className="canvas-title">
          <span className="canvas-name" title={artifact.path}>{artifact.name}</span>
          <span className="canvas-kind">{kindLabel(artifact)}</span>
        </div>
        <div className="canvas-head-actions">
          <button className="cv-btn" title="open in default app" onClick={() => void window.agentd.openPath(artifact.path)}>
            <ExternalLink size={15} />
          </button>
          <button className="cv-btn" title="download a copy" onClick={() => void window.agentd.downloadPath(artifact.path)}>
            <Download size={15} />
          </button>
          <button className="cv-btn" title="close (Esc)" onClick={close}><X size={16} /></button>
        </div>
      </div>
      <div className="canvas-body">
        {/* key by path so switching files fully remounts the viewer (fresh zoom/text) */}
        <CanvasBody key={artifact.path} a={artifact} />
      </div>
    </aside>
  )
}
