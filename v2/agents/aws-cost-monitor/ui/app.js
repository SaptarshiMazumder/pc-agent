/* Boot and wiring for a DASHBOARD agent.

   Same client as the chat template — one WebSocket, `scope=<agent-id>` on the URL, the daemon
   forcing this agent onto every request — but the window opens on numbers instead of a composer.

   THE PART WORTH UNDERSTANDING is the last line of `Chat.init`: when a tool finishes IN
   CONVERSATION, the board reloads. So asking "rebalance the portfolio" updates the tiles without
   anyone pressing Refresh, and the screen never disagrees with what the agent just did. A
   dashboard that only refreshes on its own button is a dashboard that lies after every chat. */

;(function () {
  const $ = (id) => document.getElementById(id)
  const client = agentd.fromPage()

  // Display only — never send it as a parameter. The daemon already knows whose window this is.
  const AGENT_ID = (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '')

  let view = 'board'

  // Openers for the Ask view. Keep them about adding/changing what is on screen.
  const SUGGESTIONS = [
    'Add Lambda and RDS to the dashboard',
    'Set a cost threshold for EC2 at $100',
    'Check now and alert me if anything is over its limit',
  ]

  // The DEFAULT resources to load on open, before the user has asked for anything. This is
  // the instruction the agent runs in a background chat turn, not a tool call.
  const BOOTSTRAP = (
    'Fetch my AWS costs month-to-date for the default resources (EC2, ECS/Fargate, Lambda, ' +
    'S3, and RDS): per-service and per-resource totals plus daily totals for the graphs, then ' +
    'save the snapshot. Do not answer me in prose — just populate the dashboard.'
  )

  function drawSuggests() {
    const box = $('suggests')
    if (!box) return
    box.textContent = ''
    for (const s of SUGGESTIONS) {
      const b = document.createElement('button')
      b.className = 'suggest'
      b.textContent = s
      b.addEventListener('click', () => { show('chat'); Chat.ask(s) })
      box.append(b)
    }
  }

  /** Is there anything to show yet? `get_cost_dashboard` returns an empty rows/tiles/series
   *  when no snapshot is stored. We use that to decide whether to auto-populate on open. */
  async function boardIsEmpty() {
    try {
      const res = await client.invokeTool('get_cost_dashboard', {})
      const data = JSON.parse(agentd.resultText(res) || '{}')
      return !(data.tiles && data.tiles.length) && !(data.rows && data.rows.data && data.rows.data.length)
    } catch {
      return false // on any error, let the normal Board.load() surface it
    }
  }

  /** Populate the dashboard on open, without showing the chat view: run ONE background chat
   *  turn (the agent fetches AWS costs and saves the snapshot), and reload the board when that
   *  run ends. Uses a dedicated session so the user's Ask thread stays clean. */
  async function autoPopulate() {
    if (await boardIsEmpty() === false) return
    Board.showLoading('Loading your AWS costs…')
    const key = 'boot-' + Date.now().toString(36)
    const off = client.onRun(key, (payload) => {
      if (payload && payload.event && payload.event.type === 'agent_end') {
        off()
        void Board.load()
      }
    })
    try {
      await client.send({ sessionKey: key, message: BOOTSTRAP })
    } catch (e) {
      Board.showLoading('Could not auto-load costs: ' + ((e && e.message) || e))
      void Board.load()
    }
  }

  // ── conversation history ──────────────────────────────────────────────────
  let chats = []
  let openKey = null

  async function loadChats() {
    try {
      const res = await client.sessions()
      chats = (res && res.sessions) || []
    } catch {
      chats = []
    }
    const count = $('chatCount')
    if (count) count.textContent = chats.length || ''
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
    if (!ul) return
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

      const top = document.createElement('div')
      top.className = 'chat-top'
      const name = document.createElement('span')
      name.className = 'chat-name'
      name.textContent = c.title || 'Untitled'
      const ts = document.createElement('span')
      ts.className = 'chat-when'
      ts.textContent = when(c.modified)
      top.append(name, ts)

      const sub = document.createElement('div')
      sub.className = 'chat-sub'
      sub.textContent = c.snippet || `${c.messages || 0} messages`

      li.append(top, sub)
      li.addEventListener('click', () => {
        openKey = c.sessionId
        drawChats()
        show('chat')
        void Chat.open(c.sessionId)
      })
      ul.append(li)
    }
  }

  // ── views ─────────────────────────────────────────────────────────────────
  function show(next) {
    view = next
    $('view-board').hidden = next !== 'board'
    $('view-chat').hidden = next !== 'chat'
    $('view-settings').hidden = next !== 'settings'
    for (const b of document.querySelectorAll('.nav-item')) {
      b.classList.toggle('active', b.dataset.view === next)
    }
    $('crumbRoot').textContent =
      next === 'settings' ? 'Settings' : next === 'chat' ? 'Ask' : 'Dashboard'
    if (next === 'settings') void Settings.load()
  }
  for (const b of document.querySelectorAll('.nav-item')) {
    b.addEventListener('click', () => show(b.dataset.view))
  }
  // Also in the topbar: the rail collapses on a narrow window, and settings must never become
  // unreachable — an unset API key would be an unfixable dead end.
  $('settingsBtn').addEventListener('click', () => show(view === 'settings' ? 'board' : 'settings'))

  $('railToggle').addEventListener('click', () => {
    const shell = document.querySelector('.shell')
    shell.classList.toggle('no-rail')
    $('railToggle').textContent = shell.classList.contains('no-rail') ? '›' : '‹'
  })

  // ── boot ──────────────────────────────────────────────────────────────────
  Settings.init(client)
  Board.init(client)
  Chat.init(client, {
    // THE LINE THAT KEEPS THE SCREEN HONEST — see the file header.
    onToolDone: () => void Board.load(),
    onSession: (key) => { openKey = key; drawChats() },
  })
  drawSuggests()

  let started = false
  client.onStatus((s) => {    // Connection state, always visible: when the daemon goes away, a board that simply stops
    // updating is unexplainable, and this is the explanation.
    const el = $('status')
    el.className = `status ${s === 'open' ? 'live' : s === 'closed' ? 'down' : ''}`
    el.textContent = s === 'open' ? 'connected' : s === 'closed' ? 'disconnected' : s

    if (s === 'open' && !started) {
      started = true
      void (async () => {
        // AGENTD:COMPONENTS — `add_ui_component` inserts after this line.
        try {
          // Renders nothing on a BYOK build or when a stored session still works, so it is safe
          // to call unconditionally.
          await agentd.mountSignInGate()
        } catch (e) {
          console.warn('[sign-in]', (e && e.message) || e)
        }
        try {
          const hello = await client.hello()
          $('daemonVer').textContent = hello && hello.version ? `v${hello.version}` : ''
        } catch { /* advisory only */ }
        try {
          const me = await client.agentDetail()
          const agent = (me && me.agent) || me || {}
          const label = agent.name || AGENT_ID
          if (label) {
            $('brandName').textContent = label
            document.title = label
          }
        } catch { /* the brand falls back to what index.html already says */ }
        await loadChats()
        Board.start()
        // AUTO-POPULATE: the window opens on the dashboard, so make it show data immediately
        // instead of an empty board that waits for a chat message.
        void autoPopulate()
      })()
    }
  })

  // The history rail follows the session list: a new chat gets its auto-title a moment after
  // the first exchange, which arrives as sessions.changed rather than on the run itself.
  client.on('sessions.changed', () => void loadChats())
})()
