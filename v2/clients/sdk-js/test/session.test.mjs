// The stored session — WHERE a credential is kept, and whose it is.
//
// Replaces platform.test.mjs, which tested `platform.ts`. That module is gone: sign-in is now
// ordinary HTTP (auth.ts) and identity is presented per connection, so `resolveAuth`,
// `platformConnect`, `keysLive` and the lost-reply poll no longer exist to test. What survives
// from that file is the part that still decides something — the storage KEY, because it is what
// keeps two agents' sessions apart — plus the run-mode default.
//
// THE BUG PINNED HERE. The key is derived from `?scope=agent:<id>`, which only exists when an
// OPENER built the url. A page reached from a marketplace card is a bare `/apps/<id>/`, so
// without a path fallback every web-delivered agent on one origin shared the key
// `agentd.session.app` — one agent's session silently becoming another's.

import assert from 'node:assert/strict'
import { test, beforeEach } from 'node:test'

import {
  effectiveMode,
  loadMode,
  loadSession,
  saveMode,
  saveSession
} from '../dist/index.js'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
}

function at(href) {
  globalThis.location = { href }
}

beforeEach(() => {
  store.clear()
  delete globalThis.location
})

// ── which key ───────────────────────────────────────────────────────────────

test('an opener-built url keys off ?scope=', () => {
  at('http://127.0.0.1:8787/apps/game-master/?scope=agent:game-master&token=T')
  saveSession({ token: 'sess_gm', email: 'a@b.c', accountId: 'acct_1' })
  assert.ok(store.has('agentd.session.game-master'))
  assert.equal(loadSession().token, 'sess_gm')
})

test('a BARE marketplace link keys off the /apps/<id>/ path', () => {
  // No ?scope=, no ?token= — this is what a store card links to.
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: 'sess_bk', email: 'a@b.c', accountId: 'acct_1' })
  assert.ok(store.has('agentd.session.bedtime-kids'), 'the path must name the agent')
  assert.ok(!store.has('agentd.session.app'), 'never the shared fallback key')
})

test('two web-delivered agents on one origin never share a session', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: 'sess_bk', email: 'a@b.c', accountId: 'acct_1' })
  at('http://run.example:8787/apps/game-master/')
  assert.equal(loadSession(), null, "another agent's session is not mine")
  saveSession({ token: 'sess_gm', email: 'x@y.z', accountId: 'acct_2' })

  at('http://run.example:8787/apps/bedtime-kids/')
  assert.equal(loadSession().token, 'sess_bk')
  at('http://run.example:8787/apps/game-master/')
  assert.equal(loadSession().token, 'sess_gm')
})

test('an explicit storage key always wins', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: 'sess_x', email: '', accountId: '' }, 'custom.key')
  assert.ok(store.has('custom.key'))
  assert.equal(loadSession('custom.key').token, 'sess_x')
})

test('a page that is not an app falls back to one shared key', () => {
  at('http://127.0.0.1:8787/')
  saveSession({ token: 'sess_shell', email: '', accountId: '' })
  assert.ok(store.has('agentd.session.app'))
})

// ── reading it back ─────────────────────────────────────────────────────────

test('a stored blob with no token is not a session', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  store.set('agentd.session.bedtime-kids', JSON.stringify({ email: 'a@b.c' }))
  assert.equal(loadSession(), null)
})

test('null clears it', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: 'sess_bk', email: '', accountId: '' })
  saveSession(null)
  assert.equal(loadSession(), null)
})

test('storage being unavailable is not a failure', () => {
  const real = globalThis.localStorage
  globalThis.localStorage = {
    getItem: () => { throw new Error('denied') },
    setItem: () => { throw new Error('denied') },
    removeItem: () => { throw new Error('denied') }
  }
  try {
    assert.equal(loadSession(), null)
    saveSession({ token: 'x', email: '', accountId: '' }) // must not throw: private mode is not an error
  } finally {
    globalThis.localStorage = real
  }
})

// ── run mode ────────────────────────────────────────────────────────────────

test('mode is remembered per agent', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveMode('local')
  assert.equal(loadMode(), 'local')
  at('http://run.example:8787/apps/game-master/')
  assert.equal(loadMode(), null, 'one agent choosing local does not move another')
})

test('junk in storage is not a mode', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  store.set('agentd.session.bedtime-kids.mode', 'kiosk')
  assert.equal(loadMode(), null)
})

test('the default is cloud once signed in, and only where a cloud exists', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  assert.equal(effectiveMode('', false, true), 'local', 'signed out pays with its own keys')
  assert.equal(effectiveMode('', true, true), 'cloud')
  assert.equal(effectiveMode('', true, false), 'local', 'no proxy on this build => local')
})

test('an explicit choice beats the default', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveMode('local')
  assert.equal(effectiveMode('', true, true), 'local')
  saveMode(null)
  assert.equal(effectiveMode('', true, true), 'cloud', 'clearing restores the default')
})
