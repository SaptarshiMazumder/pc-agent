/* Figure Creator — the agent's own desktop app. A pure CLIENT of the local daemon:
 *   • sign-in is the SDK's standard gate: identity rides the SOCKET (`?session=`), the daemon
 *     stores nothing — see b4→b5 note below
 *   • the chat with the figure-creator agent goes over the daemon WebSocket (streamed events)
 *   • figures are the artifacts the pipeline declares; we just render them
 * No backend of its own — an app INVOKES the agent, it never extends it.
 *
 * b5: the b4 flow (login → HTTP /platform/connect → poll /platform/status until the proxy
 * reports enabled) bound a DAEMON-GLOBAL platform key. That global is gone by design — identity
 * and billing are per-connection now, so /platform/status over plain HTTP (no connection) says
 * signedIn:false forever, and b4's confirmation poll died on a value that could never flip.
 * The SDK gate is the one sign-in every agent app shares; nothing here hand-rolls auth again. */

const BUILD = 'b5-sdk-gate'
console.log('figure-creator app', BUILD)

const $ = (id) => document.getElementById(id)

const client = agentd.fromPage({ clientName: 'figure-creator-app/1' })
const SESSION = 'figure'

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

// ---- sign-in (the SDK's standard gate) --------------------------------------------------
$('signOut').addEventListener('click', () => {
  // The session is CLIENT state (the daemon holds none): clear it and reload — the fresh page
  // re-runs the gate, which re-dials the socket without a session.
  agentd.saveSession(null)
  window.location.reload()
})

/** One awaited call decides everything: the gate renders NOTHING on a BYOK build, when this
 *  device's machine token already authorizes it, or when a stored session still works — and
 *  otherwise shows the shared sign-in, signs in, and re-dials the socket with the session.
 *  Past the await, this window is as signed-in as it is ever going to be. */
async function bootstrap() {
  try {
    await agentd.mountSignInGate({ client })
  } catch (e) {
    // The daemon itself is unreachable — say so plainly rather than a dead spinner.
    setStatus('off', 'cannot reach the local service')
    console.warn('[sign-in]', (e && e.message) || e)
    return
  }
  authReady = true
  $('signOut').hidden = !agentd.loadSession()
  refreshComposer()
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
    // Streamed text is message_update with kind 'text_delta'. There is NO 'message_delta'
    // event — a branch on one is dead code, and this window showed nothing until message_end.
    case 'message_update':
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
