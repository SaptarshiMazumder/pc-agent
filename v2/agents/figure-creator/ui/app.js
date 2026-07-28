/* Figure Creator — the agent's own desktop app. A pure CLIENT of the local daemon:
 *   • sign-in and platform-key binding go over plain HTTP to the daemon (reliable, same origin)
 *   • the chat with the figure-creator agent goes over the daemon WebSocket (streamed events)
 *   • figures are the artifacts the pipeline declares; we just render them
 * No backend of its own — an app INVOKES the agent, it never extends it. */

const BUILD = 'b4-http-signin'
console.log('figure-creator app', BUILD)

const $ = (id) => document.getElementById(id)

// The page is served BY the daemon, so its own origin + bearer token authorize same-origin
// HTTP calls back to it. HTTP is the reliable transport for sign-in: a plain fetch to our own
// origin can't be disturbed by the live model-proxy reconfigure that binding a token performs.
const PAGE = new URL(window.location.href)
const ORIGIN = PAGE.origin
const DTOKEN = PAGE.searchParams.get('token') || ''

const client = agentd.fromPage({ clientName: 'figure-creator-app/1' })
const SESSION = 'figure'

// ---- tiny helpers ----------------------------------------------------------------------
function withTimeout(promise, ms, label) {
  return Promise.race([
    promise,
    new Promise((_, rej) => setTimeout(() => rej(new Error(label + ' timed out')), ms))
  ])
}

/** GET a daemon HTTP endpoint (auth via the page's bearer token). Returns parsed JSON.
 *  Every phase is traced to the console AND time-bounded — including reading the body,
 *  which is a separate await that would otherwise be an unbounded hang. */
async function daemonGet(path, params) {
  const u = new URL(path, ORIGIN)
  if (DTOKEN) u.searchParams.set('token', DTOKEN)
  for (const [k, v] of Object.entries(params || {})) u.searchParams.set(k, v)
  console.log('[daemonGet] →', path)
  const r = await withTimeout(fetch(u.toString(), { cache: 'no-store' }), 8000, 'the local service')
  console.log('[daemonGet] ←', path, 'status', r.status)
  const data = await withTimeout(r.json().catch(() => ({})), 5000, 'reading the reply')
  console.log('[daemonGet] ✓', path, JSON.stringify(data).slice(0, 120))
  if (!r.ok) throw new Error((data && data.error) || ('HTTP ' + r.status))
  return data
}

const platformStatus = () => daemonGet('/platform/status', {})
const platformConnect = (session) => daemonGet('/platform/connect', { session })

// ---- connection status (top bar) -------------------------------------------------------
let authReady = false // signed in on a hosted build, or BYOK — cleared to use the agent
let wsOpen = false
let busy = false
function setStatus(state, text) {
  $('status').className = 'status' + (state ? ' ' + state : '')
  $('statusText').textContent = text
}
function refreshComposer() {
  const usable = authReady && wsOpen && !busy
  // while busy the button is live as "Stop"; otherwise it's Generate and needs a ready app
  $('send').disabled = busy ? !wsOpen : !usable
  $('prompt').disabled = !(authReady && wsOpen)
  if (busy) return
  if (!authReady) setStatus('off', 'sign in to start')
  else if (!wsOpen) setStatus('', 'connecting…')
  else setStatus('on', 'ready')
}

// ---- sign-in gate ----------------------------------------------------------------------
const SESSION_LS = 'figurecreator.session'
let accountsBase = ''
let signupMode = false

const loadSession = () => { try { return JSON.parse(localStorage.getItem(SESSION_LS) || 'null') } catch { return null } }
const saveSession = (s) => { try { s ? localStorage.setItem(SESSION_LS, JSON.stringify(s)) : localStorage.removeItem(SESSION_LS) } catch (_) {} }

async function accountsPost(path, body) {
  const r = await withTimeout(fetch(accountsBase + path, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body)
  }), 12000, 'the sign-in server')
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error((data && data.detail) || ('sign-in failed (HTTP ' + r.status + ')'))
  return data
}

function showGate() { $('gate').hidden = false; setTimeout(() => $('gateEmail').focus(), 0) }
function hideGate() { $('gate').hidden = true }
function gateMsg(text) { $('gateSub').textContent = text }
function gateError(text) {
  const el = $('gateError')
  if (text) { el.textContent = text; el.hidden = false } else { el.hidden = true }
}

$('gateToggle').addEventListener('click', () => {
  signupMode = !signupMode
  $('gateTitle').textContent = signupMode ? 'Create your account' : 'Sign in'
  $('gateBtn').textContent = signupMode ? 'Create account' : 'Sign in'
  $('gateToggle').textContent = signupMode ? 'Have an account? Sign in' : 'New here? Create an account'
  $('gatePass').setAttribute('autocomplete', signupMode ? 'new-password' : 'current-password')
  gateMsg('Figures run on our servers — no API keys to set up.')
  gateError('')
})

/** Sign in (or up), then hand the session token to the LOCAL daemon so it runs on platform
 *  keys. Both steps are plain HTTP + time-bounded, so this can never hang silently. */
