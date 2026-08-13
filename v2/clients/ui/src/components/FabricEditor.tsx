import { useEffect, useRef, useState } from 'react'
import * as fabric from 'fabric'
import {
  MousePointer2, Pencil, Highlighter, Square, Circle, MoveUpRight, Type, Crop,
  Trash2, Undo2, Redo2, Save, Download, ZoomIn, ZoomOut, Maximize2, Check, X,
  MessageSquarePlus, ArrowUp,
} from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { platform } from '../lib/platform'
import { useCanvasHost } from '../canvas/host'

type Tool = 'select' | 'pen' | 'marker' | 'rect' | 'ellipse' | 'arrow' | 'text' | 'crop'
const PALETTE = ['#e5352b', '#f5a623', '#1fb854', '#2d7ff9', '#111111', '#ffffff']

/** How each file kind loads into / saves out of the shared fabric canvas. */
export interface EditorMode {
  /** populate the canvas from the file; return the working (logical) size. */
  load: (canvas: fabric.Canvas, artifact: Artifact) => Promise<{ width: number; height: number }>
  /** persist the canvas back (copy=Save As). `exporters.png()` -> PNG data URL,
   *  `exporters.svg()` -> SVG markup (both flattened at logical size). */
  save: (canvas: fabric.Canvas, copy: boolean, exporters: Exporters, artifact: Artifact) => Promise<{ ok?: boolean } | undefined>
  enableCrop: boolean
  /** double-click a loaded text element to retype it (svg) */
  editableText: boolean
}

interface Exporters { png: () => string; svg: () => string }

/** One fabric editor used by BOTH the SVG (vector) and raster viewers — same tools,
 *  undo/redo, zoom and save; only load/save differ (see EditorMode). Renders at device
 *  pixel ratio so vector text/lines stay crisp. */
