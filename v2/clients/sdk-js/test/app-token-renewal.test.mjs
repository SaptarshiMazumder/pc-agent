import assert from 'node:assert/strict'
import { test } from 'node:test'
import { identity, resetIdentity } from '../dist/index.js'

test('a renewed scoped app credential reaches an existing socket without a cookie refresh', async () => {
  const priorWindow = globalThis.window
  const priorFetch = globalThis.fetch
  const listeners = new Map()
  const pushes = []
  globalThis.window = {
    __agentdAppToken: { state: 'ok', accessToken: 'own-app-first', accountId: 'acct_own', expiresAt: Date.now() / 1000 + 600 },
    addEventListener: (name, callback) => listeners.set(name, callback),
  }
  globalThis.fetch = () => { throw new Error('app must not refresh an account cookie') }
  try {
    resetIdentity()
    const client = { status: 'connected', request: async (method, params) => { pushes.push({ method, params }); return {} } }
    const handle = identity({ origin: 'https://own-app.example.invalid', client })
    assert.equal(await handle.accessToken(), 'own-app-first')
    globalThis.window.__agentdAppToken.accessToken = 'own-app-renewed'
    listeners.get('agentd:app-token')()
    await new Promise(resolve => setImmediate(resolve))
    assert.equal(await handle.accessToken(), 'own-app-renewed')
    assert.ok(pushes.some(p => p.method === 'auth.update' && p.params.accessToken === 'own-app-renewed'))
  } finally {
    globalThis.window = priorWindow
    globalThis.fetch = priorFetch
    resetIdentity()
  }
})
