import { useEffect, useRef, useState } from 'react'
import { ZoomIn, ZoomOut, Maximize2, Pencil, Save, Eye, Code2, ExternalLink, Download, Wand2, Loader2 } from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { actionsFor } from '../lib/artifacts'
import { platform } from '../lib/platform'
import { readArtifactText, useCanvasHost } from '../canvas/host'
import { EDITABLE, ext, viewerKind } from '../lib/canvasFile'
import FabricEditor, { rasterMode, svgMode } from './FabricEditor'
import Markdown from './Markdown'

/** Self-declared tool action button(s) for this artifact (e.g. "Convert to Vector" on a PNG),
 *  rendered in a viewer toolbar beside View/Edit. Data-driven: shows only when an installed tool
 *  advertises an action for this artifact's mime; empty → nothing. */
function ActionTabs({ a }: { a: Artifact }): JSX.Element | null {
  const host = useCanvasHost()
  const runAction = host.runArtifactAction
  const busy = host.artifactActionBusy(a.path)
  const acts = actionsFor(host.artifactActions, a)
  if (!acts.length) return null
  return (
    <>
      {acts.map((ac) => (
        <button
          key={ac.tool}
          className="cv-tab"
          title={busy ? 'working…' : ac.label}
          disabled={busy}
          onClick={() => runAction(ac, a).catch((err) => console.error(`${ac.label} failed`, err))}
        >
          {busy ? <Loader2 size={14} className="spin" /> : <Wand2 size={14} />} {busy ? 'Working…' : ac.label}
        </button>
      ))}
    </>
  )
}

// ---------------------------------------------------------------- image (zoom / pan)
function ImageViewer({ a }: { a: Artifact }): JSX.Element {
  const host = useCanvasHost()
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
          src={host.fileUrl(a.path)}
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
        <ActionTabs a={a} />
      </div>
      <div className="cv-text-body">
        {mode === 'view' ? <ImageViewer a={a} key="v" /> : <FabricEditor a={a} mode={rasterMode} key="e" />}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------- pdf (native viewer)
function PdfViewer({ a }: { a: Artifact }): JSX.Element {
  const host = useCanvasHost()
  return <iframe className="cv-frame" src={host.fileUrl(a.path)} title={a.name} />
}

// ---------------------------------------------------------------- video / audio
function MediaViewer({ a }: { a: Artifact }): JSX.Element {
  const host = useCanvasHost()
  return (
    <div className="cv-media">
      {a.kind === 'audio'
        ? <audio src={host.fileUrl(a.path)} controls />
        : <video src={host.fileUrl(a.path)} controls autoPlay={false} />}
    </div>
  )
}

/** Split simple `--- key: value --- ` YAML frontmatter (as SKILL.md and many docs use) off the
 *  top of a markdown string. ReactMarkdown renders frontmatter as raw paragraph text, so the
 *  preview parses it out and shows it as a clean header instead. */
function parseFrontmatter(text: string): { meta: Record<string, string>; body: string } {
  const m = /^﻿?---[ \t]*\r?\n([\s\S]*?)\r?\n---[ \t]*\r?\n?/.exec(text)
  if (!m) return { meta: {}, body: text }
  const meta: Record<string, string> = {}
  for (const line of m[1].split(/\r?\n/)) {
    const i = line.indexOf(':')
    if (i > 0) {
      const key = line.slice(0, i).trim()
      let val = line.slice(i + 1).trim()
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1)
      }
      if (key) meta[key] = val
    }
  }
  return { meta, body: text.slice(m[0].length) }
}

/** Markdown preview: frontmatter renders as a clean, UNIFORM key/value block (no key is
 *  special-cased) above the body, instead of ReactMarkdown's run-on paragraph. */
function MarkdownPreview({ text }: { text: string }): JSX.Element {
  const { meta, body } = parseFrontmatter(text)
  const entries = Object.entries(meta)
  return (
    <div className="cv-md">
      {entries.length > 0 && (
        <div className="cv-frontmatter">
          {entries.map(([k, v]) => (
            <div className="cv-fm-row" key={k}>
              <span className="cv-fm-key">{k}</span>
              <span className="cv-fm-val">{v}</span>
            </div>
          ))}
        </div>
      )}
      <Markdown text={body} />
    </div>
  )
}

// ---------------------------------------------------------------- text / code / md / html / csv
function TextViewer({ a }: { a: Artifact }): JSX.Element {
  const host = useCanvasHost()
  const kind = viewerKind(a)
  const inline = a.text != null          // synthetic in-memory doc (no file) — read-only
  const editable = !inline && EDITABLE.has(kind)
  const canPreview = kind === 'markdown' || kind === 'html' || kind === 'csv'
  const [text, setText] = useState<string | null>(inline ? a.text! : null)
  const [error, setError] = useState('')
  const [mode, setMode] = useState<'preview' | 'source' | 'edit'>(canPreview ? 'preview' : 'source')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    if (inline) { setText(a.text!); setError(''); setDirty(false); return }
    let alive = true
    setText(null); setError(''); setDirty(false)
    // Through the HOST: the desktop shell reads over IPC (local file, no cross-origin fetch),
    // while the web shell and an agent's app window fetch from the daemon the host is bound to.
    readArtifactText(host, a.path).then((res: { ok: boolean; text?: string; error?: string }) => {
      if (!alive) return
      if (res.ok && res.text != null) setText(res.text)
      else setError(res.error || 'read failed')
    })
    return () => { alive = false }
  }, [a.path, inline, a.text])

  async function save(): Promise<void> {
    if (text == null) return
    setSaving(true)
    const res = await platform.savePath(a.path, text)
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
        {mode === 'preview' && kind === 'markdown' && <MarkdownPreview text={text} />}
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
        <button className="cv-btn wide" onClick={() => void platform.openPath(a.path)}>
          <ExternalLink size={15} /> Open in default app
        </button>
        <button className="cv-btn wide" onClick={() => void platform.downloadPath(a.path)}>
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
