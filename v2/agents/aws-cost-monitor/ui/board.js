/* The dashboard itself: tiles, a chart, a table — all from ONE tool call.

   ─────────────────────────────────────────────────────────────────────────────────────────
   THE ONE THING TO CHANGE

   `TOOL` and `shape()`. Everything else is plumbing that already works.

   `TOOL` is one of THIS agent's own tools. `client.invokeTool` runs it directly: no chat turn,
   no model call, no tokens. That is what makes Refresh instant and what makes a dashboard
   honest — the screen shows what the agent actually knows, not a summary it wrote about it.

   `shape()` turns whatever that tool returns into { tiles, series, rows }. Write the tool to
   return JSON and this stays four lines.

   ─────────────────────────────────────────────────────────────────────────────────────────
   WHY THE CHART IS HAND-ROLLED SVG

   No CDN, no charting library. A published agent's page is served under a strict CSP, so an
   external <script> silently never loads — you get a blank rectangle and a console nobody is
   looking at. Fifty lines of <polyline> always renders. If you need more than a line and some
   bars, draw more SVG; do not reach for a bundle.

   ─────────────────────────────────────────────────────────────────────────────────────────
   EVERY STATE IS DRAWN

   loading / empty / error / data. A dashboard that shows nothing while it loads and nothing
   when it fails has one appearance for two very different situations, and the user cannot tell
   which one they are looking at. */