async function doSignIn(email, password) {
  if (!accountsBase) throw new Error('this build has no sign-in server configured')
  console.log('[signin] start; accounts =', accountsBase)
  gateMsg('Signing in…')
  if (signupMode) await accountsPost('/signup', { email, password })
  const login = await accountsPost('/login', { email, password })
  console.log('[signin] login ok; activating on device…')
  gateMsg('Activating your account on this device…')
  // The connect CALL does the work; its REPLY is only a confirmation. If that one response
  // is lost (observed in the wild on the packaged shell), the daemon is still signed in —
  // so on any doubt we fall back to polling status, the ground truth. Bounded throughout.
  let ok = false
  try {
    const status = await platformConnect(login.token)
    console.log('[signin] connect replied:', JSON.stringify(status).slice(0, 160))
    ok = !!(modelProxyStatus(status) && modelProxyStatus(status).enabled)
  } catch (e) {
    console.log('[signin] connect reply lost:', (e && e.message) || e)
  }
  for (let i = 0; !ok && i < 6; i++) {
    gateMsg('Confirming… (' + (i + 1) + '/6)')
    await new Promise((r) => setTimeout(r, 700))
    try {
      const s = await platformStatus()
      ok = !!(modelProxyStatus(s) && modelProxyStatus(s).enabled)
    } catch (_) { /* transient — keep polling */ }
  }
  if (!ok) throw new Error('could not activate this device — the Figure Creator service did not accept the sign-in')
  saveSession({ token: login.token, email: login.email || email })
  console.log('[signin] SUCCESS')
  onSignedIn()
}

$('gateForm').addEventListener('submit', async (ev) => {
  ev.preventDefault()
  const email = $('gateEmail').value.trim().toLowerCase()
  const password = $('gatePass').value
  const btn = $('gateBtn')
  gateError('')
  btn.disabled = true
  const label = btn.textContent
  btn.textContent = 'Please wait…'
  try {
    await doSignIn(email, password)
  } catch (e) {
    const msg = (e && (e.message || e.toString())) || 'unknown error'
    gateError(msg)
    gateMsg('Figures run on our servers — no API keys to set up.')
  } finally {
    btn.disabled = false
    btn.textContent = label
  }
})

function onSignedIn() {
  authReady = true
  hideGate()
  $('signOut').hidden = false
  refreshComposer()
}

/** New daemons say modelProxy; modelGateway keeps already-installed daemons compatible. */
function modelProxyStatus(status) {
  return status && (status.modelProxy || status.modelGateway)
}

$('signOut').addEventListener('click', async () => {
  saveSession(null)
  try { await client.request('platform.disconnect', {}) } catch (_) {}
  window.location.reload()
})

/** Decide what this build needs the moment the page loads — asked over HTTP so it works even
 *  if the WebSocket is slow to come up. */
async function bootstrap() {
  let status
  try {
    status = await platformStatus()
  } catch (e) {
    // daemon HTTP not reachable — surface it plainly rather than a dead spinner
    setStatus('off', 'cannot reach the local service')
    gateError('Could not reach Figure Creator on this device: ' + ((e && e.message) || e))
    showGate()
    return
  }
  accountsBase = String(status.accountsUrl || '').replace(/\/$/, '')
  const hosted = !!accountsBase
  const keysLive = !!(modelProxyStatus(status) && modelProxyStatus(status).enabled)

  if (!hosted || keysLive) {
    // BYOK build, or the daemon already holds a valid platform token (persisted) — go in.
    authReady = true
    $('signOut').hidden = !hosted
    refreshComposer()
    return
  }
  // hosted, not connected: try a stored token silently, else show the gate
  const stored = loadSession()
  if (stored && stored.token) {
    try {
      const s = await platformConnect(stored.token)
      if (modelProxyStatus(s) && modelProxyStatus(s).enabled) { onSignedIn(); return }
    } catch (_) { /* fall through */ }
    saveSession(null)
  }
  refreshComposer()
  showGate()
}

// ---- WebSocket lifecycle (chat transport) ----------------------------------------------
let booted = false
client.onStatus((s) => {
  if (s === 'open') {
    wsOpen = true
    if (!booted) { booted = true; void bootstrap() }
    refreshComposer()
  } else {
    wsOpen = false
    if (!busy) refreshComposer()
  }
})

// ---- conversation log ------------------------------------------------------------------
const log = $('log')
const atBottom = () => log.scrollHeight - log.scrollTop - log.clientHeight < 40
const scroll = () => { log.scrollTop = log.scrollHeight }