export default function FabricEditor({ a, mode }: { a: Artifact; mode: EditorMode }): JSX.Element {
  const host = useCanvasHost()
  const wrapRef = useRef<HTMLDivElement>(null)
  const elRef = useRef<HTMLCanvasElement>(null)
  const fcRef = useRef<fabric.Canvas | null>(null)
  const sizeRef = useRef({ w: 800, h: 600 })
  const zoomRef = useRef(1)
  const toolRef = useRef<Tool>('select')
  const colorRef = useRef('#e5352b')
  const widthRef = useRef(4)
  const draw = useRef<{ obj: fabric.FabricObject; x: number; y: number } | null>(null)
  const cropRect = useRef<fabric.Rect | null>(null)
  const undo = useRef<string[]>([])
  const redo = useRef<string[]>([])
  const muted = useRef(false)

  const [tool, setTool] = useState<Tool>('select')
  const [color, setColor] = useState('#e5352b')
  const [width, setWidth] = useState(4)
  const [error, setError] = useState('')
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [cropping, setCropping] = useState(false)
  const [canUndo, setCanUndo] = useState(false)
  const [canRedo, setCanRedo] = useState(false)
  const [zoomPct, setZoomPct] = useState(100)
  const [hasEdits, setHasEdits] = useState(false)  // latches once any edit is made (drives the bottom send bar)
  // "send to chat": a PNG snapshot of the current canvas + a description, posted to the
  // active chat as a user message (the model sees the edited image inline)
  const [composing, setComposing] = useState(false)
  const [sendShot, setSendShot] = useState('')
  const [desc, setDesc] = useState('')
  const [sending, setSending] = useState(false)

  // ---- history ----
  function snapshot(): void {
    const c = fcRef.current
    if (!c || muted.current) return
    undo.current.push(JSON.stringify(c.toObject()))
    if (undo.current.length > 60) undo.current.shift()
    redo.current = []
    setCanUndo(undo.current.length > 1); setCanRedo(false); setDirty(true); setHasEdits(true)
  }
  async function restore(json: string): Promise<void> {
    const c = fcRef.current
    if (!c) return
    muted.current = true
    await c.loadFromJSON(json)
    c.requestRenderAll()
    muted.current = false
  }
  async function doUndo(): Promise<void> {
    if (undo.current.length < 2) return
    redo.current.push(undo.current.pop()!)
    await restore(undo.current[undo.current.length - 1])
    setCanUndo(undo.current.length > 1); setCanRedo(true)
  }
  async function doRedo(): Promise<void> {
    if (!redo.current.length) return
    const j = redo.current.pop()!
    undo.current.push(j)
    await restore(j)
    setCanUndo(true); setCanRedo(redo.current.length > 0)
  }

  // ---- zoom (device-pixel-ratio aware via fabric retina scaling) ----
  function applyZoom(z: number): void {
    const c = fcRef.current
    if (!c) return
    z = Math.max(0.1, Math.min(8, z))
    zoomRef.current = z
    c.setZoom(z)
    c.setDimensions({ width: sizeRef.current.w * z, height: sizeRef.current.h * z })
    setZoomPct(Math.round(z * 100))
  }
  function fit(): void {
    const wrap = wrapRef.current
    if (!wrap) return
    const z = Math.min((wrap.clientWidth - 28) / sizeRef.current.w, (wrap.clientHeight - 28) / sizeRef.current.h, 1)
    applyZoom(z > 0.02 ? z : 1)
  }
  function exportPng(region?: { left: number; top: number; width: number; height: number }): string {
    const c = fcRef.current!
    const { w, h } = sizeRef.current
    const vpt = c.viewportTransform.slice() as fabric.TMat2D
    const dw = c.getWidth(); const dh = c.getHeight()
    c.setViewportTransform([1, 0, 0, 1, 0, 0])
    c.setDimensions({ width: w, height: h })
    const url = c.toDataURL({ format: 'png', multiplier: 1, ...(region || {}) })
    c.setViewportTransform(vpt); c.setDimensions({ width: dw, height: dh })
    c.requestRenderAll()
    return url
  }
  // SVG export at logical size (zoom/pan reset) + a fix for fabric's own text round-trip:
  // fabric writes text with a centering tspan offset that its LOADER ignores, so a saved
  // SVG reopens with every label crept to the right (and it compounds each save). foldText
  // folds that offset back into the element transform — render-identical, but now fabric
  // re-reads the exact same position (see foldTextOffsets).
  function exportSvg(): string {
    const c = fcRef.current!
    const { w, h } = sizeRef.current
    const vpt = c.viewportTransform.slice() as fabric.TMat2D
    const dw = c.getWidth(); const dh = c.getHeight()
    c.setViewportTransform([1, 0, 0, 1, 0, 0])
    c.setDimensions({ width: w, height: h })
    const svg = c.toSVG()
    c.setViewportTransform(vpt); c.setDimensions({ width: dw, height: dh })
    c.requestRenderAll()
    return foldTextOffsets(svg)
  }

  // ---- init ----
  useEffect(() => {
    let disposed = false
    const el = elRef.current
    if (!el) return
    const c = new fabric.Canvas(el, { backgroundColor: '#ffffff', preserveObjectStacking: true, enableRetinaScaling: true })
    fcRef.current = c

    void mode.load(c, a).then(({ width, height }) => {
      if (disposed) return
      sizeRef.current = { w: Math.round(width) || 800, h: Math.round(height) || 600 }
      // vector crispness: fabric caches each object to a bitmap at its BASE size then
      // scales the cache to the on-canvas size — since the SVG's viewBox scale is baked
      // into every element's scaleX/scaleY, cached text/paths get stretched and blur.
      // Disabling per-object caching makes fabric re-rasterize the vectors directly at
      // the final (zoom × retina) scale, so glyphs and strokes stay sharp.
      c.getObjects().forEach(disableCacheDeep)
      c.requestRenderAll()
      fit()
      undo.current = [JSON.stringify(c.toObject())]; redo.current = []
      c.on('object:modified', snapshot)
      c.on('object:added', (e) => { if (e.target) disableCacheDeep(e.target); if (!muted.current) snapshot() })
      c.on('object:removed', () => { if (!muted.current) snapshot() })
      c.on('mouse:down', onDown)
      c.on('mouse:move', onMove)
      c.on('mouse:up', onUp)
      c.on('mouse:wheel', onWheel)
      c.on('path:created', () => snapshot())
      if (mode.editableText) c.on('mouse:dblclick', onDblClick)
    }).catch((e) => { if (!disposed) setError(String(e?.message || e)) })

    const onResize = (): void => fit()
    window.addEventListener('resize', onResize)
    return () => {
      disposed = true
      window.removeEventListener('resize', onResize)
      void c.dispose()
      fcRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a.path])

  // convert a loaded <text> to an editable IText IN PLACE (exact geometry) on double-click
  function onDblClick(opt: { target?: fabric.FabricObject }): void {
    const c = fcRef.current!
    const t = opt.target
    if (!t || t instanceof fabric.IText) return
    const type = (t as { type?: string }).type
    if (type !== 'text' && type !== 'textbox') return
    const src = t as unknown as { text?: string }
    const g = t as unknown as Record<string, unknown>
    const it = new fabric.IText(String(src.text ?? ''), {
      left: t.left, top: t.top, originX: t.originX, originY: t.originY, angle: t.angle,
      scaleX: t.scaleX, scaleY: t.scaleY, fill: g.fill as string,
      fontSize: g.fontSize as number, fontFamily: g.fontFamily as string,
      fontWeight: g.fontWeight as string, fontStyle: g.fontStyle as 'normal',
      textAlign: g.textAlign as 'left',
    })
    muted.current = true; c.remove(t); c.add(it); muted.current = false
    c.setActiveObject(it); it.enterEditing(); it.selectAll(); c.requestRenderAll()
  }

  // ---- wheel zoom (toward the cursor) ----
  function onWheel(opt: { e: WheelEvent }): void {
    const e = opt.e
    e.preventDefault()
    e.stopPropagation()
    applyZoom(zoomRef.current * (e.deltaY < 0 ? 1.1 : 1 / 1.1))
  }

  // ---- drawing ----
  function onDown(opt: { e: Event }): void {
    const c = fcRef.current!
    const t = toolRef.current
    if (t === 'select' || t === 'pen' || t === 'marker') return
    const p = c.getScenePoint(opt.e as any)
    const col = colorRef.current; const wdt = widthRef.current
    if (t === 'text') {
      const it = new fabric.IText('text', { left: p.x, top: p.y, fontSize: 6 * wdt + 6, fill: col })
      c.add(it); c.setActiveObject(it); it.enterEditing(); it.selectAll(); selectTool('select'); return
    }
    let obj: fabric.FabricObject
    if (t === 'rect' || t === 'crop') {
      obj = new fabric.Rect({ left: p.x, top: p.y, width: 1, height: 1, fill: 'rgba(0,0,0,0)', stroke: t === 'crop' ? '#2d7ff9' : col, strokeWidth: wdt, strokeDashArray: t === 'crop' ? [6, 4] : undefined, strokeUniform: true })
    } else if (t === 'ellipse') {
      obj = new fabric.Ellipse({ left: p.x, top: p.y, rx: 1, ry: 1, fill: 'rgba(0,0,0,0)', stroke: col, strokeWidth: wdt, strokeUniform: true })
    } else {
      obj = new fabric.Line([p.x, p.y, p.x, p.y], { stroke: col, strokeWidth: wdt, strokeUniform: true })
    }
    muted.current = true
    c.add(obj)
    draw.current = { obj, x: p.x, y: p.y }
  }
  function onMove(opt: { e: Event }): void {
    const c = fcRef.current!
    const d = draw.current
    if (!d) return
    const p = c.getScenePoint(opt.e as any)
    const o = d.obj
    if (o instanceof fabric.Line) o.set({ x2: p.x, y2: p.y })
    else if (o instanceof fabric.Ellipse) o.set({ rx: Math.abs(p.x - d.x) / 2, ry: Math.abs(p.y - d.y) / 2, left: Math.min(p.x, d.x), top: Math.min(p.y, d.y) })
    else o.set({ width: Math.abs(p.x - d.x), height: Math.abs(p.y - d.y), left: Math.min(p.x, d.x), top: Math.min(p.y, d.y) })
    o.setCoords()
    c.requestRenderAll()
  }
  function onUp(): void {
    const c = fcRef.current!
    const d = draw.current
    if (!d) return
    draw.current = null
    const o = d.obj
    const t = toolRef.current
    const tiny = (o instanceof fabric.Line)
      ? Math.hypot((o.x2 ?? 0) - (o.x1 ?? 0), (o.y2 ?? 0) - (o.y1 ?? 0)) < 4
      : ((o.width ?? 0) < 4 && (o.height ?? 0) < 4)
    if (tiny) { c.remove(o); muted.current = false; selectTool('select'); return }
    if (t === 'arrow' && o instanceof fabric.Line) {
      c.remove(o); muted.current = false
      const arrow = makeArrow(o.x1 ?? 0, o.y1 ?? 0, o.x2 ?? 0, o.y2 ?? 0, colorRef.current, widthRef.current)
      c.add(arrow); c.setActiveObject(arrow); selectTool('select'); return
    }
    if (t === 'crop') {
      muted.current = false; cropRect.current = o as fabric.Rect
      o.set({ selectable: true }); c.setActiveObject(o); setCropping(true); c.requestRenderAll(); return
    }
    muted.current = false
    o.set({ selectable: true, evented: true }); c.setActiveObject(o); snapshot(); selectTool('select')
  }

  // ---- crop (raster) ----
  function applyCrop(): void {
    const c = fcRef.current!
    const rct = cropRect.current
    if (!rct) return
    const left = Math.max(0, Math.round(rct.left ?? 0))
    const top = Math.max(0, Math.round(rct.top ?? 0))
    const width = Math.round((rct.width ?? 0) * (rct.scaleX ?? 1))
    const height = Math.round((rct.height ?? 0) * (rct.scaleY ?? 1))
    c.remove(rct); cropRect.current = null; setCropping(false)
    if (width < 4 || height < 4) return
    const url = exportPng({ left, top, width, height })
    void fabric.FabricImage.fromURL(url).then((img) => {
      muted.current = true
      c.getObjects().slice().forEach((o) => c.remove(o))
      img.set({ selectable: false, evented: false, left: 0, top: 0 })
      c.add(img)
      sizeRef.current = { w: width, h: height }
      muted.current = false
      fit(); snapshot()
    })
  }
  function cancelCrop(): void {
    const c = fcRef.current!
    if (cropRect.current) c.remove(cropRect.current)
    cropRect.current = null; setCropping(false); c.requestRenderAll()
  }

  // ---- tools ----
  function selectTool(t: Tool): void {
    setTool(t); toolRef.current = t
    const c = fcRef.current
    if (!c) return
    const freehand = t === 'pen' || t === 'marker'
    c.isDrawingMode = freehand
    c.selection = t === 'select'
    if (freehand) {
      const brush = new fabric.PencilBrush(c)
      brush.color = t === 'marker' ? hexToRgba(colorRef.current, 0.4) : colorRef.current
      brush.width = t === 'marker' ? widthRef.current * 4 : widthRef.current
      c.freeDrawingBrush = brush
    }
  }
  function pickColor(hex: string): void {
    setColor(hex); colorRef.current = hex
    const c = fcRef.current
    if (!c) return
    c.getActiveObjects().forEach((o) => o.set((o instanceof fabric.IText || o instanceof fabric.Group) ? 'fill' : 'stroke', hex))
    c.requestRenderAll()
    if (c.getActiveObjects().length) snapshot()
    if (c.isDrawingMode && c.freeDrawingBrush) c.freeDrawingBrush.color = toolRef.current === 'marker' ? hexToRgba(hex, 0.4) : hex
  }
  function pickWidth(w: number): void {
    setWidth(w); widthRef.current = w
    const c = fcRef.current
    if (c?.isDrawingMode && c.freeDrawingBrush) c.freeDrawingBrush.width = toolRef.current === 'marker' ? w * 4 : w
  }
  function del(): void {
    const c = fcRef.current
    if (!c) return
    c.getActiveObjects().forEach((o) => { if (o.selectable !== false) c.remove(o) })
    c.discardActiveObject(); c.requestRenderAll()
  }
  async function save(copy: boolean): Promise<void> {
    const c = fcRef.current
    if (!c) return
    setSaving(true)
    const res = await mode.save(c, copy, { png: () => exportPng(), svg: () => exportSvg() }, a)
    setSaving(false)
    if (res?.ok && !copy) setDirty(false)
  }

  // ---- send to chat (flatten current canvas -> PNG attachment on a user message) ----
  function openComposer(): void {
    if (!fcRef.current) return
    setSendShot(exportPng())   // thumbnail of exactly what will be sent
    setDesc('')
    setComposing(true)
  }
  async function sendToChat(): Promise<void> {
    if (sending || !fcRef.current) return
    setSending(true)
    try {
      const url = exportPng()                       // re-flatten so latest edits are included
      const b64 = url.split(',')[1] || ''
      const base = a.name.replace(/\.[^.]+$/, '') || 'image'
      await host.sendToChat(desc.trim(), [
        { name: `${base}-edited.png`, mimeType: 'image/png', dataBase64: b64 },
      ])
      setComposing(false); setDesc('')
    } finally {
      setSending(false)
    }
  }

  const tbtn = (t: Tool, icon: JSX.Element, title: string): JSX.Element => (
    <button className={`cv-btn ${tool === t ? 'on' : ''}`} title={title} onClick={() => selectTool(t)}>{icon}</button>
  )

  return (
    <div className="cv-svgedit">
      <div className="cv-toolbar cv-tools">
        {tbtn('select', <MousePointer2 size={15} />, 'select / move')}
        {tbtn('pen', <Pencil size={15} />, 'freehand pen')}
        {tbtn('marker', <Highlighter size={15} />, 'highlighter')}
        {tbtn('rect', <Square size={15} />, 'rectangle')}
        {tbtn('ellipse', <Circle size={15} />, 'ellipse')}
        {tbtn('arrow', <MoveUpRight size={15} />, 'arrow')}
        {tbtn('text', <Type size={15} />, 'text')}
        {mode.enableCrop && tbtn('crop', <Crop size={15} />, 'crop')}
        <span className="cv-sep" />
        {PALETTE.map((hex) => (
          <button key={hex} className={`cv-swatch ${color === hex ? 'on' : ''}`} style={{ background: hex }} title={hex} onClick={() => pickColor(hex)} />
        ))}
        <input className="cv-range" type="range" min={1} max={20} value={width} onChange={(e) => pickWidth(Number(e.target.value))} title="stroke width" />
        <button className="cv-btn" title="delete selection" onClick={del}><Trash2 size={15} /></button>
        <span className="cv-sep" />
        <button className="cv-btn" disabled={!canUndo} title="undo" onClick={() => void doUndo()}><Undo2 size={15} /></button>
        <button className="cv-btn" disabled={!canRedo} title="redo" onClick={() => void doRedo()}><Redo2 size={15} /></button>
        <span className="cv-sep" />
        <button className="cv-btn" title="zoom out" onClick={() => applyZoom(zoomRef.current / 1.2)}><ZoomOut size={15} /></button>
        <span className="cv-zoom">{zoomPct}%</span>
        <button className="cv-btn" title="zoom in" onClick={() => applyZoom(zoomRef.current * 1.2)}><ZoomIn size={15} /></button>
        <button className="cv-btn" title="fit" onClick={fit}><Maximize2 size={15} /></button>
        <span className="cv-spacer" />
        {cropping ? (
          <>
            <button className="cv-btn" title="apply crop" onClick={applyCrop}><Check size={14} /> Crop</button>
            <button className="cv-btn" title="cancel crop" onClick={cancelCrop}><X size={14} /></button>
          </>
        ) : (
          <>
            <button className="cv-btn" title={saving ? 'saving…' : dirty ? 'save changes' : 'saved'} disabled={!dirty || saving} onClick={() => void save(false)}><Save size={15} /></button>
            <button className="cv-btn" title="download a copy" onClick={() => void save(true)}><Download size={15} /></button>
          </>
        )}
      </div>
      {/* opt out of the app-wide soft-scroll fade: fading the artwork's edges when the
          zoomed canvas overflows would hide the true image edges/corners while editing */}
      <div className="cv-canvas-wrap" ref={wrapRef} data-no-soft-scroll>
        {error ? <div className="cv-empty">couldn’t load — {error}</div> : <canvas ref={elRef} />}
      </div>
      {composing ? (
        <div className="cv-send-bar">
          {sendShot && <img className="cv-send-thumb" src={sendShot} alt="preview" />}
          <div className="composer-box cv-send-box">
            <textarea
              className="cv-send-desc"
              placeholder="Describe the change…"
              value={desc}
              autoFocus
              spellCheck={false}
              rows={Math.min(8, Math.max(1, desc.split('\n').length))}
              onChange={(e) => setDesc(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void sendToChat() }
                else if (e.key === 'Escape') { setComposing(false); setDesc('') }
              }}
            />
            <button type="button" className="composer-attach" title="cancel" onClick={() => { setComposing(false); setDesc('') }}>
              <X size={18} />
            </button>
            <button type="button" className={`composer-send ${sending ? '' : 'ready'}`} title="send to chat" disabled={sending} onClick={() => void sendToChat()}>
              <ArrowUp size={18} />
            </button>
          </div>
        </div>
      ) : hasEdits ? (
        // an edit was made — offer to send it to the chat (accent-coloured, at the BOTTOM)
        <div className="cv-send-prompt">
          <span className="cv-send-hint">You’ve edited this image.</span>
          <button className="cv-btn accent" onClick={openComposer}>
            <MessageSquarePlus size={14} /> Send to chat
          </button>
        </div>
      ) : null}
    </div>
  )
}

