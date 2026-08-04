/* Agent Builder workbench — a pure CLIENT of the daemon.
   It reads the roster (agents.list / agents.detail) and INVOKES this agent's own
   validate_agent + package_agent over its scoped connection (tools.invoke). It has no
   backend of its own and never could — that's the platform invariant.

   Note on scope: the connection is scoped to `agent:agent-builder`, which decides WHICH
   TOOLS may be invoked — not which agents they may act on. So this window validates and
   packages every agent on the machine while only ever calling its own two tools. */

const here = new URL(location.href)
const pathId = (location.pathname.match(/^\/apps\/([^/]+)/) || [])[1]
const token = here.searchParams.get('token') || ''
const scope =
  here.searchParams.get('scope') || (pathId ? `agent:${decodeURIComponent(pathId)}` : '')
const client = new agentd.AgentdClient()
client.connect({ url: here.origin, token: token || undefined, scope: scope || undefined })

const $ = (id) => document.getElementById(id)
const SELF = 'agent-builder'

let agents = []
let selected = null
let busy = false

// ---------------------------------------------------------------- roster
async function loadAgents() {
  const res = await client.agents()
  agents = (res && res.agents) || []
  $('count').textContent = `${agents.length} on this machine`
  renderList()
  if (selected && !agents.some((a) => a.id === selected.id)) select(null)
}

function renderList() {
  const ul = $('agents')
  ul.textContent = ''
  for (const a of agents) {
    const li = document.createElement('li')
    li.className = 'row' + (selected && selected.id === a.id ? ' active' : '')
    li.tabIndex = 0

    const dot = document.createElement('span')
    dot.className = 'avatar'
    dot.style.background = a.color || '#6b7280'
    dot.textContent = (a.name || a.id).slice(0, 1).toUpperCase()

    const body = document.createElement('div')
    body.className = 'row-body'
    const top = document.createElement('div')
    top.className = 'row-top'
    top.append(text('span', 'row-name', a.name || a.id))
    if (a.version) top.append(text('span', 'ver', `v${a.version}`))
    if (a.app) top.append(text('span', 'chip', 'app'))
    body.append(top, text('div', 'row-sub muted', a.tagline || a.description || a.id))

    li.append(dot, body)
    li.addEventListener('click', () => select(a))
    li.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault()
        select(a)
      }
    })
    ul.append(li)
  }
}

function text(tag, cls, value) {
  const el = document.createElement(tag)
  el.className = cls
  el.textContent = value
  return el
}

// ---------------------------------------------------------------- detail
async function select(a) {
  selected = a
  renderList()
  $('empty').hidden = !!a
  $('agent').hidden = !a
  if (!a) return

  $('a-name').textContent = a.name || a.id
  $('a-sub').textContent = a.tagline || a.description || ''
  setOutput('Nothing run yet.', 'muted')
  $('out-title').textContent = 'Output'

  const badges = $('a-badges')
  badges.textContent = ''
  if (a.app) badges.append(text('span', 'chip', a.app.mode === 'window' ? 'window app' : 'browser app'))
  if (a.id === SELF) badges.append(text('span', 'chip self', 'this agent'))

  const facts = $('a-facts')
  facts.textContent = ''
  addFact(facts, 'id', a.id)
  addFact(facts, 'version', a.version || '— not set (installs supersede BY VERSION)')

  // agents.detail is best-effort enrichment: the roster alone is enough to act on.
  try {
    const d = await client.agentDetail(a.id)
    const detail = (d && d.agent) || d || {}
    if (detail.model) addFact(facts, 'model', detail.model)
    if (detail.workspace) addFact(facts, 'workspace', detail.workspace)
    const skills = detail.skills || []
    if (skills.length) addFact(facts, 'skills', skills.map((s) => s.name).join(', '))
    if (detail.app && detail.app.url) addFact(facts, 'app entry', detail.app.url)
  } catch {
    /* roster data is sufficient — never block the actions on the detail call */
  }
}

function addFact(dl, key, value) {
  dl.append(text('dt', '', key), text('dd', '', value))
}

// ---------------------------------------------------------------- actions
function setOutput(value, cls = '') {
  const out = $('out')
  out.className = `out ${cls}`.trim()
  out.textContent = value
  $('clear').hidden = !value || cls === 'muted'
}

/** Both tools render their own report, and the gateway throws with that same text when the
 *  tool reports an error — so success and failure are formatted identically, and the only
 *  difference here is the styling. Never swallow the message: it IS the report. */
async function run(tool, label, params) {
  if (busy || !selected) return
  busy = true
  document.body.classList.add('busy')
  $('out-title').textContent = label
  setOutput(`Running ${tool} on '${selected.id}'…`, 'muted')
  try {
    const res = await client.invokeTool(tool, { agent_id: selected.id, ...params })
    setOutput(agentd.resultText(res) || '(no output)', 'ok')
    if (tool === 'package_agent') void loadAgents() // a pack can change nothing, but stay honest
  } catch (e) {
    setOutput(String((e && e.message) || e), 'bad')
  } finally {
    busy = false
    document.body.classList.remove('busy')
  }
}

$('validate').addEventListener('click', () => void run('validate_agent', 'Validation'))
$('package').addEventListener('click', () => void run('package_agent', 'Package'))
$('clear').addEventListener('click', () => setOutput('Nothing run yet.', 'muted'))
$('refresh').addEventListener('click', () => void loadAgents())

// ---------------------------------------------------------------- connection
// Say WHY we're closed rather than looping on "closed": a tokened (desktop) link dies when
// the daemon restarts and rotates its token; a public page just needs a refresh.
let started = false
client.onStatus((s) => {
  const el = $('status')
  el.className = `status ${s === 'open' ? 'live' : s === 'closed' ? 'down' : ''}`.trim()
  if (s === 'open') {
    el.textContent = 'live'
    if (!started) {
      started = true
      void loadAgents()
    }
  } else if (s === 'closed') {
    el.textContent = token ? 'disconnected — reopen from JARVIS' : 'disconnected — refresh'
  } else {
    el.textContent = s
  }
})

// The roster changes when an agent is created, reloaded, or installed — reload_agent
// broadcasts this, so the workbench stays current without polling.
client.on('agents.changed', () => void loadAgents())
