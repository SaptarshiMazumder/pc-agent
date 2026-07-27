/* Expense Summarizer — the agent's own app. A pure CLIENT of the local daemon:
 * chat over the daemon WebSocket, history via the sessions RPCs, charts are the
 * artifacts the run declares. No backend of its own — an app INVOKES the agent,
 * it never extends it. (Pattern: figure-creator/ui/app.js, simplified: no sign-in
 * gate — this is the local BYOK build.) */

const AGENT = 'expense-summarizer'
const $ = (id) => document.getElementById(id)

const client = agentd.fromPage({ clientName: 'expense-summarizer-app/1' })

// ---- state -----------------------------------------------------------------------------
let wsOpen = false
let busy = false
let session = newSessionKey() // current chat; "New chat" mints another
let suggestions = [
  'Summarize my spending this month',
  'Top 5 merchants by spend this month',
  'Compare June vs July spending by category'
]

function newSessionKey() {
  return 'ui-' + Date.now().toString(36) + Math.random().toString(36).slice(2, 6)
}

// ---- status / composer gating ----------------------------------------------------------
function setStatus(state, text) {
  $('status').className = 'status' + (state ? ' ' + state : '')
  $('statusText').textContent = text
}
function refreshComposer() {
  $('send').disabled = busy ? !wsOpen : !wsOpen
  $('prompt').disabled = !wsOpen
  $('send').textContent = busy ? 'Stop' : 'Send'
  if (busy) setStatus('busy', 'working…')
  else if (wsOpen) setStatus('on', 'ready')
  else setStatus('', 'connecting…')
}

// ---- conversation log ------------------------------------------------------------------
const log = $('log')
const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40
const scroll = () => { log.scrollTop = log.scrollHeight }
const hideWelcome = () => { const w = $('welcome'); if (w) w.hidden = true }

