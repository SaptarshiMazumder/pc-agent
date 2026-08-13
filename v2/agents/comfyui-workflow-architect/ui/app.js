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
  let artifactsCache = []  // last known workspace listing

  // ── suggestions ───────────────────────────────────────────────────────────
  // Concrete workflow requests; each chip sends its text as a new request.
  const SUGGESTIONS = [
    'Build a FLUX text-to-image workflow for 12 GB VRAM.',
    'Create a Z-Image Turbo workflow and verify the model files.',
    'Design an image-to-video pipeline using currently available models.',
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
    $('view-artifacts').hidden = next !== 'artifacts'
    $('view-settings').hidden = next !== 'settings'
    for (const b of document.querySelectorAll('.nav-item')) {
      b.classList.toggle('active', b.dataset.view === next)
    }
    const label = next === 'artifacts' ? 'Artifacts' : next === 'settings' ? 'Settings' : 'Chat'
    $('crumbRoot').textContent = label
    if (next === 'artifacts') void loadArtifacts()
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

  // ── artifacts ─────────────────────────────────────────────────────────────
  async function loadArtifacts() {
    const body = $('artifactsBody')
    body.textContent = ''
    body.append(loadingEl('loading artifacts…'))
    try {
      artifactsCache = await listArtifactTree('')
      drawArtifacts()
    } catch (e) {
      body.textContent = ''
      body.append(errorEl('Could not load artifacts. ' + ((e && e.message) || e)))
    }
  }

  async function listArtifactTree(path, depth = 0) {
    // workspace.list is lazy: it returns {entries:[{name, rel, kind, size}]} for ONE folder.
    // Recurse so nested workflow revisions and manifests are visible in a single useful list.
    if (depth > 12) return []
    const res = await client.request('workspace.list', path ? { path } : {})
    if (res && res.error) throw new Error(res.error)
    const entries = (res && res.entries) || []
    const out = []
    for (const entry of entries) {
      const rel = entry.rel || (path ? `${path}/${entry.name}` : entry.name)
      const normalized = { ...entry, rel, path: rel, isDir: entry.kind === 'folder' }
      // Scratch and upload inputs are not generated deliverables.
      if (depth === 0 && ['tmp', 'uploads'].includes(entry.name)) continue
      if (normalized.isDir) out.push(...await listArtifactTree(rel, depth + 1))
      else out.push(normalized)
    }
    return out
  }

  function iconFor(name) {
    const ext = String(name).split('.').pop().toLowerCase()
    if (['json','jsonc'].includes(ext)) return '📋'
    if (['md','txt','log','yml','yaml','toml','cfg','ini'].includes(ext)) return '📄'
    if (['png','jpg','jpeg','gif','webp','bmp','svg','ico'].includes(ext)) return '🖼'
    if (['mp4','mov','avi','webm','mkv','gif'].includes(ext)) return '🎬'
    if (['py','js','ts','html','css'].includes(ext)) return '⌨'
    return '📦'
  }

  function drawArtifacts() {
    const body = $('artifactsBody')
    body.textContent = ''
    const files = artifactsCache || []
    if (!files.length) {
      body.append(emptyEl('No artifacts yet. Generated workflows and manifests will appear here.'))
      return
    }
    const sorted = [...files].sort((a, b) => (a.rel || a.name || '').localeCompare(b.rel || b.name || ''))
    const list = document.createElement('ul')
    list.className = 'artifacts-list'
    for (const f of sorted) {
      const li = document.createElement('li')
      li.className = 'artifacts-row'
      const icon = document.createElement('span')
      icon.className = 'a-icon'
      icon.textContent = iconFor(f.name || '')
      const text = document.createElement('div')
      text.className = 'a-text'
      const name = document.createElement('span')
      name.className = 'a-name'
      name.textContent = f.name || f.rel || ''
      const path = document.createElement('span')
      path.className = 'a-path'
      path.textContent = f.rel || ''
      text.append(name, path)

      // Open-in-Explorer button — invokes the agent's exec tool
      const openBtn = document.createElement('button')
      openBtn.className = 'a-open'
      openBtn.textContent = 'Open'
      openBtn.title = 'Open in Explorer'
      openBtn.addEventListener('click', async (e) => {
        e.stopPropagation()
        openBtn.disabled = true
        openBtn.textContent = '…'
        try {
          const safeRel = String(f.rel || '').replace(/'/g, "''")
          const cmd = `powershell -NoProfile -Command "explorer /select,(Resolve-Path '${safeRel}')"`
          await client.invokeTool('exec', { command: cmd, timeout_sec: 10 })
        } catch (err) {
          console.error('open_in_explorer failed:', err)
          openBtn.textContent = '✗'
          setTimeout(() => { openBtn.textContent = 'Open'; openBtn.disabled = false }, 1500)
          return
        }
        openBtn.textContent = 'Open'
        openBtn.disabled = false
      })

      li.append(icon, text, openBtn)
      list.append(li)
    }
    body.append(list)
  }

  function loadingEl(text) { const d = document.createElement('div'); d.className = 'loading'; d.textContent = text; return d }
  function emptyEl(text) { const d = document.createElement('div'); d.className = 'artifacts-empty'; d.textContent = text; return d }
  function errorEl(text) { const d = document.createElement('div'); d.className = 'artifacts-error'; d.textContent = text; return d }

  // ── boot ──────────────────────────────────────────────────────────────────
  Settings.init(client)
  Chat.init(client, {
    onToolDone: () => {
      // A write/edit/exec may have changed the workspace. Refresh immediately when the panel
      // is open; otherwise the next click loads a fresh listing.
      artifactsCache = []
      if (view === 'artifacts') void loadArtifacts()
    },
    onSession: (key) => { openKey = key; drawChats() },
  })
  drawSuggests()

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
