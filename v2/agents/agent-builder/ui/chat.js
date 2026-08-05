/* The conversation with Agent Builder.

   Streaming is append-only into a per-message buffer, re-rendered on a frame. Autoscroll
   FOLLOWS THE USER: the moment you scroll away from the bottom it stops, and it resumes when
   you come back. A chat that yanks you to the bottom mid-read is unusable.

   Tool activity is shown as it happens — that is how you watch an agent being built. When a
   file-writing tool finishes, the inspector refreshes so the new file appears (and flashes). */

window.Chat = (function () {
  const $ = (id) => document.getElementById(id)
  const AGENT = 'agent-builder'   // whose transcripts these are; the window is scoped to it
  let client = null
  let sessionKey = null
  let running = false
  let onToolDone = null
  let onSession = null            // tell the rail which chat is open / that a new one started

  let node = null          // the assistant bubble being streamed into
  let buf = ''             // its markdown so far
  let thinkNode = null
  let thinkBuf = ''
  let frame = 0
  const tools = new Map()  // toolCallId -> its row

  // ── autoscroll that respects the reader ────────────────────────────────────
  let stick = true
  function nearBottom() {
    const t = $('thread')
    return t.scrollHeight - t.scrollTop - t.clientHeight < 120
  }
  function follow() { if (stick) $('thread').scrollTop = $('thread').scrollHeight }

  function init(c, opts = {}) {
    client = c
    onToolDone = opts.onToolDone || null
    onSession = opts.onSession || null
    sessionKey = `builder-${Date.now().toString(36)}`

    $('thread').addEventListener('scroll', () => { stick = nearBottom() })

    const input = $('input')
    input.addEventListener('input', () => {
      input.style.height = 'auto'
      input.style.height = `${Math.min(input.scrollHeight, window.innerHeight * 0.4)}px`
    })
    input.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); void send() }
    })
    $('send').addEventListener('click', () => (running ? abort() : send()))
    $('newChat').addEventListener('click', reset)

    client.on('chat.event', (p) => {
      if (p.sessionKey === sessionKey) handle(p.event || {})
    })
  }

  function reset() {
    sessionKey = `builder-${Date.now().toString(36)}`
    $('thread').textContent = ''
    $('thread').append(hero())
    node = thinkNode = null
    buf = thinkBuf = ''
    tools.clear()
    stick = true
    if (onSession) onSession(sessionKey)
  }

  /** Open a saved conversation: render its transcript, then keep talking INTO it — the same
   *  sessionKey goes back out on the next send, so the thread continues rather than forking. */
  async function open(key) {
    sessionKey = key
    $('thread').textContent = ''
    node = thinkNode = null
    buf = thinkBuf = ''
    tools.clear()
    try {
      const res = await client.history(key, AGENT)
      for (const m of (res && res.messages) || []) replay(m)
    } catch (e) {
      const b = bubble('bot')
      b.innerHTML = MD.render(`**Could not load this chat.** ${(e && e.message) || e}`)
    }
    stick = true
    follow()
    if (onSession) onSession(sessionKey)
  }

  /** One stored message -> the same shapes a live run produces. A user message's `content` is
   *  a plain string; an assistant's is a list of typed blocks. */
  function replay(m) {
    if (m.role === 'user') {
      const text = typeof m.content === 'string'
        ? m.content
        : (m.content || []).map((c) => c.text || '').join('')
      if (text.trim()) bubble('user').textContent = text
      return
    }
    if (m.role !== 'assistant') return
    const blocks = Array.isArray(m.content) ? m.content : []
    const text = blocks.filter((c) => c.type === 'text').map((c) => c.text || '').join('')
    for (const c of blocks) {
      if (c.type !== 'tool_use' && c.type !== 'toolcall') continue
      const row = document.createElement('div')
      row.className = 'tool'
      row.innerHTML =
        '<span class="done">✓</span>' +
        `<span class="tname">${MD.esc(c.name || 'tool')}</span>` +
        `<span class="targs">${MD.esc(summarize(c.input || c.arguments))}</span>`
      $('thread').append(row)
    }
    if (text.trim()) bubble('bot').innerHTML = MD.render(text)
  }

  function hero() {
    const h = $('hero')
    if (h) return h
    const d = document.createElement('div')
    d.className = 'hero'
    d.id = 'hero'
    d.innerHTML =
      '<h1>What should we build?</h1>' +
      "<p>Describe an agent — what it does, what it needs access to — and I'll write it, " +
      'check it, and make it shippable.</p><div class="suggests" id="suggests"></div>'
    return d
  }

  function dropHero() {
    const h = $('hero')
    if (h) h.remove()
  }

  function bubble(role) {
    const wrap = document.createElement('div')
    wrap.className = `msg ${role}`
    const label = document.createElement('div')
    label.className = 'role'
    label.textContent = role === 'user' ? 'You' : 'Agent Builder'
    const body = document.createElement('div')
    body.className = role === 'user' ? 'bubble' : 'bubble md'
    wrap.append(label, body)
    $('thread').append(wrap)
    return body
  }

  async function send() {
    const input = $('input')
    const text = input.value.trim()
    if (!text || running) return
    dropHero()
    bubble('user').textContent = text
    input.value = ''
    input.style.height = 'auto'
    stick = true
    follow()

    running = true
    setSending(true)
    try {
      // `message`, not `text` — chat.send reads params.message and rejects an empty one.
      await client.send({ sessionKey, message: text })
    } catch (e) {
      running = false
      setSending(false)
      const b = bubble('bot')
      b.innerHTML = MD.render(`**Could not send.** ${(e && e.message) || e}`)
    }
  }

  async function abort() {
    try { await client.abort(sessionKey) } catch { /* the run may have just ended */ }
  }

  function setSending(on) {
    const s = $('send')
    s.textContent = on ? '■' : '↑'
    s.classList.toggle('stop', on)
    s.title = on ? 'Stop' : 'Send'
    $('hint').textContent = on ? 'running…' : 'Enter to send · Shift+Enter for a new line'
  }

  /** Re-render the streaming bubble at most once per frame — deltas arrive far faster.
   *  The guard is raised BEFORE scheduling, not from the callback's return value: if the
   *  callback ever runs synchronously, assigning the handle afterwards would overwrite the
   *  0 it just set and wedge every later repaint. */
  function paint() {
    if (frame) return
    frame = 1
    requestAnimationFrame(() => {
      frame = 0
      if (node) node.innerHTML = MD.render(buf) + '<span class="caret"></span>'
      if (thinkNode) thinkNode.textContent = thinkBuf
      follow()
    })
  }

  /** Commit whatever is streaming and drop the caret. Called whenever the assistant STOPS
   *  writing prose — a tool starting counts, and forgetting it left a blinking caret
   *  stranded on every bubble that was interrupted by a tool call. */
  function settle() {
    if (node) { node.innerHTML = MD.render(buf); node = null }
    buf = ''
    thinkNode = null
    thinkBuf = ''
  }

  function summarize(args) {
    if (!args || typeof args !== 'object') return ''
    for (const k of ['agent_id', 'id', 'path', 'name', 'query', 'file']) {
      if (args[k]) return String(args[k]).slice(0, 70)
    }
    const first = Object.values(args)[0]
    return first == null ? '' : String(first).slice(0, 70)
  }

  // tools that change an agent on disk — the inspector should re-read after these
  const WRITES = /^(write|edit|create_agent|create_tool|skill_workshop|reload_agent)$/

  function handle(ev) {
    switch (ev.type) {
      case 'message_update': {
        dropHero()
        if (ev.kind === 'thinking_delta') {
          if (!thinkNode) {
            thinkNode = document.createElement('div')
            thinkNode.className = 'think'
            $('thread').append(thinkNode)
            thinkBuf = ''
          }
          thinkBuf += ev.delta || ''
        } else if (ev.kind === 'text_delta') {
          if (!node) { node = bubble('bot'); buf = '' }
          buf += ev.delta || ''
        }
        paint()
        break
      }
      case 'tool_execution_start': {
        dropHero()
        const row = document.createElement('div')
        row.className = 'tool'
        row.innerHTML =
          '<span class="spin"></span>' +
          `<span class="tname">${MD.esc(ev.toolName || '?')}</span>` +
          `<span class="targs">${MD.esc(summarize(ev.args))}</span>`
        $('thread').append(row)
        tools.set(ev.toolCallId || ev.toolName, row)
        // The assistant stopped writing to start a tool. Commit the bubble (and lose the
        // caret) — the next delta opens a fresh one below the tool row.
        settle()
        follow()
        break
      }
      case 'tool_execution_end': {
        const row = tools.get(ev.toolCallId || ev.toolName)
        if (row) {
          const spin = row.querySelector('.spin')
          if (spin) {
            const mark = document.createElement('span')
            mark.className = ev.isError ? 'fail' : 'done'
            mark.textContent = ev.isError ? '✕' : '✓'
            spin.replaceWith(mark)
          }
          if (ev.isError) row.classList.add('err')
        }
        if (WRITES.test(ev.toolName || '') && onToolDone) onToolDone(ev)
        break
      }
      // A run is MANY turns — the model answers, calls a tool, answers again. `turn_end`
      // fires after each one, so it must only settle the current bubble; treating it as the
      // end would flip the composer back to idle while the run is still going.
      case 'message_end':
      case 'turn_end': {
        settle()
        follow()
        break
      }
      // The configured model could not answer and another one took over. Never silent:
      // "the model you chose is not the one replying" is the fact that turns an unpaid API
      // key from a mystery into a one-line fix.
      case 'model_fallback': {
        dropHero()
        settle()
        const row = document.createElement('div')
        row.className = 'tool err'
        row.innerHTML =
          '<span class="fail">⚠</span>' +
          `<span class="tname">${MD.esc(ev.from || '?')} unavailable</span>` +
          `<span class="targs">→ ${MD.esc(ev.to || '?')} · ${MD.esc((ev.reason || '').slice(0, 120))}</span>`
        $('thread').append(row)
        follow()
        break
      }
      // `agent_end` is the run terminal (stopReason, and `error` when it failed).
      case 'agent_end': {
        running = false
        setSending(false)
        settle()
        if (ev.error) {
          const b = bubble('bot')
          b.innerHTML = MD.render(`**Run failed.** ${ev.error}`)
        }
        follow()
        break
      }
      case 'error': {
        running = false
        setSending(false)
        const b = bubble('bot')
        b.innerHTML = MD.render(`**Error.** ${ev.message || 'the run failed'}`)
        break
      }
    }
  }

  function ask(text) {
    $('input').value = text
    void send()
  }

  return { init, reset, open, ask, get sessionKey() { return sessionKey } }
})()
