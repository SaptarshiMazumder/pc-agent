/* App Demo Console — a complete agent-app client in vanilla JS on @agentd/client (window.agentd).
   Everything here is an INVOCATION of the published protocol; nothing extends the backend. */
'use strict'

// ---- connect: token + scope come from the page URL (the opener minted them) -----------------
const here = new URL(window.location.href)
const SCOPE = here.searchParams.get('scope') || ''
const AGENT_ID = SCOPE.startsWith('agent:') ? SCOPE.slice('agent:'.length) : ''
const client = agentd.fromPage({ clientName: 'app-demo/1.0' })

const $ = (id) => document.getElementById(id)
$('scopeBadge').textContent = SCOPE || 'unscoped'

let sessionKey = `app-demo-${Math.random().toString(36).slice(2, 10)}`
let stopRun = null // unsubscribe for the active run listener
let liveBubble = null // the assistant bubble currently streaming

// ---- tiny render helpers ---------------------------------------------------------------------
function bubble(cls, text) {
  const el = document.createElement('div')
  el.className = `msg ${cls}`
  el.textContent = text
  $('messages').appendChild(el)
  $('messages').scrollTop = $('messages').scrollHeight
  return el
}

function chip(text) {
  const el = document.createElement('div')
  el.className = 'chip'
  el.textContent = text
  $('messages').appendChild(el)
  $('messages').scrollTop = $('messages').scrollHeight
  return el
}

function artifactNode(a) {
  const mime = a.mimeType || a.mime || ''
  if (mime.startsWith('image/') && a.path) {
    const img = document.createElement('img')
    img.src = client.fileUrl(a.path)
    img.alt = a.name || 'artifact'
    return img
  }
  const link = document.createElement('a')
  link.href = a.path ? client.fileUrl(a.path) : '#'
  link.textContent = `📄 ${a.name || a.path || 'artifact'}`
  link.target = '_blank'
  return link
}

// ---- handshake + status -----------------------------------------------------------------------
client.onStatus((s) => {
  $('statusDot').classList.toggle('open', s === 'open')
  if (s === 'open') void boot()
})

async function boot() {
  const hello = await client.hello()
  $('serverInfo').textContent =
    `${hello.product} · agentd ${hello.version} · protocol ${hello.protocol}` +
    (hello.compatible === false ? ' · ⚠ newer client than server' : '')
  await refreshSessions()
  await refreshTools()
}

// ---- sessions ----------------------------------------------------------------------------------
async function refreshSessions() {
  const { sessions = [] } = await client.sessions(AGENT_ID || undefined)
  const pick = $('sessionPick')
  pick.replaceChildren(new Option('— resume a chat —', ''))
  for (const s of sessions) pick.appendChild(new Option(s.title || s.sessionId, s.sessionId))
}

$('sessionPick').onchange = async () => {
  const picked = $('sessionPick').value
  if (!picked) return
  sessionKey = picked
  $('messages').replaceChildren()
  const { messages = [] } = await client.history(sessionKey, AGENT_ID || undefined)
  for (const m of messages) {
    const text = (Array.isArray(m.content) ? m.content : [])
      .map((b) => (b && b.type === 'text' ? b.text : ''))
      .join('')
      .trim()
    if (text && (m.role === 'user' || m.role === 'assistant')) bubble(m.role, text)
  }
  bubble('meta', '— resumed —')
}

$('newChat').onclick = () => {
  sessionKey = `app-demo-${Math.random().toString(36).slice(2, 10)}`
  $('messages').replaceChildren()
  $('sessionPick').value = ''
}

// ---- chat: send + streamed run ------------------------------------------------------------------
$('composer').onsubmit = async (e) => {
  e.preventDefault()
  const text = $('prompt').value.trim()
  if (!text) return
  $('prompt').value = ''
  bubble('user', text)
  liveBubble = null
  if (stopRun) stopRun()
  stopRun = client.onRun(sessionKey, onRunEvent)
  $('abort').disabled = false
  try {
    await client.send({ message: text, sessionKey, agentId: AGENT_ID || undefined })
  } catch (err) {
    bubble('meta', `send failed: ${err.message}`)
    $('abort').disabled = true
  }
}

function onRunEvent({ event }) {
  if (event.type === 'message_update' && event.kind === 'text_delta') {
    if (!liveBubble) liveBubble = bubble('assistant', '')
    liveBubble.textContent += event.delta || ''
    $('messages').scrollTop = $('messages').scrollHeight
  } else if (event.type === 'message_end') {
    liveBubble = null
  } else if (event.type === 'tool_execution_start') {
    event._chip = chip(`⚙ ${event.toolName || 'tool'}…`)
  } else if (event.type === 'tool_execution_end') {
    const done = chip(`✓ ${event.toolName || 'tool'}`)
    done.classList.add('done')
    for (const a of event.artifacts || []) $('messages').appendChild(artifactNode(a))
  } else if (event.type === 'agent_end') {
    if (event.stopReason && event.stopReason !== 'end_turn') {
      bubble('meta', `— run ended: ${event.stopReason} —`)
    }
    $('abort').disabled = true
    void refreshSessions() // pick up the auto-generated title
  }
}

$('abort').onclick = () => void client.abort(sessionKey)

// ---- direct invoke (no LLM) ----------------------------------------------------------------------
async function refreshTools() {
  const { capabilities = [] } = await client.capabilities(AGENT_ID || undefined)
  const tools = capabilities.filter((c) => c.kind === 'tool')
  const pick = $('toolPick')
  pick.replaceChildren()
  for (const t of tools) {
    const opt = new Option(t.id, t.id)
    opt.title = t.description
    pick.appendChild(opt)
  }
}

$('runTool').onclick = async () => {
  const name = $('toolPick').value
  if (!name) return
  let params
  try {
    params = JSON.parse($('toolParams').value || '{}')
  } catch {
    $('toolOut').textContent = 'params must be valid JSON'
    return
  }
  $('toolOut').textContent = `running ${name}…`
  $('toolArts').replaceChildren()
  try {
    const out = await client.invokeTool(name, params)
    $('toolOut').textContent = out.text || '(no text)'
    for (const a of out.artifacts || []) $('toolArts').appendChild(artifactNode(a))
  } catch (err) {
    $('toolOut').textContent = `error: ${err.message}`
  }
}

// ---- the invariant: host-tier methods are denied on this scoped connection ----------------------
$('tryConfig').onclick = async () => {
  try {
    await client.request('config.get')
    $('denied').textContent = 'unexpectedly allowed?! (connection is not scoped)'
  } catch (err) {
    $('denied').textContent = `✋ ${err.message}`
  }
}
