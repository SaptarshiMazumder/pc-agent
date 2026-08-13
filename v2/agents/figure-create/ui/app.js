/* Boot and wiring for Bio Figure.
 * Manages the file browser (left panel), initializes the canvas editor, 
 * handles template selection, and integrates with the chat. */

;(function () {
  const $ = (id) => document.getElementById(id)
  const client = agentd.fromPage()
  const AGENT_ID = (new URL(location.href).searchParams.get('scope') || '').replace(/^agent:/, '')
  let view = 'chat'

  // ── suggestions ───────────────────────────────────────────────────────────
  const SUGGESTIONS = [
    'The stages of mitosis, clean shaded style',
    'A labeled cross-section of a plant leaf',
    'How mRNA vaccines work, as a flowchart',
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

  // ── views ─────────────────────────────────────────────────────────────────
  function show(next) {
    view = next
    $('view-chat').hidden = next !== 'chat'
    $('view-settings').hidden = next !== 'settings'
    if (next === 'settings') void Settings.load()
  }
  
  $('settingsBtn').addEventListener('click', () => show(view === 'settings' ? 'chat' : 'settings'))

  // ── history drawer ────────────────────────────────────────────────────────
  let chats = []
  let openKey = null

  $('historyToggle').addEventListener('click', () => $('chatRail').classList.add('open'))
  $('railClose').addEventListener('click', () => $('chatRail').classList.remove('open'))
  $('newChat').addEventListener('click', () => {
    $('chatRail').classList.remove('open')
    Chat.reset()
  })

  async function loadChats() {
    try {
      const res = await client.sessions()
      chats = (res && res.sessions) || []
    } catch {
      chats = []
    }
    if ($('chatCount')) $('chatCount').textContent = chats.length || ''
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
        void Chat.open(c.sessionId)
        $('chatRail').classList.remove('open')
      })
      ul.append(li)
    }
  }

  // ── Canvas & File Management ──────────────────────────────────────────────
  const generatedFiles = []
  const workspaceFiles = []
  let activeTab = 'generated'
  
  /* THE REAL CANVAS. agentdCanvas is the desktop shell's own viewer/editor stack
     (@agentd/canvas, vendored): PNG annotate/crop, SVG vector editing, PDF/text/media
     viewers, data-driven artifact actions (e.g. Convert-to-Vector) — identical behavior
     to the agentd client because it IS the agentd client's code, mounted over this app's
     SDK connection. Send-to-chat lands in THIS app's conversation via Chat. */
  let canvasApi = null
  function initCanvas() {
      const mount = $('canvasMount')
      if (!mount || !window.agentdCanvas) return
      canvasApi = agentdCanvas.mountCanvas(mount, {
          client,
          agentId: 'figure-create',
          sendToChat: async (text, attachments) => {
              for (const att of attachments || []) {
                  Chat.attachDataUrl(`data:${att.mimeType};base64,${att.dataBase64}`, att.name)
              }
              Chat.ask(text || "I've annotated the figure. Please process these changes.")
          },
      })
  }

  /** mime/name -> the Artifact `kind` the canvas viewers key off. */
  function artifactKind(name, mime) {
      const m = mime || ''
      if (m.startsWith('image/') || /\.(png|jpe?g|gif|svg|webp)$/i.test(name)) return 'image'
      if (m.startsWith('video/') || /\.(mp4|webm|mov)$/i.test(name)) return 'video'
      if (m.startsWith('audio/') || /\.(mp3|wav|ogg)$/i.test(name)) return 'audio'
      return 'file'
  }

  function formatSize(bytes) {
      if (!bytes) return '0 B'
      const k = 1024
      const sizes = ['B', 'KB', 'MB', 'GB']
      const i = Math.floor(Math.log(bytes) / Math.log(k))
      return parseFloat((bytes / Math.pow(k, i)).toFixed(1)) + ' ' + sizes[i]
  }

  function getIcon(name, mime) {
      if (mime?.startsWith('image/') || name.match(/\.(png|jpg|jpeg|gif|svg)$/i)) return '🖼️'
      if (name.endsWith('.pptx')) return '📊'
      if (name.endsWith('.pdf')) return '📄'
      return '📄'
  }

  async function loadWorkspaceFiles() {
      try {
          const res = await client.request('workspace.list', {})
          workspaceFiles.length = 0
          const items = Array.isArray(res) ? res : (res && res.files) ? res.files : []
          if (items.length > 0) {
              workspaceFiles.push(...items.map(f => ({
                  name: f.name,
                  path: f.path,
                  size: f.size,
                  url: client.fileUrl(f.path)
              })))
          }
          if (activeTab === 'workspace') renderFileList()
      } catch (e) {
          console.error("Failed to load workspace files", e)
      }
  }

  function addGeneratedFile(artifact) {
      if (!artifact || !artifact.path) return
      
      const fileInfo = {
          name: artifact.name || artifact.path.split('/').pop(),
          path: artifact.path,
          mime: artifact.mime,
          url: client.fileUrl(artifact.path)
      }
      
      // Prevent duplicates in the list
      if (!generatedFiles.some(f => f.path === artifact.path)) {
          generatedFiles.unshift(fileInfo) // Add to top
          if (activeTab === 'generated') renderFileList()
      }

      // Add to chat thread
      const thread = $('thread')
      if (thread) {
          const card = document.createElement('div')
          card.className = 'chat-artifact-card'
          if (fileInfo.mime?.startsWith('image/') || fileInfo.name.match(/\.(png|jpg|jpeg|gif|svg)$/i)) {
              card.innerHTML = `<img src="${fileInfo.url}" alt="${fileInfo.name}" class="chat-artifact-img" style="max-width: 240px; border-radius: 8px; cursor: pointer; border: 1px solid var(--hair);" />`
          } else {
              card.innerHTML = `<div class="chip-file" style="cursor: pointer;"><span class="ico">${getIcon(fileInfo.name, fileInfo.mime)}</span> <span class="chip-name">${fileInfo.name}</span></div>`
          }
          
          card.addEventListener('click', () => openInCanvas(fileInfo))
          thread.appendChild(card)
          thread.scrollTop = thread.scrollHeight // auto scroll
      }
      
      // Auto-load images into the canvas immediately
      if (fileInfo.mime?.startsWith('image/') || fileInfo.name.match(/\.(png|jpg|jpeg|gif|svg)$/i)) {
          openInCanvas(fileInfo)
      }
  }

  function renderFileList() {
      const list = $('fileList')
      if (!list) return
      list.innerHTML = ''
      
      const files = activeTab === 'generated' ? generatedFiles : workspaceFiles
      
      if (files.length === 0) {
          list.innerHTML = `<li style="justify-content: center; color: var(--faint); cursor: default;">No files here yet</li>`
          return
      }

      files.forEach(f => {
          const li = document.createElement('li')
          li.innerHTML = `
              <span class="ico">${getIcon(f.name, f.mime)}</span>
              <span class="name" title="${f.name}">${f.name}</span>
              ${f.size ? `<span class="size">${formatSize(f.size)}</span>` : ''}
          `
          li.addEventListener('click', () => {
              // visual active state
              list.querySelectorAll('li').forEach(el => el.classList.remove('active'))
              li.classList.add('active')
              openInCanvas(f)
          })
          list.appendChild(li)
      })
  }
  
  function openInCanvas(fileInfo) {
      // Every kind opens: the shared viewers cover images (annotate/crop), SVG (vector edit),
      // PDF, text/code/markdown, video/audio — no more download-fallback for non-images.
      if (!canvasApi) initCanvas()
      canvasApi?.open({
          path: fileInfo.path,
          name: fileInfo.name,
          mime: fileInfo.mime || '',
          kind: artifactKind(fileInfo.name, fileInfo.mime),
      })
  }

  // Setup tabs. Generated = the flat list of this session's outputs; Workspace = the shell's
  // REAL tree (hierarchy, lazy per-directory loading, upload / new-folder / delete), mounted
  // once on first visit — clicking a file opens it on the canvas above.
  let workspaceMounted = false
  document.querySelectorAll('.file-tabs .tab-btn').forEach(btn => {
      btn.addEventListener('click', (ev) => {
          document.querySelectorAll('.file-tabs .tab-btn').forEach(b => b.classList.remove('active'))
          ev.target.classList.add('active')
          activeTab = ev.target.dataset.tab
          const ws = activeTab === 'workspace'
          $('fileList').hidden = ws
          $('workspaceMount').hidden = !ws
          if (ws && !workspaceMounted && window.agentdCanvas) {
              workspaceMounted = true
              agentdCanvas.mountWorkspace($('workspaceMount'), {
                  client,
                  agentId: 'figure-create',
                  onOpen: (artifact) => openInCanvas(artifact),
              })
          }
          if (!ws) renderFileList()
      })
  })

  // ── Resizer ───────────────────────────────────────────────────────────────
  const resizer = $('resizer')
  const leftPanel = document.querySelector('.left-panel')
  let isResizing = false

  if (resizer && leftPanel) {
    resizer.addEventListener('mousedown', (e) => {
      isResizing = true
      document.body.style.cursor = 'col-resize'
      resizer.classList.add('dragging')
    })
    window.addEventListener('mousemove', (e) => {
      if (!isResizing) return
      leftPanel.style.width = `${e.clientX}px`
    })
    window.addEventListener('mouseup', () => {
      if (isResizing) {
        isResizing = false
        document.body.style.cursor = ''
        resizer.classList.remove('dragging')
      }
    })
  }

  // ── Make Chat Images Clickable ────────────────────────────────────────────
  const thread = $('thread')
  if (thread) {
    thread.addEventListener('click', (ev) => {
      if (ev.target.tagName === 'IMG') {
        const src = ev.target.src
        const name = ev.target.alt || src.split('/').pop() || 'chat-image.png'
        openInCanvas({ url: src, name: name, mime: 'image/png' })
      }
    })
  }

  // ── Templates Modal ───────────────────────────────────────────────────────
  
  const templates = [
      { id: 'biorender-shaded', name: 'BioRender Shaded (semi-3D)', desc: 'Clean vector forms with soft volumetric shading. Best for biology.' },
      { id: 'flat-vector', name: 'Flat Vector Schematic', desc: 'Uniform outlines, flat fills. Minimal, modern infographic look.' },
      { id: 'ghosted-anatomy', name: 'Ghosted Anatomy', desc: 'Semi-transparent outer layers revealing internal anatomy.' },
      { id: 'isometric-3d-stem', name: '3D Isometric STEM', desc: 'Clean stylized isometric view. Best for physics/engineering.' },
      { id: 'watercolor-atlas', name: 'Watercolor Atlas', desc: 'Classic hand-painted medical atlas illustration.' },
      { id: 'cell-journal-cover', name: 'Journal Cover', desc: 'Cinematic, rich depth, refined lighting.' },
      { id: 'microscopy-histology', name: 'Microscopy/Histology', desc: 'Stylised field of view of stained tissue/cells.' },
      { id: 'molecular-structural', name: 'Molecular/Structural', desc: 'Proteins, DNA, molecules with clear depth.' }
  ]

  function initTemplatesModal() {
      const modal = $('templatesModal')
      const btn = $('templatesBtn')
      const closeBtn = $('templatesClose')
      const list = $('templateList')
      
      if(!modal || !btn || !list) return

      btn.addEventListener('click', () => { modal.hidden = false })
      closeBtn.addEventListener('click', () => { modal.hidden = true })
      
      list.innerHTML = ''
      templates.forEach(t => {
          const card = document.createElement('div')
          card.className = 'template-card'
          card.innerHTML = `
              <h3>${t.name}</h3>
              <p>${t.desc}</p>
          `
          card.addEventListener('click', () => {
              modal.hidden = true
              Chat.ask(`Use the **${t.id}** template.`)
          })
          list.appendChild(card)
      })
  }

  // ── boot ──────────────────────────────────────────────────────────────────
  Settings.init(client)
  Chat.init(client, {
    onToolDone: (ev) => {
      // Catch artifacts output by tools (e.g. generate_artwork, compose_figure_layers)
      if (ev.artifacts && Array.isArray(ev.artifacts)) {
          ev.artifacts.forEach(addGeneratedFile)
      }
      // Also reload workspace just in case
      if (activeTab === 'workspace') loadWorkspaceFiles()
    },
    onSession: (key) => {}
  })
  drawSuggests()
  initCanvas()
  initTemplatesModal()
  renderFileList()

  void (async () => {
    try {
      await agentd.mountSignInGate({ client })
      // If sign-in succeeds and accountsUrl is present, show sign out
      const s = await client.request('platform.status', {}).catch(()=>null)
      if (s && s.accountsUrl) {
        $('signOut').hidden = false
        if (s.user && s.user.email) $('userEmail').textContent = s.user.email
      }
    } catch (e) {
      console.warn('[sign-in]', (e && e.message) || e)
    }
  })()

  let started = false

  $('signOut').addEventListener('click', async () => {
    localStorage.removeItem('agentd.session.figure-create')
    try { await client.request('platform.disconnect', {}) } catch (_) {}
    window.location.reload()
  })

  function setStatus(state, text) {
    const el = $('status')
    if (el) {
      el.className = 'status' + (state ? ' ' + state : '')
      const textEl = $('statusText')
      if (textEl) textEl.textContent = text
    }
  }

  client.onStatus((s) => {
    if (s === 'open') {
      setStatus('live', 'connected')
    } else if (s === 'closed') {
      setStatus('down', 'disconnected')
    } else {
      setStatus('', s)
    }

    if (s === 'open' && !started) {
      started = true
      void (async () => {
        try {
          const me = await client.agentDetail()
          const agent = (me && me.agent) || me || {}
          const label = agent.name || AGENT_ID
          if (label) {
            $('brandName').textContent = label
            document.title = label
          }
        } catch { }
        await loadWorkspaceFiles()
        loadChats()
      })()
    }
  })

  client.on('sessions.changed', () => void loadChats())
})()