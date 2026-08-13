/* Bedtime Kids — the agent's own app. A pure CLIENT of the daemon: chat over the
 * WebSocket, history via the sessions RPCs. No backend of its own — an app INVOKES
 * the agent, it never extends it. (Pattern: expense-summarizer/ui/app.js, minus the
 * chart/artifact machinery — stories are text.) */

const AGENT = 'bedtime-kids'
const $ = (id) => document.getElementById(id)

const client = agentd.fromPage({ clientName: 'bedtime-kids-app/1' })

// ---- state -----------------------------------------------------------------------------
let wsOpen = false
let busy = false
let session = newSessionKey() // current story chat; "New story" mints another
let suggestions = [
  'A sleepy dragon who guards a lighthouse',
  'Two rabbits camping under the stars',
  "A story about my stuffed bear's day"
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
  $('send').disabled = !wsOpen
  $('prompt').disabled = !wsOpen
  $('send').textContent = busy ? 'Stop' : 'Tell it'
  if (busy) setStatus('busy', 'telling…')
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
function notice(text, isError) {
  const el = document.createElement('div')
  el.className = 'notice' + (isError ? ' err' : '')
  el.textContent = text
  log.appendChild(el)
  scroll()
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
        notice('The story went quiet — it may have stopped. Try again.', true)
      }
    }, 10000)
  } else if (watchdog) { clearInterval(watchdog); watchdog = null }
  refreshComposer()
}
function onEvent(payload) {
  lastEventAt = Date.now()
  const e = payload.event || {}
  switch (e.type) {
    case 'message_update': // streamed text: {kind, delta}. There is no 'message_delta' event.
      if (e.kind === 'text_delta') { assistant().textContent += e.delta || ''; if (atBottom()) scroll() }
      break
    case 'message_end': endAssistant(); break
    case 'agent_end':
      endAssistant()
      if (e.stopReason === 'error') notice(String(e.error || 'the story failed'), true)
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
async function sendMessage(text) {
  const prompt = String(text || '').trim()
  if (!prompt || busy || !wsOpen) return
  addUser(prompt)
  setBusy(true)
  try {
    await client.send({ message: prompt, sessionKey: session, agentId: AGENT })
  } catch (err) {
    notice('Could not start the story: ' + ((err && err.message) || err), true)
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
  log.querySelectorAll('.msg, .notice').forEach((n) => n.remove())
  hideWelcome()
  let payload
  try { payload = await client.history(key, AGENT) } catch (e) {
    notice('Could not load that story: ' + ((e && e.message) || e), true)
    return
  }
  const msgs = payload.messages || []
  for (const m of msgs) {
    const role = m.role || (m.message && m.message.role)
    const body = m.message || m
    const text = extractText(body)
    if (role === 'user' && text) addUser(text)
    else if (role === 'assistant' && text) { assistant().textContent = text; endAssistant() }
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
  log.querySelectorAll('.msg, .notice').forEach((n) => n.remove())
  const w = $('welcome'); if (w) w.hidden = false
  void loadSessions()
})

client.onStatus((s) => {
  wsOpen = s === 'open'
  if (wsOpen) { void loadSuggestions(); void loadSessions() }
  refreshComposer()
})

// SIGN-IN RUNS BEFORE THE SOCKET. This agent is web-delivered, so its normal entrance is a
// marketplace card linking to `/apps/bedtime-kids/` with no credential in the url — and on a
// hosted daemon the session token IS the socket credential. Waiting for `status === 'open'`
// would deadlock: unauthorized close, endless retry, no form. The gate is plain HTTP and needs
// no socket; `client` lets it reconnect the moment a session exists.
void (async () => {
  try {
    await agentd.mountSignInGate({ client })
  } catch (e) {
    // The daemon itself is unreachable. Not fatal: the status chip says so too.
    console.warn('[sign-in]', (e && e.message) || e)
  }
})()

// Surface any uncaught error — never a silent frozen screen.
window.addEventListener('error', (e) => setStatus('off', 'error: ' + (e.message || 'unknown')))
window.addEventListener('unhandledrejection', (e) =>
  setStatus('off', 'error: ' + ((e.reason && e.reason.message) || e.reason)))

followSession()
renderChips()
refreshComposer()
