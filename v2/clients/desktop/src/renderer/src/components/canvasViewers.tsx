import { useEffect, useRef, useState } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { ZoomIn, ZoomOut, Maximize2, Pencil, Save, Eye, Code2, ExternalLink, Download } from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { fileUrl } from '../lib/artifacts'
import { EDITABLE, ext, viewerKind } from '../lib/canvasFile'
import FabricEditor, { rasterMode, svgMode } from './FabricEditor'

// ---------------------------------------------------------------- image (zoom / pan)
function ImageViewer({ a }: { a: Artifact }): JSX.Element {
  const [scale, setScale] = useState(1)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const drag = useRef<{ x: number; y: number; px: number; py: number } | null>(null)
  const zoom = (f: number): void => setScale((s) => Math.max(0.1, Math.min(16, s * f)))
  const reset = (): void => { setScale(1); setPan({ x: 0, y: 0 }) }
  return (
    <div className="cv-image">
      <div className="cv-toolbar">
        <button className="cv-btn" title="zoom out" onClick={() => zoom(1 / 1.2)}><ZoomOut size={15} /></button>
        <span className="cv-zoom">{Math.round(scale * 100)}%</span>
        <button className="cv-btn" title="zoom in" onClick={() => zoom(1.2)}><ZoomIn size={15} /></button>
        <button className="cv-btn" title="reset" onClick={reset}><Maximize2 size={15} /></button>
      </div>
      <div
        className="cv-stage"
        onWheel={(e) => zoom(e.deltaY < 0 ? 1.1 : 1 / 1.1)}
        onMouseDown={(e) => { drag.current = { x: e.clientX, y: e.clientY, px: pan.x, py: pan.y } }}
        onMouseMove={(e) => {
          if (!drag.current) return
          setPan({ x: drag.current.px + (e.clientX - drag.current.x), y: drag.current.py + (e.clientY - drag.current.y) })
        }}
        onMouseUp={() => { drag.current = null }}
        onMouseLeave={() => { drag.current = null }}
        style={{ cursor: scale > 1 ? 'grab' : 'default' }}
      >
        <img
          className="cv-img"
          src={fileUrl(a.path)}
          alt={a.name}
          draggable={false}
          style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${scale})` }}
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- svg (view + vector edit)
function SvgViewer({ a }: { a: Artifact }): JSX.Element {
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  return (
    <div className="cv-text">
      <div className="cv-toolbar">
        <button className={`cv-tab ${mode === 'view' ? 'on' : ''}`} onClick={() => setMode('view')}>
          <Eye size={14} /> View
        </button>
        <button className={`cv-tab ${mode === 'edit' ? 'on' : ''}`} onClick={() => setMode('edit')}>
          <Pencil size={14} /> Edit
        </button>
      </div>
      <div className="cv-text-body">
        {mode === 'view' ? <ImageViewer a={a} key="v" /> : <FabricEditor a={a} mode={svgMode} key="e" />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- raster image (view + annotate/crop)
function RasterViewer({ a }: { a: Artifact }): JSX.Element {
  const [mode, setMode] = useState<'view' | 'edit'>('view')
  return (
    <div className="cv-text">
      <div className="cv-toolbar">
        <button className={`cv-tab ${mode === 'view' ? 'on' : ''}`} onClick={() => setMode('view')}>
          <Eye size={14} /> View
        </button>
        <button className={`cv-tab ${mode === 'edit' ? 'on' : ''}`} onClick={() => setMode('edit')}>
          <Pencil size={14} /> Edit
        </button>
      </div>
      <div className="cv-text-body">
        {mode === 'view' ? <ImageViewer a={a} key="v" /> : <FabricEditor a={a} mode={rasterMode} key="e" />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- pdf (native viewer)
function PdfViewer({ a }: { a: Artifact }): JSX.Element {
  return <iframe className="cv-frame" src={fileUrl(a.path)} title={a.name} />
}

// ---------------------------------------------------------------- video / audio
function MediaViewer({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="cv-media">
      {a.kind === 'audio'
        ? <audio src={fileUrl(a.path)} controls />
        : <video src={fileUrl(a.path)} controls autoPlay={false} />}
    </div>
  )
}

// ---------------------------------------------------------------- text / code / md / html / csv
function TextViewer({ a }: { a: Artifact }): JSX.Element {
  const kind = viewerKind(a)
  const editable = EDITABLE.has(kind)
  const canPreview = kind === 'markdown' || kind === 'html' || kind === 'csv'
  const [text, setText] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'preview' | 'source' | 'edit'>(canPreview ? 'preview' : 'source')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let alive = true
    setText(null); setError(''); setDirty(false)
    // read via IPC (main's fs) — the file is local, and a cross-origin fetch would be blocked
    window.agentd.readText(a.path).then((res) => {
      if (!alive) return
      if (res.ok && res.text != null) setText(res.text)
      else setError(res.error || 'read failed')
    })
    return () => { alive = false }
  }, [a.path])

  async function save(): Promise<void> {
    if (text == null) return
    setSaving(true)
    const res = await window.agentd.savePath(a.path, text)
    setSaving(false)
    if (res?.ok) setDirty(false)
  }

  if (error) return <div className="cv-empty">couldn’t load this file — {error}</div>
  if (text == null) return <div className="cv-empty">loading…</div>

  return (
    <div className="cv-text">
      <div className="cv-toolbar">
        {canPreview && (
          <button className={`cv-tab ${mode === 'preview' ? 'on' : ''}`} onClick={() => setMode('preview')}>
            <Eye size={14} /> Preview
          </button>
        )}
        <button className={`cv-tab ${mode === 'source' ? 'on' : ''}`} onClick={() => setMode('source')}>
          <Code2 size={14} /> Source
        </button>
        {editable && (
          <button className={`cv-tab ${mode === 'edit' ? 'on' : ''}`} onClick={() => setMode('edit')}>
            <Pencil size={14} /> Edit
          </button>
        )}
        <span className="cv-spacer" />
        {editable && (
          <button className="cv-btn primary" disabled={!dirty || saving} onClick={() => void save()}>
            <Save size={14} /> {saving ? 'Saving…' : dirty ? 'Save' : 'Saved'}
          </button>
        )}
      </div>
      <div className="cv-text-body">
        {mode === 'preview' && kind === 'markdown' && (
          <div className="markdown cv-md"><ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown></div>
        )}
        {mode === 'preview' && kind === 'html' && (
          <iframe className="cv-frame" sandbox="allow-same-origin" srcDoc={text} title={a.name} />
        )}
        {mode === 'preview' && kind === 'csv' && <CsvTable text={text} />}
        {mode === 'source' && <pre className="cv-code">{text}</pre>}
        {mode === 'edit' && (
          <textarea
            className="cv-editor"
            value={text}
            spellCheck={false}
            onChange={(e) => { setText(e.target.value); setDirty(true) }}
          />
        )}
      </div>
    </div>
  )
}

function CsvTable({ text }: { text: string }): JSX.Element {
  const rows = parseCsv(text)
  const head = rows[0] || []
  const body = rows.slice(1)
  return (
    <div className="cv-csv">
      <table>
        <thead><tr>{head.map((h, i) => <th key={i}>{h}</th>)}</tr></thead>
        <tbody>
          {body.slice(0, 2000).map((r, ri) => (
            <tr key={ri}>{r.map((cell, ci) => <td key={ci}>{cell}</td>)}</tr>
          ))}
        </tbody>
      </table>
      {body.length > 2000 && <div className="cv-empty">…showing first 2000 rows</div>}
    </div>
  )
}

/** Minimal RFC-4180-ish CSV parse (handles quotes + embedded commas/newlines). */
function parseCsv(text: string): string[][] {
  const rows: string[][] = []
  let row: string[] = []
  let cur = ''
  let quoted = false
  for (let i = 0; i < text.length; i++) {
    const ch = text[i]
    if (quoted) {
      if (ch === '"') { if (text[i + 1] === '"') { cur += '"'; i++ } else quoted = false }
      else cur += ch
    } else if (ch === '"') quoted = true
    else if (ch === ',' || ch === '\t') { row.push(cur); cur = '' }
    else if (ch === '\n') { row.push(cur); rows.push(row); row = []; cur = '' }
    else if (ch !== '\r') cur += ch
  }
  if (cur.length || row.length) { row.push(cur); rows.push(row) }
  return rows
}

// ---------------------------------------------------------------- office / unknown
function Fallback({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="cv-empty">
      <p>No in-app preview for this file type yet.</p>
      <div className="cv-empty-actions">
        <button className="cv-btn wide" onClick={() => void window.agentd.openPath(a.path)}>
          <ExternalLink size={15} /> Open in default app
        </button>
        <button className="cv-btn wide" onClick={() => void window.agentd.downloadPath(a.path)}>
          <Download size={15} /> Download
        </button>
      </div>
    </div>
  )
}

/** Pick + render the viewer for an artifact. Add a case here to support a new type. */
export function CanvasBody({ a }: { a: Artifact }): JSX.Element {
  if (ext(a.name) === 'svg') return <SvgViewer a={a} /> // view + vector edit
  switch (viewerKind(a)) {
    case 'image': return <RasterViewer a={a} /> // view + annotate/crop
    case 'pdf': return <PdfViewer a={a} />
    case 'video':
    case 'audio': return <MediaViewer a={a} />
    case 'markdown':
    case 'html':
    case 'csv':
    case 'code': return <TextViewer a={a} />
    default: return <Fallback a={a} />
  }
}
