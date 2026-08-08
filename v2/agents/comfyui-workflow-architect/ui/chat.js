/* The conversation.

   ─────────────────────────────────────────────────────────────────────────────────────────
   THIS FILE IS THE PROTOCOL. It is correct against the daemon as shipped, and it is checked
   in CI. Change the WORDS (labels, the hero, the bot's name) and the LOOK (style.css).
   Do not change how events are read — every past hand-written chat view got that wrong in
   the same two ways, and both are invisible at runtime:

     1. the event arrives WRAPPED — `{sessionKey, runId, agentId, ts, event}`. The type you
        switch on is `payload.event.type`, one level down. Reading `payload.type` makes every
        branch miss and the screen simply never updates.
     2. streamed text is `message_update` with `kind: 'text_delta'`. There is no
        `message_delta` event. A branch on one can never run.

   ─────────────────────────────────────────────────────────────────────────────────────────
   No agent id appears anywhere below. The window is opened with `?scope=<agent-id>` and the
   daemon FORCES that agent onto every request the page makes — so `sessions()`, `history()`
   and `send()` are already about this agent, and naming it would only be a second copy of the
   id to keep in sync with agent.toml.

   Streaming is append-only into a buffer, re-rendered once per frame; deltas arrive far
   faster than the screen refreshes. Autoscroll FOLLOWS THE READER: it stops the moment you
   scroll up and resumes when you come back, because a view that yanks you to the bottom
   mid-read cannot be read. */

