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

  let seenIds = null   // null until the first load, so a cold start is not "everything is new"

  async function loadAgents() {
    try {
      const res = await client.agents()
      agents = (res && res.agents) || []
    } catch {
      agents = []
    }
    // An agent that did not exist a moment ago was just BUILT — in this window, by this
    // conversation. Focus it, because watching its files appear is what the panel is for and
    // making the user go and pick it would be asking them to find what they just asked for.
    const ids = new Set(openable().map((a) => a.id))
    if (seenIds && !selected) {
      const born = [...ids].filter((id) => !seenIds.has(id))
      if (born.length === 1) select(agents.find((a) => a.id === born[0]) || null)
    }
    seenIds = ids

    drawScopePicker()
    if (selected && !ids.has(selected.id)) select(null)
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

  // ── focus → the panel ─────────────────────────────────────────────────────
  /** Point the panel at an agent, or at nothing.
   *
   *  With nothing focused the tree is EMPTY and says what to do, rather than falling back to
   *  showing something. There is no "default agent to look at" — the panel is about the agent
   *  being built, and before one is chosen there is no answer. */
  function select(a) {
    selected = a
    // NOTHING IN FOCUS -> NO PANEL. Not an empty panel with a placeholder in it: there is no
    // question for the panel to answer yet, and a column of dashes is furniture.
    document.querySelector('.shell').classList.toggle('no-panel', !a)
    $('panelToggle').hidden = !a
    // The crumb names what the conversation is working on. The chat is always WITH Agent
    // Builder, so "· building X" rather than "/ X", which read like a breadcrumb into
    // someone else's thread.
    $('crumbSep').hidden = !a
    $('crumbLeaf').textContent = a ? `building ${a.name || a.id}` : ''
    $('panelAgent').textContent = a ? (a.name || a.id) : ''
    $('panelSub').textContent = a
      ? [a.tagline || a.description || a.id, a.version && `v${a.version}`]
          .filter(Boolean).join('  ·  ')
      : ''
    syncActions()
    void Files.select(a ? a.id : null)
  }

  // ── panel actions ─────────────────────────────────────────────────────────
  function showOut(title, text, cls) {
    $('panelOut').hidden = false
    $('outTitle').textContent = title
    const body = $('outBody')
    body.className = cls || ''
    body.textContent = text
  }

  const ACTION_BTNS = () => [$('btnValidate'), $('btnPackage'), $('btnPublish')]

  /* Publish is OWNERSHIP-gated. The daemon marks each agents.list row with `mine` (is it the
     caller's) and `origin` (authored | installed | curated). A catalogue agent stays fully
     openable, but publishing it would upload someone else's work under this user's creator
     identity; an INSTALLED agent is theirs to use but its author is the only one who can ship
     a new version. The tool refuses both server-side — the button just says so up front
     instead of letting a click discover it. Checks are exact (`=== false`, exact strings),
     never falsy: an older daemon sends neither field, and greying every Publish on that
     absence would turn a missing feature into a broken one. */
  function publishable(a) {
    return !!a && a.mine !== false && a.origin !== 'installed' && a.origin !== 'curated'
  }

  function publishBlockReason(a) {
    if (a && a.mine === false) {
      return 'Part of this deployment, not your agent — agents you create here are publishable.'
    }
    return 'Installed from the marketplace — only its author can publish a new version.'
  }

  function syncActions() {
    const btn = $('btnPublish')
    btn.disabled = !publishable(selected)
    btn.title = publishable(selected) ? 'Publish to the marketplace' : publishBlockReason(selected)
  }

  async function runTool(tool, title, extra) {
    if (!selected) return null
    showOut(title, `running ${tool} on ${selected.id}…`)
    for (const b of ACTION_BTNS()) b.disabled = true
    try {
      const res = await client.invokeTool(tool, { agent_id: selected.id, ...(extra || {}) })
      const text = agentd.resultText(res) || '(no output)'
      showOut(title, text)
      void Files.refresh()
      return text
    } catch (e) {
      // the daemon throws with the tool's own report text when a tool reports an error —
      // that IS the result, so show it rather than a generic failure line
      showOut(title, String((e && e.message) || e), 'bad')
      return null
    } finally {
      for (const b of ACTION_BTNS()) b.disabled = false
      syncActions() // never re-enable Publish past the ownership gate
    }
  }

  /* Publish is TWO steps on purpose.
     A publish uploads a public artifact and rewrites the registry index every client reads, so
     one click must not be enough. The first call is the tool's default dry run: it prints the
     exact index that would be published — which is where you notice a bundle you did not expect
     is about to change. Only then do we ask, and only a yes sends dry_run=false + confirm=true
     (the tool requires BOTH, so nothing here can publish by accident either).
     A dry run that FAILED returns null — usually "not configured to publish", already on screen
     — and we stop rather than asking the user to confirm something that cannot work. */
  async function publishFlow() {
    if (!publishable(selected)) return // the button is disabled; this guards a stale handler
    const preview = await runTool('publish_agent', 'Publish — preview', { dry_run: true })
    if (preview === null) return
    const ok = window.confirm(
      `Publish ${selected.id} to the marketplace?\n\n` +
        'This uploads a PUBLIC artifact and rewrites the registry index.\n' +
        'Check the preview behind this dialog first — it lists every bundle that will be in the ' +
        'published index.'
    )
    if (!ok) {
      showOut('Publish', 'cancelled — nothing was uploaded.\n\n' + preview)
      return
    }
    await runTool('publish_agent', 'Publish', { dry_run: false, confirm: true })
  }

  // The tree re-reads after every tool, but a file can change from outside this window too.
  $('btnRefresh').addEventListener('click', () => void Files.refresh())

  $('btnValidate').addEventListener('click', () => void runTool('validate_agent', 'Validation'))
  $('btnPackage').addEventListener('click', () => void runTool('package_agent', 'Package'))
  $('btnPublish').addEventListener('click', () => void publishFlow())
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
  // ── chrome toggles ────────────────────────────────────────────────────────
  $('railToggle').addEventListener('click', () => {
    const shell = document.querySelector('.shell')
    shell.classList.toggle('no-rail')
    $('railToggle').textContent = shell.classList.contains('no-rail') ? '›' : '‹'
  })
  $('panelToggle').addEventListener('click', () => {
    if (!selected) return   // nothing to show; the button is hidden anyway
    document.querySelector('.shell').classList.toggle('no-panel')
  })

  // ONE handler for the ONE button. chat.js used to wire a second listener to a second
  // button under a different name ("New agent" here, "New chat" in the topbar) — same action,
  // two names, and only one of them switched the view.
  $('newChat').addEventListener('click', () => {
    show('build')
    Chat.reset()        // fires onReset -> drawSuggests() repaints the hero
    $('input').focus()
  })

  // ── suggestions ───────────────────────────────────────────────────────────
  // Starter prompts. NEVER name a specific agent — these are clickable, and on a machine that
  // never had one, the click asks to work on something that does not exist.
  const SUGGESTIONS = [
    'Build an agent that summarises my YouTube history and charts it by month',
    'Give one of my agents its own app window',
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
    drawScopePicker()
  }

  // ── what is this conversation about? ──────────────────────────────────────
  // Offered ONLY when there is something to open. On a fresh install the user has nothing
  // but Agent Builder itself, and asking "existing or new?" with one meaningless answer is
  // ceremony — so the whole block stays hidden and the chat behaves exactly as before.
  function openable() {
    return agents.filter((a) => a.id !== 'agent-builder')
  }

  function drawScopePicker() {
    const wrap = $('pickScope')
    const sel = $('scopePick')
    if (!wrap || !sel) return
    const list = openable()
    wrap.hidden = list.length === 0
    if (!list.length) return
    sel.textContent = ''
    for (const a of list) {
      const opt = document.createElement('option')
      opt.value = a.id
      // A catalogue agent (mine === false) is still openable — chat, files, validate — it just
      // is not the user's to publish. Said here, in the list, so the greyed Publish button is
      // never the first time they find out.
      opt.textContent = (a.name || a.id) + (a.mine === false ? ' · catalogue' : '')
      sel.append(opt)
    }
    // BOUND HERE, not once at boot: "new chat" clones a fresh hero out of the template, so a
    // listener attached to the old Open button is attached to a node that no longer exists —
    // the button would render and do nothing. `onclick =` rather than addEventListener so
    // redrawing on a roster change cannot stack duplicates.
    $('scopeGo').onclick = () => openScoped($('scopePick').value)
  }

  function openScoped(id) {
    const agent = agents.find((a) => a.id === id)
    if (!agent) return
    select(agent)              // the inspector follows the conversation
    Chat.setScope(agent)       // and the model is told what it is looking at
    $('input').focus()
  }

  /* WHO AM I — the identity THIS WINDOW's connection carries, which is the identity a Publish
     would be signed with. Asked of the daemon (platform.status answers per connection), never
     read from localStorage: a stored session and the socket's session can disagree — after a
     reconnect, or when the shell signed in but this window's socket didn't — and showing the
     stored one would name the wrong publisher. Hidden entirely when the daemon is too old to
     answer; "not signed in" only when the daemon SAID so. */
  async function showWhoAmI() {
    const el = $('whoAmI')
    if (!el) return
    try {
      const s = await client.request('platform.status')
      el.hidden = false
      if (s && s.signedIn) {
        el.textContent = s.email || s.accountId
        el.title = `Signed in — publishes are attributed to ${s.email || s.accountId}` +
          (s.mode ? ` (${s.mode} mode)` : '')
        el.classList.remove('anon')
      } else {
        el.textContent = 'not signed in'
        el.title = 'This window has no account — publishing will ask you to sign in.'
        el.classList.add('anon')
      }
    } catch {
      el.hidden = true // daemon predates platform.status on app sockets — say nothing, not wrong
    }
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  Files.init(client)
  Settings.init(client)
  Chat.init(client, {
    // a build wrote something — re-read the tree so the new file shows up (and flashes)
    onToolDone: () => { if (selected) void Files.refresh() },
    // The conversation CHANGED — a new one, or a saved one reopened. Focus belongs to a
    // conversation, so it goes with it: carrying the last chat's agent into the next one is
    // the drift this panel is not allowed to have. A new chat re-chooses in the hero.
    onSession: (key) => { openKey = key; drawChats(); select(null) },
    // the hero was just re-planted from the template, so its picker and suggestions are empty
    // shells — this is what puts the live roster back into them
    onReset: () => drawSuggests(),
  })

  let started = false
  client.onStatus((s) => {
    const el = $('status')
    el.className = `status ${s === 'open' ? 'live' : s === 'closed' ? 'down' : ''}`
    el.textContent = s === 'open' ? 'connected' : s === 'closed' ? 'disconnected' : s
    // EVERY open, not just the first: signing in re-dials the socket with the new session,
    // and the identity chip must follow the connection it describes.
    if (s === 'open' && started) void showWhoAmI()
    if (s === 'open' && !started) {
      started = true
      void (async () => {
        // AGENTD:COMPONENTS — add_ui_component inserts after this line. Keep the marker.
        try {
          // Hosted sign-in. Renders NOTHING on a BYOK build, when this device is already connected, or
          // when a stored session still works — so it is safe to call unconditionally.
          await agentd.mountSignInGate()
        } catch (e) {
          // The daemon itself is unreachable. Not fatal: the chat surface reports that too, and blocking
          // the whole window on a status probe would hide the better message.
          console.warn('[sign-in]', (e && e.message) || e)
        }
        try {
          const hello = await client.hello()
          $('daemonVer').textContent = hello && hello.version ? `v${hello.version}` : ''
        } catch { /* advisory only */ }
        await showWhoAmI()
        await loadAgents()
        // Nothing is focused on open. This used to select agent-builder itself "so the panel
        // is never empty" — which meant the window booted showing you its own guts instead of
        // the agent you came to build. An empty panel that says why is the honest state.
        select(null)
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