function addUser(text) {
  hideWelcome()
  const el = document.createElement('div')
  el.className = 'msg user'
  el.textContent = text
  log.appendChild(el)
  scroll()
}
let assistantEl = null
function assistant() {
  if (!assistantEl) {
    assistantEl = document.createElement('div')
    assistantEl.className = 'msg assistant streaming'
    log.appendChild(assistantEl)
  }
  return assistantEl
}
function endAssistant() {
  if (assistantEl) assistantEl.classList.remove('streaming')
  assistantEl = null
}
function addStep(name) {
  const el = document.createElement('div')
  el.className = 'step'
  el.dataset.tool = name
  el.innerHTML = '<span class="s-dot">▸</span>'
  el.appendChild(document.createTextNode(' ' + String(name || 'working').replace(/_/g, ' ')))
  log.appendChild(el)
  if (atBottom()) scroll()
}
function finishStep(name, isError) {
  const steps = log.querySelectorAll('.step[data-tool="' + String(name).replace(/"/g, '\\"') + '"]')
  const el = steps[steps.length - 1]
  if (!el) return
  el.classList.add('done')
  if (isError) el.classList.add('err')
  el.querySelector('.s-dot').textContent = isError ? '✕' : '✓'
}
function notice(text, isError) {
  const el = document.createElement('div')
  el.className = 'notice' + (isError ? ' err' : '')
  el.textContent = text
  log.appendChild(el)
  scroll()
}
function addChart(a) {
  hideWelcome()
  const wrap = document.createElement('figure')
  wrap.className = 'chart'
  const img = document.createElement('img')
  img.src = client.fileUrl(a.path)
  img.alt = a.name || 'chart'
  const cap = document.createElement('figcaption')
  const dl = document.createElement('a')
  dl.href = client.fileUrl(a.path)
  dl.setAttribute('download', a.name || 'chart.png')
  dl.textContent = '⬇ ' + (a.name || 'chart.png')
  cap.appendChild(dl)
  wrap.appendChild(img)
  wrap.appendChild(cap)
  log.appendChild(wrap)
  scroll()
}
const isImage = (a) => a && typeof a.mime === 'string' && a.mime.indexOf('image/') === 0
function takeArtifacts(list) {
  if (!Array.isArray(list)) return
  for (const a of list) if (isImage(a)) addChart(a)
}

// ---- run events ------------------------------------------------------------------------
let lastEventAt = 0
let watchdog = null
let unsubscribe = null
function setBusy(on) {
  busy = on
  if (on) {
    lastEventAt = Date.now()
    if (!watchdog) watchdog = setInterval(() => {
      if (busy && Date.now() - lastEventAt > 180000) {
        setBusy(false)
        notice('The run went quiet — it may have died. Try again.', true)
      }
    }, 10000)
  } else if (watchdog) { clearInterval(watchdog); watchdog = null }
  refreshComposer()
}
function onEvent(payload) {
  lastEventAt = Date.now()
  const e = payload.event || {}
  switch (e.type) {
    case 'message_delta':
      assistant().textContent += e.delta || ''
      if (atBottom()) scroll()
      break
    case 'message_update': // engine-shaped fallback: {kind, delta}
      if (e.kind === 'text_delta') { assistant().textContent += e.delta || ''; if (atBottom()) scroll() }
      break
    case 'message_end': endAssistant(); break
    case 'tool_execution_start': addStep(e.toolName || 'tool'); break
    case 'tool_execution_end': finishStep(e.toolName || 'tool', !!e.isError); takeArtifacts(e.artifacts); break
    case 'agent_end':
      endAssistant()
      takeArtifacts(e.artifacts)
      if (e.stopReason === 'error') notice(String(e.error || 'the run failed'), true)
      setBusy(false)
      void loadSessions() // a finished run may have minted/updated a session row
      break
    default: break
  }
}
function followSession() {
  if (unsubscribe) unsubscribe()
  unsubscribe = client.onRun(session, onEvent)
}

// ---- sending ---------------------------------------------------------------------------
function chartSuffix() {
  const k = $('chartKind').value
  if (k === 'pie') return ' Present the chart as a pie chart.'
  if (k === 'trend') return ' Present a month-over-month trend line chart.'
  if (k === 'bar') return ' Present the chart as a horizontal bar chart.'
  return ''
}
async function sendMessage(text) {
  const prompt = String(text || '').trim()
  if (!prompt || busy || !wsOpen) return
  addUser(prompt)
  setBusy(true)
  try {
    await client.send({ message: prompt + chartSuffix(), sessionKey: session, agentId: AGENT })
  } catch (err) {
    notice('Could not start the run: ' + ((err && err.message) || err), true)
    setBusy(false)
  }
}

// ---- history sidebar -------------------------------------------------------------------
async function loadSessions() {
  let payload
  try { payload = await client.sessions(AGENT) } catch { return }
  const rows = payload.sessions || payload.items || []
  const ul = $('sessionList')
  ul.innerHTML = ''
  for (const s of rows) {
    const key = s.sessionKey || s.key || s.id
    if (!key) continue
    const li = document.createElement('li')
    li.textContent = s.title || s.preview || key
    li.title = key
    if (key === session) li.classList.add('active')
    li.addEventListener('click', () => { void openSession(key) })
    ul.appendChild(li)
  }
}
async function openSession(key) {
  if (busy) return
  session = key
  followSession()
  log.querySelectorAll('.msg, .step, .notice, .chart').forEach((n) => n.remove())
  hideWelcome()
  let payload
  try { payload = await client.history(key, AGENT) } catch (e) {
    notice('Could not load that chat: ' + ((e && e.message) || e), true)
    return
  }
  const msgs = payload.messages || []
  for (const m of msgs) {
    const role = m.role || (m.message && m.message.role)
    const body = m.message || m
    const text = extractText(body)
    if (role === 'user' && text) addUser(text)
    else if (role === 'assistant' && text) { assistant().textContent = text; endAssistant() }
    else if (role === 'toolResult') {
      for (const a of body.artifacts || []) if (isImage(a)) addChart(a)
    }
  }
  void loadSessions() // refresh the active highlight
}
function extractText(m) {
  const c = m && m.content
  if (typeof c === 'string') return c
  if (Array.isArray(c)) {
    return c.map((b) => (b && b.type === 'text' ? b.text || '' : '')).join('').trim()
  }
  return ''
}

// ---- quick prompts ---------------------------------------------------------------------
function renderChips() {
  const ul = $('chips')
  ul.innerHTML = ''
  for (const s of suggestions.slice(0, 3)) {
    const li = document.createElement('li')
    li.textContent = s
    li.addEventListener('click', () => { void sendMessage(s) })
    ul.appendChild(li)
  }
}
async function loadSuggestions() {
  try {
    const payload = await client.agents()
    const me = (payload.agents || []).find((a) => a.id === AGENT)
    if (me && Array.isArray(me.suggestions) && me.suggestions.length) suggestions = me.suggestions
  } catch { /* keep the fallback */ }
  renderChips()
}

// ---- wiring ----------------------------------------------------------------------------
$('composer').addEventListener('submit', (ev) => {
  ev.preventDefault()
  if (busy) { client.abort(session).catch(() => setBusy(false)); return }
  const text = $('prompt').value
  $('prompt').value = ''
  void sendMessage(text)
})
$('prompt').addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('composer').requestSubmit() }
})
$('newChat').addEventListener('click', () => {
  if (busy) return
  session = newSessionKey()
  followSession()
  log.querySelectorAll('.msg, .step, .notice, .chart').forEach((n) => n.remove())
  const w = $('welcome'); if (w) w.hidden = false
  void loadSessions()
})

client.onStatus((s) => {
  wsOpen = s === 'open'
  if (wsOpen) { void loadSuggestions(); void loadSessions() }
  refreshComposer()
})

// Surface any uncaught error — never a silent frozen screen.
window.addEventListener('error', (e) => setStatus('off', 'error: ' + (e.message || 'unknown')))
window.addEventListener('unhandledrejection', (e) =>
  setStatus('off', 'error: ' + ((e.reason && e.reason.message) || e.reason)))

followSession()
renderChips()
refreshComposer()