window.Board = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let timer = null

  // The tool this dashboard reads, and its arguments.
  const TOOL = 'get_cost_dashboard'
  const ARGS = {}

  // Auto-refresh. Cost data only changes when the agent re-fetches from AWS, so a timer would
  // redraw identical numbers — leave it off; Refresh re-reads the stored snapshot instantly.
  const REFRESH_MS = 0

  /** tool result -> what the screen draws. `get_cost_dashboard` already returns exactly
   *  this shape, so this stays a straight pass-through.
   *
   *  tiles  [{ label, value, delta }]   the numbers across the top; `delta` is optional
   *  series [{ label, points: [n] }]    one line per entry
   *  rows   { columns: [str], data: [[cell]] }
   *
   *  Returning empty arrays is legitimate and renders the empty state — say so in `note`. */
  function shape(result) {
    const data = result || {}
    return {
      tiles: data.tiles || [],
      series: data.series || [],
      bars: data.bars || [],
      rows: data.rows || { columns: [], data: [] },
      note: data.note || '',
    }
  }

  function init(c) {
    client = c
    const btn = $('refresh')
    if (btn) btn.addEventListener('click', () => void load())
  }

  async function load() {
    const msg = $('boardMsg')
    if (!TOOL) {
      // The scaffolded state. Saying so beats an empty screen that looks broken.
      msg.className = 'board-msg'
      msg.textContent = 'No tool wired yet — set TOOL in board.js to one of this agent’s tools.'
      return
    }
    setBusy(true)
    try {
      const res = await client.invokeTool(TOOL, ARGS)
      const text = agentd.resultText(res)
      let parsed
      try {
        parsed = JSON.parse(text)
      } catch {
        // A tool that answers in prose is not a failure — show it rather than pretending the
        // dashboard is empty.
        parsed = { note: text }
      }
      draw(shape(parsed))
      stamp()
      msg.textContent = ''
    } catch (e) {
      // The error goes ON THE SCREEN. A dashboard whose refresh silently does nothing is
      // indistinguishable from one whose numbers simply have not changed.
      msg.className = 'board-msg bad'
      msg.textContent = `could not load: ${(e && e.message) || e}`
    } finally {
      setBusy(false)
    }
  }

  function setBusy(on) {
    const btn = $('refresh')
    if (btn) {
      btn.disabled = on
      btn.textContent = on ? 'refreshing…' : 'Refresh'
    }
  }

  /** Show a one-line status under the board (used by app.js during auto-load). */
  function showLoading(text) {
    const msg = $('boardMsg')
    if (!msg) return
    msg.className = 'board-msg'
    msg.textContent = text || ''
  }

  function stamp() {
    const el = $('stamp')
    if (el) el.textContent = `updated ${new Date().toLocaleTimeString()}`
  }

  // ── drawing ───────────────────────────────────────────────────────────────
  function el(tag, cls, text) {
    const n = document.createElement(tag)
    if (cls) n.className = cls
    if (text != null) n.textContent = text
    return n
  }

  function draw(view) {
    drawTiles(view.tiles)
    drawChart(view.series)
    drawBars(view.bars)
    drawTable(view.rows)
    if (view.note) {
      const msg = $('boardMsg')
      msg.className = 'board-msg'
      msg.textContent = view.note
    }
  }

  function drawTiles(tiles) {
    const box = $('tiles')
    box.textContent = ''
    if (!tiles.length) {
      box.append(el('p', 'empty', 'Nothing to show yet.'))
      return
    }
    for (const t of tiles) {
      const card = el('div', 'tile')
      card.append(el('span', 'tile-label', t.label || ''))
      card.append(el('span', 'tile-value', String(t.value == null ? '—' : t.value)))
      if (t.delta != null && t.delta !== '') {
        const up = Number(t.delta) >= 0
        card.append(el('span', `tile-delta ${up ? 'up' : 'down'}`, `${up ? '▲' : '▼'} ${t.delta}`))
      }
      box.append(card)
    }
  }

  /** A line chart in plain SVG. viewBox + preserveAspectRatio="none" makes it responsive with
   *  no resize listener and no measuring — the browser does the scaling. */
  function drawChart(series) {
    const wrap = $('chartWrap')
    wrap.textContent = ''
    const lines = (series || []).filter((s) => (s.points || []).length > 1)
    if (!lines.length) {
      wrap.append(el('p', 'empty', 'No trend data.'))
      return
    }
    const W = 600
    const H = 180
    const all = lines.flatMap((s) => s.points)
    const min = Math.min(...all)
    const max = Math.max(...all)
    const span = max - min || 1

    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
    svg.setAttribute('preserveAspectRatio', 'none')
    svg.setAttribute('class', 'chart')

    // Baseline, so a flat line reads as flat rather than as missing.
    const axis = document.createElementNS('http://www.w3.org/2000/svg', 'line')
    axis.setAttribute('x1', '0')
    axis.setAttribute('y1', String(H - 1))
    axis.setAttribute('x2', String(W))
    axis.setAttribute('y2', String(H - 1))
    axis.setAttribute('class', 'axis')
    svg.append(axis)

    lines.forEach((s, i) => {
      const pts = s.points
      const step = W / (pts.length - 1)
      const coords = pts
        .map((p, x) => `${(x * step).toFixed(1)},${(H - ((p - min) / span) * (H - 12) - 6).toFixed(1)}`)
        .join(' ')
      const poly = document.createElementNS('http://www.w3.org/2000/svg', 'polyline')
      poly.setAttribute('points', coords)
      poly.setAttribute('class', `line line-${i % 4}`)
      svg.append(poly)
    })
    wrap.append(svg)

    const legend = el('div', 'legend')
    lines.forEach((s, i) => {
      const item = el('span', 'legend-item')
      item.append(el('span', `swatch swatch-${i % 4}`))
      item.append(el('span', null, s.label || `series ${i + 1}`))
      legend.append(item)
    })
    wrap.append(legend)
  }

  /** A horizontal bar chart in plain SVG — the cost of each service side by side, so the
   *  ranking reads at a glance. Same responsive, hand-rolled approach as the line chart. */
  function drawBars(bars) {
    const wrap = $('barWrap')
    wrap.textContent = ''
    const rows = (bars || []).filter((b) => Number(b.value) > 0)
    if (!rows.length) {
      wrap.append(el('p', 'empty', 'No per-service cost data.'))
      return
    }
    const W = 600
    const H = 24 * rows.length + 8
    const max = Math.max(...rows.map((b) => Number(b.value))) || 1
    const svg = document.createElementNS('http://www.w3.org/2000/svg', 'svg')
    svg.setAttribute('viewBox', `0 0 ${W} ${H}`)
    svg.setAttribute('preserveAspectRatio', 'none')
    svg.setAttribute('class', 'chart')

    rows.forEach((b, i) => {
      const y = i * 24 + 2
      const h = 16
      const w = Math.max(2, (Number(b.value) / max) * (W - 150))
      const bar = document.createElementNS('http://www.w3.org/2000/svg', 'rect')
      bar.setAttribute('x', '150')
      bar.setAttribute('y', String(y))
      bar.setAttribute('width', String(w))
      bar.setAttribute('height', String(h))
      bar.setAttribute('rx', '3')
      bar.setAttribute('class', `bar bar-${i % 4}`)
      svg.append(bar)
      const label = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      label.setAttribute('x', '144')
      label.setAttribute('y', String(y + h - 3))
      label.setAttribute('text-anchor', 'end')
      label.setAttribute('class', 'bar-label')
      label.textContent = b.label || ''
      svg.append(label)
      const val = document.createElementNS('http://www.w3.org/2000/svg', 'text')
      val.setAttribute('x', String(150 + w + 4))
      val.setAttribute('y', String(y + h - 3))
      val.setAttribute('class', 'bar-val')
      val.textContent = Number(b.value).toLocaleString(undefined, { maximumFractionDigits: 2 })
      svg.append(val)
    })
    wrap.append(svg)
  }

  function drawTable(rows) {
    const table = $('grid')
    table.textContent = ''
    const cols = (rows && rows.columns) || []
    const data = (rows && rows.data) || []
    if (!cols.length) {
      table.append(el('caption', 'empty', 'No detail rows.'))
      return
    }
    const head = document.createElement('tr')
    for (const c of cols) head.append(el('th', null, String(c)))
    table.append(head)
    for (const r of data) {
      const tr = document.createElement('tr')
      for (const cell of r) tr.append(el('td', null, cell == null ? '' : String(cell)))
      table.append(tr)
    }
  }

  function start() {
    void load()
    if (REFRESH_MS > 0 && !timer) timer = setInterval(() => void load(), REFRESH_MS)
  }

  return { init, load, start, showLoading }
})()
