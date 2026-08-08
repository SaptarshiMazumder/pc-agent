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
  let onReset = null              // the hero is back and EMPTY — app.js fills it from the roster

  // The agent this conversation is ABOUT (null = we are creating something new). Carried into
  // the FIRST message only; after that it is in the transcript and repeating it is noise.
  let scope = null
  let scopeSent = false

  let node = null          // the assistant bubble being streamed into
  let buf = ''             // its markdown so far
  let thinkNode = null
  let thinkBuf = ''
  let frame = 0
  const tools = new Map()  // toolCallId -> its row

  // ── attachments ───────────────────────────────────────────────────────────
  // Screenshots are how you show an agent what is wrong with an agent, so this window needs
  // them. Three routes, because people reach for all three: paste, drag-drop, and a button.
  const MAX_FILES = 10
  let pending = []   // [{name, mimeType, dataBase64}]

  /** A clipboard image usually has no usable filename ('' or no extension). The daemon then
   *  stores it as literally "attachment", which — having no extension — is not classified as
   *  an image, so a vision model never receives it as one. Name it from its mime type. */
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

  function text(tag, cls, value) {
    const n = document.createElement(tag)
    n.className = cls
    n.textContent = value
    return n
  }

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
    onReset = opts.onReset || null
    sessionKey = `builder-${Date.now().toString(36)}`
    // The thread starts EMPTY in the markup; the hero is planted here, by the same path a
    // later "new chat" uses. One code path for the empty state, so it cannot differ between
    // the first one you see and every one after it.
    $('thread').append(hero())
    if (onReset) onReset()

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

    // paste: `files` covers a copied FILE, but a copied IMAGE (screenshot tool, "copy image")
    // can arrive ONLY in `items` with `files` empty — read both or pasting a screenshot
    // silently does nothing. A plain text paste falls through untouched.
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
    // No overlay. The only feedback is a tint on the composer's own border, cleared by a
    // self-expiring timer — `dragover` fires continuously while a drag is live, so "no
    // dragover recently" reliably means it ended, however it ended. Counting
    // dragenter/dragleave instead looks correct and is not: they fire per child element, and
    // a drop landing outside the counted subtree never decrements.
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

    // Window level, not a zone: preventDefault is REQUIRED or Electron navigates the whole
    // window to the dropped file:// URL and the UI is replaced by the image.
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

    client.on('chat.event', (p) => {
      if (p.sessionKey === sessionKey) handle(p.event || {})
    })
  }

  /** Wipe the thread back to the empty state.
   *
   *  `onReset` is not optional decoration: the hero's agent picker and suggestions are FILLED
   *  by app.js from the live roster, and cloning the template gives you the empty shells. With
   *  nothing telling app.js to repaint, a new chat showed a picker with no agents in it — the
   *  half of the bug that survived after the two hero copies were merged. */
  function reset() {
    sessionKey = `builder-${Date.now().toString(36)}`
    scope = null           // a fresh chat is about nothing until told
    scopeSent = false
    $('thread').textContent = ''
    $('thread').append(hero())
    node = thinkNode = null
    buf = thinkBuf = ''
    tools.clear()
    stick = true
    if (onSession) onSession(sessionKey)
    if (onReset) onReset()
  }

  /** Open a saved conversation: render its transcript, then keep talking INTO it — the same
   *  sessionKey goes back out on the next send, so the thread continues rather than forking. */
  async function open(key) {
    sessionKey = key
    // a resumed chat already carries its context in message 1 of the transcript
    scope = null
    scopeSent = true
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

  /** The empty state, cloned from the ONE definition in index.html (`#heroTpl`).
   *
   *  This used to build its own copy from an HTML string. The two drifted — the string never
   *  got the agent picker F1 added — so "new chat" quietly produced a hero missing the "work
   *  on an existing agent" choice. A template cannot drift from itself. */
  function hero() {
    const tpl = $('heroTpl')
    return tpl.content.firstElementChild.cloneNode(true)
  }

  function dropHero() {
    const h = $('hero')
    if (h) h.remove()
  }

  /** Point this conversation at an existing agent. `null` clears it (building something new). */
  function setScope(agent) {
    scope = agent
    scopeSent = false
    if (!agent) return
    dropHero()
    const row = document.createElement('div')
    row.className = 'scope-row'
    row.innerHTML =
      `<span class="scope-dot"></span>Working on <b>${MD.esc(agent.name || agent.id)}</b>` +
      `<span class="scope-path">agents/${MD.esc(agent.id)}/</span>`
    $('thread').append(row)
    follow()
  }

  /** What the model is told about the subject, the first time we send.
   *
   *  It goes in the MESSAGE because chat.send has no system/context parameter — and it is
   *  SHOWN, because a client that silently prepends instructions to your words leaves you
   *  unable to explain the model's behaviour later. The last line is load-bearing: without
   *  it, "add pagination to the job finder" has previously been answered by building a
   *  second job finder. */
  function preamble(agent) {
    return (
      `[context] We are working on the EXISTING agent \`${agent.id}\`, which lives at ` +
      `\`agents/${agent.id}/\`. Read its agent.toml, IDENTITY.md, AGENTS.md and any ` +
      `skills/, plugins/ and ui/ before proposing changes, so you are working from what is ` +
      `actually there. Do NOT create a new agent unless I explicitly ask for one.`
    )
  }

  /** What the user just attached, shown on their own bubble — an image as a thumbnail so
   *  they can see WHICH screenshot they sent, anything else as a named chip. */
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
    label.textContent = role === 'user' ? 'You' : 'Agent Builder'
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
      // The scope preamble rides on the FIRST message only — afterwards it is in the
      // transcript, and repeating it every turn would just crowd the context.
      const withScope = scope && !scopeSent ? `${preamble(scope)}\n\n${body}` : body
      // Marked BEFORE the await, not after: everything up to the await runs synchronously, so
      // a flag set afterwards is still false for anything that reaches send() in the same
      // tick, and the preamble goes out twice. The catch below puts the debt back.
      scopeSent = true
      // `message`, not `text` — chat.send reads params.message and rejects an empty one.
      await client.send({
        sessionKey,
        message: withScope,
        ...(sending.length ? { attachments: sending } : {}),
      })
    } catch (e) {
      scopeSent = false   // it never reached the daemon; the retry must still carry it
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
        // EVERY tool, not a list of the ones that write. This used to test the name against
        // /^(write|edit|create_agent|...)$/ — and when scaffold_ui was added nobody extended
        // it, so a whole generated ui/ folder could land with the tree showing none of it.
        // A re-read is a handful of small directory listings; a list you must remember to
        // extend is a bug waiting for the next tool.
        if (onToolDone) onToolDone(ev)
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

  return { init, reset, open, ask, setScope, get sessionKey() { return sessionKey } }
})()
