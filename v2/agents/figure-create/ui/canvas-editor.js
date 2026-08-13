/* canvas-editor.js — integrated canvas annotation editor for Figure Create.
 * Mounts directly into #canvasArea. Supports:
 *   pen, highlight, circle, rectangle, arrow, text, crop, eraser
 *   undo/redo, clear all, zoom in/out/fit, download, save to chat
 */

'use strict'

window.FigureCanvas = (() => {
  let state = null

  const PALETTE = [
    '#e53935', '#ffb300', '#43a047', '#1e88e5', '#212121', '#ffffff'
  ]

  let colour = '#8bc34a' // matches image
  let strokeW = 4
  let tool = 'pen'
  let drawing = false
  let startX = 0, startY = 0
  let strokes = []
  let undoStack = []
  
  // Viewport/Zoom state
  let zoom = 1.0
  let panX = 0, panY = 0
  let isPanning = false
  let panStartX = 0, panStartY = 0

  function buildToolbar() {
    const bar = document.createElement('div')
    bar.className = 'fc-topbar'
    bar.innerHTML = `
      <div class="fc-tools" id="fcTools">
        <button class="fc-tool" data-tool="select" title="Select/Pan (V)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"><path d="M4 14l3.5-3.5L11 14l1-10-10 7z"/></svg>
        </button>
        <button class="fc-tool fc-tool-active" data-tool="pen" title="Pen (P)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"><path d="M3 15L8 3l7 7-5 5H3z"/></svg>
        </button>
        <button class="fc-tool" data-tool="highlight" title="Highlighter (H)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none"><rect x="2" y="5" width="14" height="3" rx="1" fill="currentColor" opacity="0.35"/><rect x="2" y="5" width="14" height="3" rx="1" stroke="currentColor" stroke-width="1.5"/></svg>
        </button>
        <button class="fc-tool" data-tool="rect" title="Rectangle (R)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="4" width="12" height="10" rx="1"/></svg>
        </button>
        <button class="fc-tool" data-tool="circle" title="Circle (C)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="9" cy="9" r="6"/></svg>
        </button>
        <button class="fc-tool" data-tool="arrow" title="Arrow (A)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><line x1="3" y1="15" x2="15" y2="3"/><polyline points="9,3 15,3 15,9"/></svg>
        </button>
        <button class="fc-tool" data-tool="text" title="Text (T)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="currentColor"><path d="M4 5h10V3H4v2zm4 10h2V5H8v10z"/></svg>
        </button>
        <button class="fc-tool" data-tool="crop" title="Crop">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M4 14V4h10M14 4v10H4"/></svg>
        </button>
        
        <span class="fc-sep"></span>
        
        <div class="fc-color-pick" id="fcPalette">
          ${PALETTE.map(c => `<button class="fc-color-swatch ${c===colour?'active':''}" style="background-color: ${c}" data-color="${c}"></button>`).join('')}
        </div>
        
        <div class="fc-size-control">
          <input type="range" id="fcSizeSlider" min="1" max="20" value="4" title="Stroke width" />
        </div>

        <span class="fc-sep"></span>

        <button class="fc-tool" id="fcClear" title="Clear All">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6h12M7 6v8M11 6v8M5 6v9a1 1 0 001 1h6a1 1 0 001-1V6M8 3h2a1 1 0 011 1v2H7V4a1 1 0 011-1z"/></svg>
        </button>
        
        <span class="fc-sep"></span>
        
        <button class="fc-tool" id="fcUndo" title="Undo (Ctrl+Z)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 7l-3 3 3 3M3 10h8a4 4 0 000-8H9"/></svg>
        </button>
        <button class="fc-tool" id="fcRedo" title="Redo (Ctrl+Y)">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M12 7l3 3-3 3M15 10H7a4 4 0 010-8h2"/></svg>
        </button>

        <span class="fc-sep"></span>
        
        <button class="fc-tool" id="fcZoomOut" title="Zoom Out">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="5"/><line x1="12" y1="12" x2="16" y2="16"/><line x1="5" y1="8" x2="11" y2="8"/></svg>
        </button>
        <span id="fcZoomLevel" style="font-size: 12px; color: #ccc; min-width: 30px; text-align: center;">100%</span>
        <button class="fc-tool" id="fcZoomIn" title="Zoom In">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="8" cy="8" r="5"/><line x1="12" y1="12" x2="16" y2="16"/><line x1="5" y1="8" x2="11" y2="8"/><line x1="8" y1="5" x2="8" y2="11"/></svg>
        </button>
        <button class="fc-tool" id="fcZoomFit" title="Fit to Screen">
          <svg width="18" height="18" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 6V3h3M15 6V3h-3M3 12v3h3M15 12v3h-3"/></svg>
        </button>

      </div>
      
      <div class="fc-actions">
        <button class="fc-act" id="fcSave" title="Save & Send to Chat">
          <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 1H4a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V4l-2-3zM4 3h7v4H4V3zM14 15H4v-4h10v4z"/></svg>
        </button>
        <button class="fc-act" id="fcDownload" title="Download">
          <svg width="16" height="16" viewBox="0 0 18 18" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"><path d="M9 13V3M5 9l4 4 4-4M3 16h12"/></svg>
        </button>
      </div>
    `
    return bar
  }

  function setColour(c) {
    colour = c
    document.querySelectorAll('.fc-color-swatch').forEach(b => {
      b.classList.toggle('active', b.dataset.color === c)
    })
  }

  function setTool(t) {
    tool = t
    const label = document.getElementById('fcToolLabel')
    if (label) label.textContent = t.charAt(0).toUpperCase() + t.slice(1)
    document.querySelectorAll('.fc-tool[data-tool]').forEach(b => {
      b.classList.toggle('fc-tool-active', b.dataset.tool === t)
    })
  }

  function pushStroke(s) {
    strokes.push(s)
    undoStack = []
  }

  function undo() {
    if (!strokes.length) return
    undoStack.push(strokes.pop())
    redraw(state.canvas, state.img)
  }
  
  function redo() {
    if (!undoStack.length) return
    strokes.push(undoStack.pop())
    redraw(state.canvas, state.img)
  }

  function updateZoomLabel() {
    const el = document.getElementById('fcZoomLevel')
    if (el) el.textContent = Math.round(zoom * 100) + '%'
  }

  function fitToScreen(canvas, img, wrap) {
    if (!canvas || !img || !wrap) return
    const pad = 20
    const wRatio = (wrap.clientWidth - pad) / img.naturalWidth
    const hRatio = (wrap.clientHeight - pad) / img.naturalHeight
    zoom = Math.min(wRatio, hRatio, 1.0) // Don't scale up past 100% on fit
    panX = (wrap.clientWidth - (img.naturalWidth * zoom)) / 2
    panY = (wrap.clientHeight - (img.naturalHeight * zoom)) / 2
    updateZoomLabel()
    applyTransform(canvas)
  }
  
  function applyTransform(canvas) {
    if (!canvas) return
    canvas.style.transform = `translate(${panX}px, ${panY}px) scale(${zoom})`
    canvas.style.transformOrigin = '0 0'
  }

  function canvasCoords(canvas, ev) {
    const rect = canvas.getBoundingClientRect()
    // Convert screen coordinates to original image coordinates
    return {
      x: (ev.clientX - rect.left) / zoom,
      y: (ev.clientY - rect.top) / zoom,
    }
  }

  function redraw(c, img) {
    if (!c || !img) return
    const ctx = c.getContext('2d')
    ctx.clearRect(0, 0, c.width, c.height)
    ctx.drawImage(img, 0, 0, c.width, c.height)

    for (const s of strokes) {
      ctx.save()
      ctx.lineCap = 'round'
      ctx.lineJoin = 'round'

      if (s.tool === 'eraser') {
        ctx.globalCompositeOperation = 'destination-out'
        ctx.lineWidth = s.width * 2.5
        if (s.pts && s.pts.length > 1) {
          ctx.beginPath(); ctx.moveTo(s.pts[0].x, s.pts[0].y)
          for (let i = 1; i < s.pts.length; i++) ctx.lineTo(s.pts[i].x, s.pts[i].y)
          ctx.stroke()
        }
      } else if (s.tool === 'highlight') {
        ctx.globalAlpha = 0.35
        ctx.strokeStyle = s.colour
        ctx.lineWidth = s.width * 4
        if (s.pts && s.pts.length > 1) {
          ctx.beginPath(); ctx.moveTo(s.pts[0].x, s.pts[0].y)
          for (let i = 1; i < s.pts.length; i++) ctx.lineTo(s.pts[i].x, s.pts[i].y)
          ctx.stroke()
        }
      } else if (s.tool === 'rect') {
        ctx.strokeStyle = s.colour
        ctx.lineWidth = s.width
        ctx.strokeRect(s.x, s.y, s.w, s.h)
      } else if ((s.tool === 'circle' || s.tool === 'arrow') && s.w !== undefined) {
        ctx.strokeStyle = s.colour
        ctx.lineWidth = s.width
        if (s.tool === 'circle') {
          ctx.beginPath()
          ctx.ellipse(s.x + s.w / 2, s.y + s.h / 2, Math.abs(s.w) / 2, Math.abs(s.h) / 2, 0, 0, Math.PI * 2)
          ctx.stroke()
        } else {
          const dx = s.w, dy = s.h, len = Math.sqrt(dx * dx + dy * dy) || 1
          const ux = dx / len, uy = dy / len
          const headLen = Math.min(16 + s.width * 2, len * 0.6)
          ctx.beginPath()
          ctx.moveTo(s.x, s.y)
          ctx.lineTo(s.x + dx - ux * headLen, s.y + dy - uy * headLen)
          ctx.stroke()
          ctx.fillStyle = s.colour
          ctx.beginPath()
          ctx.moveTo(s.x + dx, s.y + dy)
          ctx.lineTo(s.x + dx - ux * headLen + uy * headLen * 0.4, s.y + dy - uy * headLen - ux * headLen * 0.4)
          ctx.lineTo(s.x + dx - ux * headLen - uy * headLen * 0.4, s.y + dy - uy * headLen + ux * headLen * 0.4)
          ctx.closePath()
          ctx.fill()
        }
      } else if (s.tool === 'text') {
        ctx.fillStyle = s.colour
        ctx.font = `${Math.max(12, s.width * 4)}px sans-serif`
        ctx.fillText(s.text, s.x, s.y)
      } else if (s.pts && s.pts.length > 1) {
        ctx.strokeStyle = s.colour
        ctx.lineWidth = s.width
        ctx.beginPath(); ctx.moveTo(s.pts[0].x, s.pts[0].y)
        for (let i = 1; i < s.pts.length; i++) ctx.lineTo(s.pts[i].x, s.pts[i].y)
        ctx.stroke()
      }
      ctx.restore()
    }
  }

  function init(areaEl) {
    areaEl.innerHTML = ''
    
    const toolbar = buildToolbar()
    areaEl.appendChild(toolbar)

    const wrap = document.createElement('div')
    wrap.className = 'fc-canvas-wrap'
    wrap.id = 'fcCanvasWrap'
    
    const canvas = document.createElement('canvas')
    canvas.id = 'fcCanvas'
    wrap.appendChild(canvas)
    
    const status = document.createElement('div')
    status.className = 'fc-status'
    status.innerHTML = `<span id="fcToolLabel">Pen</span><span id="fcPos"></span>`

    areaEl.appendChild(wrap)
    areaEl.appendChild(status)

    state = { areaEl, wrap, canvas, img: null }

    // Event listeners
    toolbar.addEventListener('click', ev => {
      const btn = ev.target.closest('button')
      if (!btn) return
      
      if (btn.classList.contains('fc-tool') && btn.dataset.tool) {
        setTool(btn.dataset.tool)
      } else if (btn.classList.contains('fc-color-swatch')) {
        setColour(btn.dataset.color)
      } else if (btn.id === 'fcUndo') { undo() }
      else if (btn.id === 'fcRedo') { redo() }
      else if (btn.id === 'fcClear') { strokes.length = 0; undoStack.length = 0; redraw(canvas, state.img) }
      else if (btn.id === 'fcZoomIn') { zoom *= 1.2; updateZoomLabel(); applyTransform(canvas) }
      else if (btn.id === 'fcZoomOut') { zoom /= 1.2; updateZoomLabel(); applyTransform(canvas) }
      else if (btn.id === 'fcZoomFit') { fitToScreen(canvas, state.img, wrap) }
      else if (btn.id === 'fcSave') {
        canvas.toBlob(blob => {
          if (state.onSave) state.onSave(blob)
        }, 'image/png')
      }
      else if (btn.id === 'fcDownload') {
        const a = document.createElement('a')
        a.download = 'annotated-figure.png'
        a.href = canvas.toDataURL()
        a.click()
      }
    })

    const sizeSlider = document.getElementById('fcSizeSlider')
    if (sizeSlider) sizeSlider.addEventListener('input', () => { strokeW = parseInt(sizeSlider.value) || 4 })

    wireEvents(canvas)
  }

  function loadFile(fileInfo, { onSave } = {}) {
    if (!state) return
    state.onSave = onSave
    
    strokes.length = 0
    undoStack.length = 0

    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      state.img = img
      state.canvas.width = img.naturalWidth
      state.canvas.height = img.naturalHeight
      fitToScreen(state.canvas, img, state.wrap)
      redraw(state.canvas, img)
    }
    img.src = fileInfo.url
  }

  function wireEvents(canvas) {
    let curStroke = null

    const onDown = (ev) => {
      if (!state || !state.img) return
      
      // Allow right click to pan
      if (ev.button === 2) {
        ev.preventDefault()
        isPanning = true
        panStartX = ev.clientX - panX
        panStartY = ev.clientY - panY
        canvas.style.cursor = 'grabbing'
        return
      }

      if (tool === 'select') {
        isPanning = true
        panStartX = ev.clientX - panX
        panStartY = ev.clientY - panY
        canvas.style.cursor = 'grabbing'
        return
      }
      
      ev.preventDefault()
      
      if (tool === 'text') {
        const pos = canvasCoords(canvas, ev)
        const text = prompt('Enter text:')
        if (text) pushStroke({ tool: 'text', colour, width: strokeW, x: pos.x, y: pos.y, text })
        redraw(canvas, state.img)
        return
      }

      drawing = true
      const pos = canvasCoords(canvas, ev)
      startX = pos.x; startY = pos.y
      curStroke = null

      if (tool === 'pen' || tool === 'highlight' || tool === 'eraser') {
        curStroke = { tool, colour, width: strokeW, pts: [{ x: pos.x, y: pos.y }] }
      }
    }

    const onMove = (ev) => {
      if (!state || !state.img) return
      
      if (isPanning && tool === 'select') {
        panX = ev.clientX - panStartX
        panY = ev.clientY - panStartY
        applyTransform(canvas)
        return
      }

      if (!drawing) return
      ev.preventDefault()
      const pos = canvasCoords(canvas, ev)

      if (tool === 'pen' || tool === 'highlight' || tool === 'eraser') {
        if (curStroke) curStroke.pts.push({ x: pos.x, y: pos.y })
      }

      redraw(canvas, state.img)
      drawPreview(canvas, curStroke, pos)

      const posEl = document.getElementById('fcPos')
      if (posEl) posEl.textContent = `${Math.round(pos.x)}, ${Math.round(pos.y)}`
    }

    const onUp = (ev) => {
      if (!state || !state.img) return
      if (isPanning) {
        isPanning = false
        canvas.style.cursor = 'crosshair'
        return
      }

      if (!drawing) return
      drawing = false
      const pos = canvasCoords(canvas, ev)

      if ((tool === 'pen' || tool === 'highlight' || tool === 'eraser') && curStroke && curStroke.pts.length > 0) {
        if (curStroke.pts.length === 1) curStroke.pts.push({ x: curStroke.pts[0].x + 0.5, y: curStroke.pts[0].y + 0.5 })
        pushStroke(curStroke)
      } else if (tool === 'rect' || tool === 'circle' || tool === 'arrow') {
        if (Math.abs(pos.x - startX) > 2 || Math.abs(pos.y - startY) > 2) {
           pushStroke({ tool, colour, width: strokeW, x: startX, y: startY, w: pos.x - startX, h: pos.y - startY })
        }
      }
      curStroke = null
      redraw(canvas, state.img)
    }
    
    // Zoom on wheel
    state.wrap.addEventListener('wheel', ev => {
        if (!state.img) return
        ev.preventDefault()
        const zoomCenter = {
            x: ev.clientX - state.wrap.getBoundingClientRect().left,
            y: ev.clientY - state.wrap.getBoundingClientRect().top
        }
        
        const zoomFactor = ev.deltaY < 0 ? 1.1 : 0.9
        const newZoom = zoom * zoomFactor
        
        // Adjust pan to zoom towards mouse cursor
        panX = zoomCenter.x - (zoomCenter.x - panX) * (newZoom / zoom)
        panY = zoomCenter.y - (zoomCenter.y - panY) * (newZoom / zoom)
        
        zoom = newZoom
        updateZoomLabel()
        applyTransform(canvas)
    })

    canvas.addEventListener('mousedown', onDown)
    canvas.addEventListener('contextmenu', e => e.preventDefault())
    window.addEventListener('mousemove', onMove) // window to catch drags outside
    window.addEventListener('mouseup', onUp)
  }

  function drawPreview(canvas, curStroke, pos) {
    const ctx = canvas.getContext('2d')
    ctx.save()
    ctx.lineCap = 'round'; ctx.lineJoin = 'round'

    if (curStroke && (tool === 'pen' || tool === 'highlight' || tool === 'eraser')) {
      if (tool === 'highlight') { ctx.globalAlpha = 0.35; ctx.lineWidth = strokeW * 4; ctx.strokeStyle = colour }
      else if (tool === 'eraser') { ctx.globalCompositeOperation = 'destination-out'; ctx.lineWidth = strokeW * 2.5 }
      else { ctx.lineWidth = strokeW; ctx.strokeStyle = colour }
      ctx.beginPath(); ctx.moveTo(curStroke.pts[0].x, curStroke.pts[0].y)
      for (let i = 1; i < curStroke.pts.length; i++) ctx.lineTo(curStroke.pts[i].x, curStroke.pts[i].y)
      ctx.stroke()
    }

    if (tool === 'rect' || tool === 'circle' || tool === 'arrow') {
      const w = pos.x - startX, h = pos.y - startY
      ctx.lineWidth = strokeW
      ctx.strokeStyle = colour
      if (tool === 'rect') {
          ctx.strokeRect(startX, startY, w, h)
      } else if (tool === 'circle') {
        ctx.beginPath()
        ctx.ellipse(startX + w / 2, startY + h / 2, Math.abs(w) / 2, Math.abs(h) / 2, 0, 0, Math.PI * 2)
        ctx.stroke()
      } else {
        const dx = w, dy = h, len = Math.sqrt(dx * dx + dy * dy) || 1
        const ux = dx / len, uy = dy / len, headLen = Math.min(16 + strokeW * 2, len * 0.6)
        ctx.beginPath(); ctx.moveTo(startX, startY); ctx.lineTo(startX + dx - ux * headLen, startY + dy - uy * headLen); ctx.stroke()
        ctx.fillStyle = colour; ctx.beginPath()
        ctx.moveTo(startX + dx, startY + dy)
        ctx.lineTo(startX + dx - ux * headLen + uy * headLen * 0.4, startY + dy - uy * headLen - ux * headLen * 0.4)
        ctx.lineTo(startX + dx - ux * headLen - uy * headLen * 0.4, startY + dy - uy * headLen + ux * headLen * 0.4)
        ctx.closePath(); ctx.fill()
      }
    }
    ctx.restore()
  }

  return { init, loadFile }
})()