/** Turn off fabric's bitmap object-cache (recursively, incl. group children) so scaled
 *  vector text/paths render crisp instead of stretching a low-res cached bitmap. */
function disableCacheDeep(o: fabric.FabricObject): void {
  o.objectCaching = false
  o.dirty = true
  const kids = (o as unknown as { _objects?: fabric.FabricObject[] })._objects
  if (kids) kids.forEach(disableCacheDeep)
}

function makeArrow(x1: number, y1: number, x2: number, y2: number, color: string, w: number): fabric.Group {
  const angle = Math.atan2(y2 - y1, x2 - x1)
  const head = 8 + w * 2
  const line = new fabric.Line([x1, y1, x2, y2], { stroke: color, strokeWidth: w, strokeUniform: true })
  const tri = new fabric.Triangle({ left: x2, top: y2, originX: 'center', originY: 'center', width: head, height: head, fill: color, angle: (angle * 180) / Math.PI + 90 })
  return new fabric.Group([line, tri])
}

/** Fix fabric's own SVG text round-trip. `toSVG()` centers a label by giving its <tspan>
 *  a negative x (and a baseline y) offset while translating the wrapping <g> to the anchor
 *  point — but fabric's LOADER reads only the <g> translate and ignores the tspan offset, so
 *  every label reopens shifted right (and it compounds each save). We fold the tspan x/y
 *  offset back into the <g> matrix and zero the tspan — mathematically the SAME render, but
 *  now fabric re-reads the exact position. Only single-line labels are folded (multi-tspan
 *  paragraphs are left untouched). */
