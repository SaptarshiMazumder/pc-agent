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

  // CHANGE ME — openers for the Ask view. Keep them about the numbers on screen.
  const SUGGESTIONS = []

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
      next === 'settings' ? 'Settings' : next === 'chat' ? 'Ask' : 'Overview'
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
    onSession: () => {},
  })
  drawSuggests()

  let started = false
  client.onStatus((s) => {
    // Connection state, always visible: when the daemon goes away, a board that simply stops
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
        Board.start()
      })()
    }
  })
})()
