/* Agent Builder — boot and wiring.

   A pure CLIENT of the daemon: it connects over the same WebSocket any agent app uses, and
   every capability it has is one the daemon granted. Two of those are unusual and worth
   naming, because the rest of this file assumes them:

     * it may READ other agents (roster, detail, files) — the daemon lists agent-builder in
       CROSS_AGENT_READS. Reads only: it can never chat or write AS another agent.
     * it may read and write CONFIG — so the Settings view is the real agentd settings, and
       BYOK works from inside a shipped agent.

   Nothing here has a backend of its own, and never could. That is the platform invariant. */

;(function () {
  const $ = (id) => document.getElementById(id)
  const client = agentd.fromPage()

  let agents = []
  let selected = null
  let view = 'build'

  // ── roster ────────────────────────────────────────────────────────────────
  const COLORS = ['#8b74ff', '#5ec8c0', '#f0a45d', '#e8749b', '#7bb4f2', '#b88bd8']
  function color(a, i) {
    return a.color || COLORS[i % COLORS.length] || '#8b74ff'
  }

  async function loadAgents() {
    try {
      const res = await client.agents()
      agents = (res && res.agents) || []
    } catch {
      agents = []
    }
    drawPicker()
    if (selected && !agents.some((a) => a.id === selected.id)) select(null)
  }

  /** The inspector's agent chooser. The rail belongs to this agent's own conversations, so
   *  picking a DIFFERENT agent to look at is a control on the panel that shows it. */
  function drawPicker() {
    const sel = $('agentPick')
    const keep = selected && selected.id
    sel.textContent = ''
    for (const a of agents) {
      const opt = document.createElement('option')
      opt.value = a.id
      opt.textContent = (a.name || a.id) + (a.app ? '  ·  app' : '')
      sel.append(opt)
    }
    if (keep) sel.value = keep
  }

  // ── chat history (the rail) ───────────────────────────────────────────────
  let chats = []
  let openKey = null

  async function loadChats() {
    try {
      const res = await client.sessions('agent-builder')
      chats = (res && res.sessions) || []
    } catch {
      chats = []
    }
    $('chatCount').textContent = chats.length || ''
    drawChats()
  }

  function when(ts) {
    if (!ts) return ''
    const d = new Date(ts * 1000)
    const days = (Date.now() - d.getTime()) / 86400000
    if (days < 1) return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    if (days < 7) return d.toLocaleDateString([], { weekday: 'short' })
    return d.toLocaleDateString([], { month: 'short', day: 'numeric' })
  }

  function drawChats() {
    const ul = $('chatList')
    ul.textContent = ''
    if (!chats.length) {
      const empty = document.createElement('li')
      empty.className = 'rail-empty'
      empty.textContent = 'No conversations yet.'
      ul.append(empty)
      return
    }
    for (const c of chats) {
      const li = document.createElement('li')
      li.className = `chat-row ${c.sessionId === openKey ? 'on' : ''}`
      li.title = c.snippet || c.title || c.sessionId

      const meta = document.createElement('div')
      meta.className = 'agent-meta'
      const top = document.createElement('div')
      top.className = 'chat-top'
      const name = document.createElement('span')
      name.className = 'agent-name'
      name.textContent = c.title || 'Untitled'
      const ts = document.createElement('span')
      ts.className = 'chat-when'
      ts.textContent = when(c.modified)
      top.append(name, ts)
      const sub = document.createElement('div')
      sub.className = 'agent-sub'
      sub.textContent = c.snippet || `${c.messages || 0} messages`
      meta.append(top, sub)

      li.append(meta)
      li.addEventListener('click', () => {
        openKey = c.sessionId
        drawChats()
        void Chat.open(c.sessionId)
      })
      ul.append(li)
    }
  }

  // ── selection → inspector ─────────────────────────────────────────────────
  function select(a) {
    selected = a
    if (a) $('agentPick').value = a.id
    // The crumb names the agent being INSPECTED, not a chat target — the conversation is
    // always with Agent Builder. "· inspecting X" instead of "/ X", which read like a
    // breadcrumb into a different chat.
    $('crumbSep').hidden = !a
    $('crumbLeaf').textContent = a ? `inspecting ${a.name || a.id}` : ''
    $('panelActions').hidden = !a
    $('panelSub').textContent = a
      ? [a.tagline || a.description || a.id, a.version && `v${a.version}`]
          .filter(Boolean).join('  ·  ')
      : '—'
    if (!a) { $('tree').textContent = ''; $('fileTabs').hidden = true; return }
    void Files.select(a.id)
  }

  $('agentPick').addEventListener('change', (e) => {
    select(agents.find((a) => a.id === e.target.value) || null)
  })

  // ── panel actions ─────────────────────────────────────────────────────────
  function showOut(title, text, cls) {
    $('panelOut').hidden = false
    $('outTitle').textContent = title
    const body = $('outBody')
    body.className = cls || ''
    body.textContent = text
  }

  async function runTool(tool, title) {
    if (!selected) return
    showOut(title, `running ${tool} on ${selected.id}…`)
    for (const b of [$('btnValidate'), $('btnPackage')]) b.disabled = true
    try {
      const res = await client.invokeTool(tool, { agent_id: selected.id })
      showOut(title, agentd.resultText(res) || '(no output)')
      void Files.refresh()
    } catch (e) {
      // the daemon throws with the tool's own report text when a tool reports an error —
      // that IS the result, so show it rather than a generic failure line
      showOut(title, String((e && e.message) || e), 'bad')
    } finally {
      for (const b of [$('btnValidate'), $('btnPackage')]) b.disabled = false
    }
  }

  $('btnValidate').addEventListener('click', () => void runTool('validate_agent', 'Validation'))
  $('btnPackage').addEventListener('click', () => void runTool('package_agent', 'Package'))
  $('outClear').addEventListener('click', () => { $('panelOut').hidden = true })

  // ── views ─────────────────────────────────────────────────────────────────
  function show(next) {
    view = next
    $('view-build').hidden = next !== 'build'
    $('view-settings').hidden = next !== 'settings'
    for (const b of document.querySelectorAll('.nav-item')) {
      b.classList.toggle('active', b.dataset.view === next)
    }
    $('crumbRoot').textContent = next === 'settings' ? 'Settings' : 'Agent Builder'
    if (next === 'settings') void Settings.load()
  }
  for (const b of document.querySelectorAll('.nav-item')) {
    b.addEventListener('click', () => show(b.dataset.view))
  }
  $('settingsBtn').addEventListener('click', () => show(view === 'settings' ? 'build' : 'settings'))

  // ── chrome toggles ────────────────────────────────────────────────────────
  $('railToggle').addEventListener('click', () => {
    const shell = document.querySelector('.shell')
    shell.classList.toggle('no-rail')
    $('railToggle').textContent = shell.classList.contains('no-rail') ? '›' : '‹'
  })
  $('panelToggle').addEventListener('click', () =>
    document.querySelector('.shell').classList.toggle('no-panel'))

  $('newAgent').addEventListener('click', () => {
    show('build')
    Chat.reset()
    $('input').focus()
  })

  // ── suggestions ───────────────────────────────────────────────────────────
  const SUGGESTIONS = [
    'Build an agent that summarises my YouTube history and charts it by month',
    'Give the weather agent its own app window',
    'Validate every agent I have and tell me what is wrong',
  ]
  function drawSuggests() {
    const box = $('suggests')
    if (!box) return
    box.textContent = ''
    for (const s of SUGGESTIONS) {
      const b = document.createElement('button')
      b.className = 'suggest'
      b.textContent = s
      b.addEventListener('click', () => Chat.ask(s))
      box.append(b)
    }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  Files.init(client)
  Settings.init(client)
  Chat.init(client, {
    // a build wrote something — re-read the tree so the new file shows up (and flashes)
    onToolDone: () => { if (selected) void Files.refresh() },
    // which conversation is live, so the rail highlights it; a brand-new one has no row yet
    onSession: (key) => { openKey = key; drawChats() },
  })
  drawSuggests()

  let started = false
  client.onStatus((s) => {
    const el = $('status')
    el.className = `status ${s === 'open' ? 'live' : s === 'closed' ? 'down' : ''}`
    el.textContent = s === 'open' ? 'connected' : s === 'closed' ? 'disconnected' : s
    if (s === 'open' && !started) {
      started = true
      void (async () => {
        try {
          const hello = await client.hello()
          $('daemonVer').textContent = hello && hello.version ? `v${hello.version}` : ''
        } catch { /* advisory only */ }
        await loadAgents()
        // select agent-builder itself so the panel is never empty on open
        select(agents.find((a) => a.id === 'agent-builder') || agents[0] || null)
        await loadChats()
      })()
    }
  })

  // the roster changes when an agent is created, reloaded, or installed — reload_agent
  // broadcasts this, so the picker stays current with no polling
  client.on('agents.changed', () => void loadAgents())
  // and the rail follows the conversation list: a new chat gets its auto-title a moment
  // after the first exchange, which arrives as sessions.changed rather than on the run
  client.on('sessions.changed', () => void loadChats())
})()