function foldTextOffsets(svg: string): string {
  return svg.replace(
    /<g transform="matrix\(([^)]+)\)"([^>]*)>([\s\S]*?)<\/g>/g,
    (whole, mat: string, gRest: string, inner: string) => {
      if (!/<text/.test(inner)) return whole
      const tspans = inner.match(/<tspan\b[^>]*>/g) || []
      if (tspans.length !== 1) return whole // only single-line labels round-trip this way
      const ox = parseFloat((tspans[0].match(/\bx="(-?[\d.]+)"/) || [])[1] || '0')
      const oy = parseFloat((tspans[0].match(/\by="(-?[\d.]+)"/) || [])[1] || '0')
      if (!ox && !oy) return whole
      const n = mat.trim().split(/[\s,]+/).map(Number)
      if (n.length !== 6 || n.some((v) => Number.isNaN(v))) return whole
      const [a, b, c, d, e, f] = n
      const ne = e + a * ox + c * oy // fold the offset through the linear part of the matrix
      const nf = f + b * ox + d * oy
      const ni = inner
        .replace(/(<tspan\b[^>]*?)\bx="-?[\d.]+"/, '$1x="0"')
        .replace(/(<tspan\b[^>]*?)\by="-?[\d.]+"/, '$1y="0"')
      return `<g transform="matrix(${a} ${b} ${c} ${d} ${ne} ${nf})"${gRest}>${ni}</g>`
    }
  )
}