window.Chat = (function () {
  const $ = (id) => document.getElementById(id)

  // Label shown above assistant messages.
  const BOT_NAME = 'Workflow Architect'

  let client = null
  let sessionKey = null
  let running = false
  let onToolDone = null   // a tool finished — refresh whatever the page shows about its work
  let onSession = null    // which conversation is live, so the history rail can highlight it

  let node = null         // the assistant bubble being streamed into
  let buf = ''            // its markdown so far
  let thinkNode = null
  let thinkBuf = ''
  let frame = 0
  const tools = new Map() // toolCallId -> its row

  // ── attachments ───────────────────────────────────────────────────────────
  // Three routes, because people reach for all three: paste, drag-and-drop, and the button.
  const MAX_FILES = 10
  let pending = []  // [{name, mimeType, dataBase64}]

  /** A pasted screenshot usually has no usable filename. The daemon would then store it as
   *  literally "attachment" — no extension, so it is not classified as an image, so a vision
   *  model never receives it as one. Name it from its mime type instead. */
  function fileName(f) {
    if (f.name && f.name.includes('.')) return f.name
    const ext = ((f.type.split('/')[1] || 'bin').split('+')[0]).replace(/[^a-z0-9]/gi, '')
    const stamp = new Date().toISOString().replace(/[-:T]/g, '').slice(0, 14)
    return `${f.name || 'pasted'}-${stamp}.${ext}`
  }

  function readFile(f) {
    return new Promise((resolve, reject) => {
      const r = new FileReader()
      r.onload = () => resolve({
        name: fileName(f),
        mimeType: f.type || 'application/octet-stream',
        dataBase64: String(r.result).split(',')[1] || '',
      })
      r.onerror = () => reject(r.error)
      r.readAsDataURL(f)
    })
  }

  async function addFiles(list) {
    const files = Array.from(list || [])
    if (!files.length) return
    const room = MAX_FILES - pending.length
    if (room <= 0) return
    pending = pending.concat(await Promise.all(files.slice(0, room).map(readFile)))
    drawAttachments()
  }

  function text(tag, cls, value) {
    const n = document.createElement(tag)
    n.className = cls
    n.textContent = value
    return n
  }

  function drawAttachments() {
    const box = $('attachments')
    box.textContent = ''
    box.hidden = !pending.length
    pending.forEach((a, i) => {
      const chip = document.createElement('span')
      chip.className = 'chip-file'
      if (a.mimeType.startsWith('image/')) {
        const img = document.createElement('img')
        img.src = `data:${a.mimeType};base64,${a.dataBase64}`
        img.alt = a.name
        chip.append(img)
      }
      chip.append(text('span', 'chip-name', a.name))
      const x = document.createElement('button')
      x.className = 'chip-x'
      x.textContent = '✕'
      x.title = 'Remove'
      x.addEventListener('click', () => { pending.splice(i, 1); drawAttachments() })
      chip.append(x)
      box.append(chip)
    })
  }

  // ── autoscroll that respects the reader ───────────────────────────────────
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
    sessionKey = newKey()

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
    $('fork').addEventListener('click', () => void fork())
    $('newChat').addEventListener('click', reset)

    // Paste. `files` covers a copied FILE, but a copied IMAGE (a screenshot tool, "copy
    // image") can arrive ONLY in `items` with `files` empty — read both, or pasting a
    // screenshot silently does nothing. A plain text paste falls through untouched.
    input.addEventListener('paste', (e) => {
      const dt = e.clipboardData
      if (!dt) return
      const fromItems = Array.from(dt.items || [])
        .filter((it) => it.kind === 'file')
        .map((it) => it.getAsFile())
        .filter(Boolean)
      const files = dt.files && dt.files.length ? Array.from(dt.files) : fromItems
      if (!files.length) return
      e.preventDefault()
      void addFiles(files)
    })

    // Drop a file anywhere in the window to attach it.
    //
    // The only feedback is a tint on the composer's border, cleared by a self-expiring timer:
    // `dragover` fires continuously while a drag is live, so "no dragover recently" reliably
    // means it ended, however it ended. Counting dragenter/dragleave instead looks correct
    // and is not — they fire per child element, and a drop outside the counted subtree never
    // decrements, which leaves the drop state stuck on forever.
    const DRAG_IDLE_MS = 700
    const hasFiles = (dt) => !!dt && Array.from(dt.types || []).includes('Files')
    let dragTimer = null
    const dragOff = () => {
      clearTimeout(dragTimer)
      dragTimer = null
      $('composer').classList.remove('drag')
    }
    const dragOn = () => {
      $('composer').classList.add('drag')
      clearTimeout(dragTimer)
      dragTimer = setTimeout(dragOff, DRAG_IDLE_MS)
    }

    // Window level, not a drop zone: preventDefault is REQUIRED, or the window navigates to
    // the dropped file:// URL and your whole UI is replaced by the image.
    window.addEventListener('dragover', (e) => {
      if (!hasFiles(e.dataTransfer)) return
      e.preventDefault()
      e.dataTransfer.dropEffect = 'copy'
      dragOn()
    })
    window.addEventListener('drop', (e) => {
      if (!hasFiles(e.dataTransfer)) return
      e.preventDefault()
      dragOff()
      void addFiles(e.dataTransfer.files)
    })
    window.addEventListener('dragend', dragOff)
    window.addEventListener('blur', dragOff)

    const picker = $('filePicker')
    $('attach').addEventListener('click', () => picker.click())
    picker.addEventListener('change', () => { void addFiles(picker.files); picker.value = '' })

    // THE EVENT FEED. Every run event for every session of this agent arrives here; the
    // sessionKey check is what keeps another window's run out of this thread.
    client.on('chat.event', (payload) => {
      if (payload.sessionKey === sessionKey) handle(payload.event || {})
    })
  }

  const newKey = () => `chat-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 7)}`

  function reset() {
    sessionKey = newKey()
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
      const res = await client.history(key)
      for (const m of (res && res.messages) || []) replay(m)
    } catch (e) {
      bubble('bot').innerHTML = MD.render(`**Could not load this chat.** ${(e && e.message) || e}`)
    }
    stick = true
    follow()
    if (onSession) onSession(sessionKey)
  }

  /** One STORED message -> the same shapes a live run produces. Note the difference: a user
   *  message's `content` is a plain string, an assistant's is a list of typed blocks. */
  function replay(m) {
    if (m.role === 'user') {
      const body = typeof m.content === 'string'
        ? m.content
        : (m.content || []).map((c) => c.text || '').join('')
      if (body.trim()) bubble('user').textContent = body
      return
    }
    if (m.role !== 'assistant') return
    const blocks = Array.isArray(m.content) ? m.content : []
    const body = blocks.filter((c) => c.type === 'text').map((c) => c.text || '').join('')
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
    if (body.trim()) bubble('bot').innerHTML = MD.render(body)
  }

  /** The empty state. It is rebuilt rather than kept hidden, so "new chat" always looks new. */
  function hero() {
    const h = $('hero')
    if (h) return h
    const d = document.createElement('div')
    d.className = 'hero'
    d.id = 'hero'
    d.innerHTML =
      '<h1>Build a runnable ComfyUI workflow</h1>' +
      '<p>Describe the result, models, constraints, or workflow changes you want. I’ll research current components, generate the graph, and validate it.</p>' +
      '<div class="suggests" id="suggests"></div>'
    return d
  }

  function dropHero() {
    const h = $('hero')
    if (h) h.remove()
  }

  /** What the user just attached, shown on their own bubble — an image as a thumbnail, so
   *  they can see WHICH screenshot they sent; anything else as a named chip. */
  function userAttachments(atts) {
    const box = document.createElement('div')
    box.className = 'msg-files'
    for (const a of atts) {
      if (a.mimeType.startsWith('image/')) {
        const img = document.createElement('img')
        img.src = `data:${a.mimeType};base64,${a.dataBase64}`
        img.alt = a.name
        img.title = a.name
        box.append(img)
      } else {
        box.append(text('span', 'chip-file', a.name))
      }
    }
    return box
  }

  function bubble(role) {
    const wrap = document.createElement('div')
    wrap.className = `msg ${role}`
    const label = document.createElement('div')
    label.className = 'role'
    label.textContent = role === 'user' ? 'You' : BOT_NAME
    const body = document.createElement('div')
    body.className = role === 'user' ? 'bubble' : 'bubble md'
    wrap.append(label, body)
    $('thread').append(wrap)
    return body
  }

  async function send() {
    const input = $('input')
    const body = input.value.trim()
    // a message may be attachments-only — the daemon accepts that, so don't require text
    if ((!body && !pending.length) || running) return
    dropHero()
    const b = bubble('user')
    if (body) b.textContent = body
    if (pending.length) b.append(userAttachments(pending))
    const sending = pending
    pending = []
    drawAttachments()
    input.value = ''
    input.style.height = 'auto'
    stick = true
    follow()

    running = true
    setSending(true)
    try {
      // `message`, not `text` — chat.send reads params.message and rejects an empty one.
      // No agentId: the daemon forces this window's own agent onto the request.
      await client.send({
        sessionKey,
        message: body,
        ...(sending.length ? { attachments: sending } : {}),
      })
    } catch (e) {
      // The send never left. Say so IN THE THREAD and unlock the composer — a silent failure
      // here looks exactly like a slow model, and the user waits forever.
      running = false
      setSending(false)
      bubble('bot').innerHTML = MD.render(`**Could not send.** ${(e && e.message) || e}`)
    }
  }

  async function abort() {
    try { await client.abort(sessionKey) } catch { /* the run may have just ended on its own */ }
  }

  function setSending(on) {
    const s = $('send')
    s.textContent = on ? '■' : '↑'
    s.classList.toggle('stop', on)
    s.title = on ? 'Stop' : 'Send'
  }

  /** Re-render the streaming bubble at most once per frame.
   *
   *  The guard is raised BEFORE scheduling, not from the callback's return value: if the
   *  callback ever runs synchronously, assigning the handle afterwards overwrites the 0 it
   *  just set, and every later repaint is wedged. */
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
   *  writing prose — a tool starting counts, and forgetting that leaves a blinking caret
   *  stranded on every bubble a tool call interrupted. */
  function settle() {
    if (node) { node.innerHTML = MD.render(buf); node = null }
    buf = ''
    thinkNode = null
    thinkBuf = ''
  }

  /** One line describing what a tool is doing. Tool args have no common shape, so this picks
   *  the most identifying field it recognises and falls back to the first value. */
  function summarize(args) {
    if (!args || typeof args !== 'object') return ''
    for (const k of ['path', 'id', 'name', 'query', 'url', 'file']) {
      if (args[k]) return String(args[k]).slice(0, 70)
    }
    const first = Object.values(args)[0]
    return first == null ? '' : String(first).slice(0, 70)
  }

  function handle(ev) {
    switch (ev.type) {
      // STREAMED OUTPUT. `kind` distinguishes the visible answer from the model's reasoning.
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

      // TOOL ACTIVITY. Showing it is most of what makes an agent feel like it is working
      // rather than hanging.
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
        // The assistant stopped writing in order to run something. Commit the bubble (and
        // lose the caret); the next delta opens a fresh one below this row.
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
        if (onToolDone) onToolDone(ev)
        break
      }

      // A run is MANY turns — the model answers, calls a tool, answers again. `turn_end`
      // fires after each one, so it may only settle the current bubble. Treating it as the
      // end of the run flips the composer back to idle while the agent is still working.
      case 'message_end':
      case 'turn_end': {
        settle()
        follow()
        break
      }

      // The configured model could not answer and another took over. Never swallow this:
      // "the model you picked is not the one replying" is the fact that turns an unpaid API
      // key from an unexplained hang into a one-line fix.
      case 'model_fallback': {
        dropHero()
        settle()
        const row = document.createElement('div')
        row.className = 'tool err'
        row.innerHTML =
          '<span class="fail">⚠</span>' +
          `<span class="tname">${MD.esc(ev.from || '?')} unavailable</span>` +
          `<span class="targs">→ ${MD.esc(ev.to || '?')} · ` +
          `${MD.esc((ev.reason || '').slice(0, 120))}</span>`
        $('thread').append(row)
        follow()
        break
      }

      // THE RUN TERMINAL. `stopReason` says how it ended, `error` is present when it failed.
      case 'agent_end': {
        running = false
        setSending(false)
        settle()
        if (ev.error) bubble('bot').innerHTML = MD.render(`**Run failed.** ${ev.error}`)
        follow()
        break
      }

      // A transport-level failure, outside any run.
      case 'error': {
        running = false
        setSending(false)
        bubble('bot').innerHTML = MD.render(`**Error.** ${ev.message || 'the run failed'}`)
        break
      }
    }
  }

  /** Put text in the box and send it — what a suggestion chip calls. */
  function ask(body) {
    $('input').value = body
    void send()
  }

  /** Fork the conversation: start a new chat with the current history transcribed as context. */
  async function fork() {
    if (running) {
      try { await client.abort(sessionKey) } catch { /* ok */ }
      running = false
      setSending(false)
    }
    const oldKey = sessionKey
    let transcript = ''
    try {
      const res = await client.history(oldKey)
      const msgs = (res && res.messages) || []
      if (msgs.length) {
        transcript = msgs.map((m) => {
          const role = m.role === 'user' ? 'User' : 'Assistant'
          const body = typeof m.content === 'string'
            ? m.content
            : (Array.isArray(m.content) ? m.content.filter((c) => c.type === 'text').map((c) => c.text || '').join('') : '')
          return `### ${role}\n${body.trim()}`
        }).join('\n\n')
      }
    } catch { /* fork works even without history — it just starts clean */ }

    // Now reset
    reset()
    $('input').value = ''
    input.style.height = 'auto'

    if (transcript) {
      const header = '---\nThe conversation above was forked. Use its context to continue.\n---\n\n'
      $('input').value = header + transcript
      // Show a brief info bubble so the user knows it worked
      const info = bubble('bot')
      info.innerHTML = MD.render('**Conversation forked.** The history has been copied into the input below. Edit or clarify, then send.')
    }
    stick = true
    follow()
  }

  return { init, reset, open, ask, fork, get sessionKey() { return sessionKey } }
})()
