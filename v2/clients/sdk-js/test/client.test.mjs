// SDK unit tests against a STUB WebSocket (no network, no deps — node --test).
// Covers: URL building (token/scope/ws-upgrade), id-matched request/response, error
// rejection, onRun/onAgent event filtering, and fileUrl construction.
import assert from 'node:assert/strict'
import { test } from 'node:test'

// --- stub WHATWG WebSocket ------------------------------------------------------------
class StubWebSocket {
  static OPEN = 1
  static last = null
  constructor(url) {
    this.url = url
    this.readyState = StubWebSocket.OPEN
    this.sent = []
    this.onopen = null
    this.onmessage = null
    this.onclose = null
    this.onerror = null
    StubWebSocket.last = this
    queueMicrotask(() => this.onopen && this.onopen())
  }
  send(data) {
    this.sent.push(JSON.parse(data))
  }
  close() {}
  // test helper: push a frame from the "server"
  receive(frame) {
    this.onmessage && this.onmessage({ data: JSON.stringify(frame) })
  }
}
globalThis.WebSocket = StubWebSocket

const { AgentdClient, PROTOCOL_VERSION } = await import('../dist/index.js')

const tick = () => new Promise((r) => setTimeout(r, 0))

async function connected(target) {
  const client = new AgentdClient({ clientName: 'test/1' })
  client.connect(target)
  await tick()
  return { client, ws: StubWebSocket.last }
}

test('connect builds ws url with token + scope and upgrades http', async () => {
  const { ws } = await connected({
    url: 'http://127.0.0.1:8787',
    token: 'T',
    scope: 'agent:demo'
  })
  const u = new URL(ws.url)
  assert.equal(u.protocol, 'ws:')
  assert.equal(u.searchParams.get('token'), 'T')
  assert.equal(u.searchParams.get('scope'), 'agent:demo')
})

test('request resolves on ok:true and rejects on ok:false', async () => {
  const { client, ws } = await connected({ url: 'ws://h:1' })
  const p = client.request('agents.list')
  const sentId = ws.sent[0].id
  ws.receive({ type: 'res', id: sentId, ok: true, payload: { agents: [] } })
  assert.deepEqual(await p, { agents: [] })

  const bad = client.request('config.get')
  ws.receive({ type: 'res', id: ws.sent[1].id, ok: false, payload: { error: 'denied' } })
  await assert.rejects(bad, /denied/)
})

test('hello sends protocol + client name', async () => {
  const { client, ws } = await connected({ url: 'ws://h:1' })
  void client.hello()
  assert.equal(ws.sent[0].method, 'hello')
  assert.equal(ws.sent[0].params.protocol, PROTOCOL_VERSION)
  assert.equal(ws.sent[0].params.client, 'test/1')
})

test('onRun filters by sessionKey; onAgent by agentId', async () => {
  const { client, ws } = await connected({ url: 'ws://h:1' })
  const mine = []
  const forAgent = []
  client.onRun('s1', (p) => mine.push(p.event.type))
  client.onAgent('helper', (p) => forAgent.push(p.event.type))
  const ev = (sessionKey, agentId, type) => ({
    type: 'event',
    event: 'chat.event',
    payload: { sessionKey, runId: 'r', agentId, ts: 0, event: { type } }
  })
  ws.receive(ev('s1', 'main', 'turn_start'))
  ws.receive(ev('other', 'helper', 'message_update'))
  ws.receive(ev('s1', 'main', 'agent_end'))
  assert.deepEqual(mine, ['turn_start', 'agent_end'])
  assert.deepEqual(forAgent, ['message_update'])
})

test('fileUrl derives the http origin and carries the token', async () => {
  const { client } = await connected({ url: 'ws://127.0.0.1:8787', token: 'T' })
  const u = new URL(client.fileUrl('C:/x/y.png'))
  assert.equal(u.origin, 'http://127.0.0.1:8787')
  assert.equal(u.pathname, '/file')
  assert.equal(u.searchParams.get('path'), 'C:/x/y.png')
  assert.equal(u.searchParams.get('token'), 'T')
})
