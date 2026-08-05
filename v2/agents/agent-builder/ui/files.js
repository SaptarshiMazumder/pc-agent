/* The inspector's file tree + viewer.

   Two roots per agent, because an agent is two different things on disk:
     definition — agent.toml, IDENTITY.md, skills/, plugins/, ui/  (what BUILDING writes)
     workspace  — what the agent PRODUCES when it runs

   Both come from workspace.list; `root` picks which. This window may point them at ANY
   agent because the daemon lists agent-builder in CROSS_AGENT_READS — a privilege no other
   agent app has, and one that covers reads only. */

window.Files = (function () {
  const $ = (id) => document.getElementById(id)
  let client = null
  let agentId = null
  let root = 'definition'
  const open = new Set()          // expanded dirs, by rel path
  let known = new Set()           // files seen last render — anything new gets flashed

  const GLYPH = { folder: '▸', image: '◧', video: '▶', audio: '♪', file: '·' }

  function size(n) {
    if (!n) return ''
    const u = ['B', 'KB', 'MB', 'GB']
    let i = 0
    while (n >= 1024 && i < u.length - 1) { n /= 1024; i++ }
    return `${n < 10 && i ? n.toFixed(1) : Math.round(n)}${u[i]}`
  }

  function init(c) {
    client = c
    $('viewerClose').addEventListener('click', close)
    $('viewerBack').addEventListener('click', (e) => { if (e.target === $('viewerBack')) close() })
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape') close() })
    for (const tab of document.querySelectorAll('#fileTabs .tab')) {
      tab.addEventListener('click', () => {
        for (const t of document.querySelectorAll('#fileTabs .tab')) t.classList.toggle('active', t === tab)
        root = tab.dataset.root
        open.clear()
        known = new Set()
        void refresh()
      })
    }
  }

  function select(id) {
    if (agentId !== id) { open.clear(); known = new Set() }
    agentId = id
    $('fileTabs').hidden = !id
    return refresh()
  }

  /** One directory. Errors render as a line, never as an exception — the panel is secondary. */
  async function list(path) {
    try {
      const res = await client.request('workspace.list', { agentId, path, root })
      if (res.error) return { entries: [], error: res.error }
      return { entries: res.entries || [], error: '' }
    } catch (e) {
      return { entries: [], error: String((e && e.message) || e) }
    }
  }

  /** Depth-first render of the expanded set. Sequential on purpose: a handful of small
   *  directory reads, and ordering matters more than shaving a few ms. */
  async function draw(path, depth, into) {
    const { entries, error } = await list(path)
    if (error) {
      const li = document.createElement('div')
      li.className = 'tree-empty'
      li.textContent = error
      into.append(li)
      return
    }
    for (const e of entries) {
      const row = document.createElement('div')
      row.className = `node ${e.kind === 'folder' ? 'dir' : ''}`
      row.style.paddingLeft = `${7 + depth * 13}px`

      const isOpen = open.has(e.rel)
      const glyph = document.createElement('span')
      glyph.className = 'glyph'
      glyph.textContent = e.kind === 'folder' ? (isOpen ? '▾' : '▸') : (GLYPH[e.kind] || GLYPH.file)

      const name = document.createElement('span')
      name.className = 'nname'
      name.textContent = e.name

      row.append(glyph, name)
      if (e.kind !== 'folder') {
        const sz = document.createElement('span')
        sz.className = 'nsize'
        sz.textContent = size(e.size)
        row.append(sz)
      }
      // written since the last refresh -> flash it, so you SEE the build happening
      if (e.kind !== 'folder' && known.size && !known.has(e.path)) row.classList.add('fresh')

      row.addEventListener('click', () => {
        if (e.kind === 'folder') {
          open.has(e.rel) ? open.delete(e.rel) : open.add(e.rel)
          void refresh()
        } else {
          void view(e)
        }
      })
      into.append(row)

      if (e.kind === 'folder' && isOpen) await draw(e.rel, depth + 1, into)
    }
  }

  async function refresh() {
    const tree = $('tree')
    if (!agentId) { tree.textContent = ''; return }
    const frag = document.createDocumentFragment()
    await draw('', 0, frag)
    tree.textContent = ''
    tree.append(frag)
    if (!tree.children.length) {
      const empty = document.createElement('div')
      empty.className = 'tree-empty'
      empty.textContent = root === 'workspace' ? 'no files produced yet' : 'nothing here'
      tree.append(empty)
    }
    // snapshot AFTER drawing, so the next refresh can tell what is new
    const seen = new Set()
    const walk = async (path) => {
      const { entries } = await list(path)
      for (const e of entries) {
        if (e.kind === 'folder') { if (open.has(e.rel)) await walk(e.rel) } else seen.add(e.path)
      }
    }
    await walk('')
    known = seen
  }

  const TEXTY = /\.(toml|md|txt|json|ya?ml|py|js|mjs|ts|tsx|css|html|sh|ps1|cfg|ini|log|env)$/i

  async function view(entry) {
    $('viewerName').textContent = entry.name
    $('viewerPath').textContent = entry.path
    const body = $('viewerBody')
    body.textContent = 'loading…'
    $('viewerBack').hidden = false

    const url = client.fileUrl(entry.path)
    try {
      if (entry.kind === 'image') {
        body.textContent = ''
        const img = document.createElement('img')
        img.src = url
        img.alt = entry.name
        body.append(img)
        return
      }
      if (entry.kind === 'video' || entry.kind === 'audio') {
        body.textContent = ''
        const el = document.createElement(entry.kind === 'video' ? 'video' : 'audio')
        el.src = url
        el.controls = true
        body.append(el)
        return
      }
      if (!TEXTY.test(entry.name) && entry.size > 512 * 1024) {
        body.textContent = 'binary file — not shown'
        return
      }
      const text = await (await fetch(url)).text()
      body.textContent = ''
      if (/\.md$/i.test(entry.name)) {
        const div = document.createElement('div')
        div.className = 'md'
        div.innerHTML = MD.render(text)
        body.append(div)
      } else {
        const pre = document.createElement('pre')
        pre.textContent = text
        body.append(pre)
      }
    } catch (e) {
      body.textContent = `could not read: ${(e && e.message) || e}`
    }
  }

  function close() { $('viewerBack').hidden = true }

  return { init, select, refresh, close, get agentId() { return agentId } }
})()