function addUser(text) {
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
  el.appendChild(document.createTextNode(' ' + prettyTool(name)))
  log.appendChild(el)
  if (atBottom()) scroll()
  return el
}
function finishStep(name, isError) {
  const steps = log.querySelectorAll('.step[data-tool="' + cssEscape(name) + '"]')
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
const prettyTool = (name) => String(name || 'working').replace(/_/g, ' ').replace(/\bto svg\b/i, '→ SVG')
const cssEscape = (s) => String(s).replace(/"/g, '\\"')

// ---- the figure stage ------------------------------------------------------------------
let current = null // {path, name, mime}
const isImage = (a) => a && typeof a.mime === 'string' && a.mime.indexOf('image/') === 0
const isRaster = (a) => isImage(a) && a.mime !== 'image/svg+xml'

function showFigure(a) {
  current = { path: a.path, name: a.name || 'figure', mime: a.mime }
  $('empty').hidden = true
  $('figureWrap').hidden = false
  $('figureImg').src = client.fileUrl(a.path)
  $('figureImg').alt = a.name || 'Generated figure'
  $('toolbar').hidden = false
  const dl = $('downloadBtn')
  dl.href = client.fileUrl(a.path)
  dl.setAttribute('download', a.name || 'figure')
  $('svgBtn').hidden = !isRaster(a)
}
function takeArtifacts(list) {
  if (!Array.isArray(list)) return
  const images = list.filter(isImage)
  if (images.length) showFigure(images[images.length - 1])
}

// ---- run the agent ---------------------------------------------------------------------
// While a run is in flight the Generate button becomes Stop (chat.abort), and a watchdog
// unlocks the UI if the stream goes silent — a stuck run must never brick the composer.
let lastEventAt = 0
let watchdog = null
function setBusy(on) {
  busy = on
  if (on) {
    setStatus('busy', 'working…')
    lastEventAt = Date.now()
    if (!watchdog) watchdog = setInterval(() => {
      if (busy && Date.now() - lastEventAt > 180000) {
        setBusy(false)
        notice('The run went quiet — it may have died. Try again.', true)
      }
    }, 10000)
  } else if (watchdog) {
    clearInterval(watchdog)
    watchdog = null
  }
  $('send').textContent = on ? 'Stop' : 'Generate'
  $('send').title = on ? 'Stop this run' : 'Generate'
  refreshComposer()
}
function onEvent(payload) {
  lastEventAt = Date.now()
  const e = payload.event || {}
  switch (e.type) {
    case 'message_delta': assistant().textContent += e.delta || ''; if (atBottom()) scroll(); break
    case 'message_end': endAssistant(); break
    case 'tool_execution_start': addStep(e.toolName || 'tool'); break
    case 'tool_execution_end': finishStep(e.toolName || 'tool', !!e.isError); takeArtifacts(e.artifacts); break
    case 'agent_end':
      endAssistant()
      takeArtifacts(e.artifacts)
      if (e.stopReason === 'error') notice(String(e.error || 'the run failed'), true)
      setBusy(false)
      break
    default: break
  }
}
client.onRun(SESSION, onEvent)

async function generate(text) {
  const prompt = String(text || '').trim()
  if (!prompt || busy || !authReady || !wsOpen) return
  addUser(prompt)
  setBusy(true)
  try {
    await client.send({ message: prompt, sessionKey: SESSION })
  } catch (err) {
    notice('Could not start the figure: ' + ((err && err.message) || err), true)
    setBusy(false)
  }
}

// ---- convert the current raster figure to an editable vector (SVG) ----------------------
$('svgBtn').addEventListener('click', async () => {
  if (!current || busy) return
  addStep('figure_to_svg')
  setBusy(true)
  try {
    const res = await client.invokeTool('figure_to_svg', { image: current.path })
    finishStep('figure_to_svg', false)
    takeArtifacts(res.artifacts)
    if (!(res.artifacts || []).some(isImage)) notice('No vector was produced.', true)
  } catch (err) {
    finishStep('figure_to_svg', true)
    notice('Vectorize failed: ' + ((err && err.message) || err), true)
  } finally {
    setBusy(false)
  }
})

// ---- composer --------------------------------------------------------------------------
const promptEl = $('prompt')
$('composer').addEventListener('submit', (ev) => {
  ev.preventDefault()
  if (busy) {
    // Stop: abort the in-flight run; agent_end (stopReason aborted) unlocks the UI
    client.abort(SESSION).catch(() => setBusy(false))
    return
  }
  const text = promptEl.value
  promptEl.value = ''
  void generate(text)
})
promptEl.addEventListener('keydown', (ev) => {
  if (ev.key === 'Enter' && !ev.shiftKey) { ev.preventDefault(); $('composer').requestSubmit() }
})
$('examples').addEventListener('click', (ev) => {
  const li = ev.target.closest('li')
  if (li) void generate(li.textContent)
})

// ---- boot ------------------------------------------------------------------------------
// Surface any uncaught error so a failure is never a silent frozen screen.
window.addEventListener('error', (e) => setStatus('off', 'error: ' + (e.message || 'unknown')))
window.addEventListener('unhandledrejection', (e) =>
  setStatus('off', 'error: ' + ((e.reason && e.reason.message) || e.reason)))

refreshComposer()
setStatus('', 'starting…')
// If the WebSocket is slow, still run bootstrap over HTTP so sign-in isn't blocked on it.
setTimeout(() => { if (!booted) { booted = true; void bootstrap() } }, 1500)