function hexToRgba(hex: string, alpha: number): string {
  const m = hex.replace('#', '')
  const n = m.length === 3 ? m.split('').map((x) => x + x).join('') : m
  return `rgba(${parseInt(n.slice(0, 2), 16)}, ${parseInt(n.slice(2, 4), 16)}, ${parseInt(n.slice(4, 6), 16)}, ${alpha})`
}

// ---------------------------------------------------------------- load/save modes

function mimeOf(name: string): string {
  const e = name.slice(name.lastIndexOf('.') + 1).toLowerCase()
  return e === 'jpg' || e === 'jpeg' ? 'image/jpeg' : e === 'gif' ? 'image/gif' : e === 'webp' ? 'image/webp' : e === 'bmp' ? 'image/bmp' : 'image/png'
}

/** SVG (vector): import objects with fabric's own viewBox transform so positions match the
 *  original exactly; keep text as-is (no lossy re-creation) — double-click retypes it. */
export const svgMode: EditorMode = {
  enableCrop: false,
  editableText: true,
  load: async (canvas, a) => {
    const res = await platform.readText(a.path)
    if (!res.ok || res.text == null) throw new Error(res.error || 'read failed')
    const parsed = await fabric.loadSVGFromString(res.text)
    const objs = (parsed.objects || []).filter(Boolean) as fabric.FabricObject[]
    const opts = (parsed.options || {}) as { width?: number; height?: number }
    // group applies the SVG's viewBox/transform correctly; ungroup (removeAll bakes the
    // group matrix into each child) so elements stay individually editable AND positioned right
    const grouped = fabric.util.groupSVGElements(objs, parsed.options as any)
    const children = (grouped instanceof fabric.Group) ? grouped.removeAll() : [grouped as fabric.FabricObject]
    for (const o of children) canvas.add(o)
    return { width: opts.width || (grouped as fabric.FabricObject).width || 800, height: opts.height || (grouped as fabric.FabricObject).height || 600 }
  },
  save: async (_canvas, copy, exporters, a) => {
    const svg = exporters.svg()  // logical-size toSVG + text round-trip fix (see foldTextOffsets)
    return copy ? platform.saveAs(a.name, svg, false) : platform.savePath(a.path, svg)
  },
}

/** Raster (png/jpg/…): the image is a locked background; annotate on top, crop, export PNG. */
export const rasterMode: EditorMode = {
  enableCrop: true,
  editableText: false,
  load: async (canvas, a) => {
    const res = await platform.readBytes(a.path)
    if (!res.ok || !res.base64) throw new Error(res.error || 'read failed')
    const img = await fabric.FabricImage.fromURL(`data:${mimeOf(a.name)};base64,${res.base64}`)
    img.set({ selectable: false, evented: false, left: 0, top: 0, originX: 'left', originY: 'top' })
    canvas.add(img)
    return { width: img.width || 800, height: img.height || 600 }
  },
  save: async (_canvas, copy, exporters, a) => {
    const b64 = exporters.png().split(',')[1] || ''
    return copy ? platform.saveAs(a.name, b64, true) : platform.savePath(a.path, b64, true)
  },
}
