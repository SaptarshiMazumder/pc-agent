/* Boot and wiring.

   This page is a pure CLIENT of the daemon. It has no backend of its own and never could —
   it connects over the same WebSocket every agent app uses, and every capability it has is
   one the daemon granted to a `scope=<agent-id>` connection.

   `agentd.fromPage()` reads the `token` and `scope` the opener put in the page URL and
   connects. From then on the daemon FORCES this agent onto every request, which is why no
   agent id is written anywhere in this app: `sessions()`, `history()` and `send()` are
   already about this agent, and a hardcoded id would just be a second copy to keep in sync
   with agent.toml.

   ADD YOUR AGENT'S OWN SURFACE HERE. The chat and the settings are done. If this agent has
   a tool worth a button, call it with:

       const res = await client.invokeTool('my_tool', { some: 'arg' })
       showSomething(agentd.resultText(res))

   and refresh that surface from Chat's `onToolDone`, so the screen follows what the agent
   does in conversation as well as what the button does. */

;(function () {
  const $ = (id) => document.getElementById(id)
  const client = agentd.fromPage()

  // Who this window belongs to. Only ever used for DISPLAY — never send it as a parameter.
  // The URL carries `scope=agent:<id>`; the daemon strips that prefix, so we do too.
  const AGENT_ID = (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '')

  let view = 'chat'

  // ── suggestions ───────────────────────────────────────────────────────────
  // CHANGE ME. Openers for someone who has just opened this window and does not yet know
  // what to ask. Keep them concrete and keep them true — each one is a click that sends.
  const SUGGESTIONS = []

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

  // ── conversation history ──────────────────────────────────────────────────
  let chats = []
  let openKey = null

  async function loadChats() {
    try {
      // no agentId — the daemon scopes this to our own agent
      const res = await client.sessions()
      chats = (res && res.sessions) || []
    } catch {
      // history is a convenience, not the app: an empty rail is honest and the chat works
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
      // THE EMPTY STATE. Every list needs one; without it a working app looks broken.
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
        void Chat.open(c.sessionId)
      })
      ul.append(li)
    }
  }

  // ── views ─────────────────────────────────────────────────────────────────
  function show(next) {
    view = next
    $('view-chat').hidden = next !== 'chat'
    $('view-settings').hidden = next !== 'settings'
    for (const b of document.querySelectorAll('.nav-item')) {
      b.classList.toggle('active', b.dataset.view === next)
    }
    $('crumbRoot').textContent = next === 'settings' ? 'Settings' : 'Chat'
    if (next === 'settings') void Settings.load()
  }
  for (const b of document.querySelectorAll('.nav-item')) {
    b.addEventListener('click', () => show(b.dataset.view))
  }
  // also in the rail, but the rail collapses on a narrow window and settings must never
  // become unreachable — without it, an unset API key is an unfixable dead end
  $('settingsBtn').addEventListener('click', () => show(view === 'settings' ? 'chat' : 'settings'))

  $('railToggle').addEventListener('click', () => {
    const shell = document.querySelector('.shell')
    shell.classList.toggle('no-rail')
    $('railToggle').textContent = shell.classList.contains('no-rail') ? '›' : '‹'
  })

  // ── boot ──────────────────────────────────────────────────────────────────
  Settings.init(client)
  Chat.init(client, {
    onToolDone: () => { /* a tool finished — refresh anything this page shows about it */ },
    onSession: (key) => { openKey = key; drawChats() },
  })
  drawSuggests()

  // AGENTD:SIGNIN — `add_ui_component` places the sign-in gate after this line, and it must stay
  // ABOVE the connection wiring below. Gating sign-in on `status === 'open'` deadlocks a page
  // opened from a marketplace card: on a hosted daemon the socket is refused until a session
  // exists, so a form that waits for the socket can never appear. This one talks plain HTTP.
  void (async () => {
    try {
      // Sign-in BEFORE the socket. On a hosted daemon the session token is the socket credential,
      // so a page opened from a marketplace link cannot connect until somebody has signed in.
      // Renders NOTHING on a BYOK build, when the page already carries a credential, or when a
      // stored session still works. `client` lets the gate reconnect once a session exists.
      await agentd.mountSignInGate({ client })
    } catch (e) {
      // The daemon itself is unreachable. Not fatal: the status chip reports that too.
      console.warn('[sign-in]', (e && e.message) || e)
    }
  })()

  let started = false
  client.onStatus((s) => {
    // CONNECTION STATE, always visible. 'connecting' | 'open' | 'closed'. When the daemon
    // goes away, a chat that just stops responding is unexplainable; this is the explanation.
    const el = $('status')
    el.className = `status ${s === 'open' ? 'live' : s === 'closed' ? 'down' : ''}`
    el.textContent = s === 'open' ? 'connected' : s === 'closed' ? 'disconnected' : s

    if (s === 'open' && !started) {
      started = true
      void (async () => {
        // AGENTD:COMPONENTS — `add_ui_component` inserts after this line. Anything it adds runs
        // before the first model call and after the socket is open, which is what every component
        // so far needs. Keep the marker: without it the tool cannot place code deterministically
        // and falls back to telling a human where to put it.
        try {
          const hello = await client.hello()
          $('daemonVer').textContent = hello && hello.version ? `v${hello.version}` : ''
        } catch { /* advisory only — the version is not worth failing the boot over */ }
        try {
          // no argument: scoped to our own agent, so this is THIS agent's card
          const me = await client.agentDetail()
          const agent = (me && me.agent) || me || {}
          const label = agent.name || AGENT_ID
          if (label) {
            $('brandName').textContent = label
            document.title = label
          }
        } catch { /* the brand falls back to what index.html already says */ }
        await loadChats()
      })()
    }
  })

  // the rail follows the conversation list: a new chat gets its auto-title a moment after
  // the first exchange, which arrives as sessions.changed rather than on the run itself
  client.on('sessions.changed', () => void loadChats())
})()
