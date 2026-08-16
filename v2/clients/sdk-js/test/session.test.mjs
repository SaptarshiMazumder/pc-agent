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
  accessTokenExpiry,
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

/**
 * A token of the shape the platform actually issues: a signed JWT, three parts, with an `exp`.
 *
 * The fixtures here used to be `sess_...` strings — the OPAQUE sessions that came before token
 * auth. `usable()` refuses those now (a stored one is a guarantee of failure, not a session), so
 * every test that read one back was asserting against a shape the SDK is right to evict.
 * Nothing is signed: the SDK reads `exp` and never verifies, which is the daemon's job.
 */
function jwt(expiresInS = 600) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'EdDSA', typ: 'JWT' })}.${b64({
    sub: 'acct_1',
    exp: Math.floor(Date.now() / 1000) + expiresInS
  })}.notasignature`
}

beforeEach(() => {
  store.clear()
  delete globalThis.location
})

// ── which key ───────────────────────────────────────────────────────────────

test('an opener-built url keys off ?scope=', () => {
  at('http://127.0.0.1:8787/apps/game-master/?scope=agent:game-master&token=T')
  const gm = jwt()
  saveSession({ token: gm, email: 'a@b.c', accountId: 'acct_1' })
  assert.ok(store.has('agentd.session.game-master'))
  assert.equal(loadSession().token, gm)
})

test('a BARE marketplace link keys off the /apps/<id>/ path', () => {
  // No ?scope=, no ?token= — this is what a store card links to.
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: jwt(), email: 'a@b.c', accountId: 'acct_1' })
  assert.ok(store.has('agentd.session.bedtime-kids'), 'the path must name the agent')
  assert.ok(!store.has('agentd.session.app'), 'never the shared fallback key')
})

test('two web-delivered agents on one origin never share a session', () => {
  const bk = jwt()
  const gm = jwt()
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: bk, email: 'a@b.c', accountId: 'acct_1' })
  at('http://run.example:8787/apps/game-master/')
  assert.equal(loadSession(), null, "another agent's session is not mine")
  saveSession({ token: gm, email: 'x@y.z', accountId: 'acct_2' })

  at('http://run.example:8787/apps/bedtime-kids/')
  assert.equal(loadSession().token, bk)
  at('http://run.example:8787/apps/game-master/')
  assert.equal(loadSession().token, gm)
})

test('an explicit storage key always wins', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  const t = jwt()
  saveSession({ token: t, email: '', accountId: '' }, 'custom.key')
  assert.ok(store.has('custom.key'))
  assert.equal(loadSession('custom.key').token, t)
})

test('a page that is not an app falls back to one shared key', () => {
  at('http://127.0.0.1:8787/')
  saveSession({ token: jwt(), email: '', accountId: '' })
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
  saveSession({ token: jwt(), email: '', accountId: '' })
  saveSession(null)
  assert.equal(loadSession(), null)
})

// ── a credential that has run out ───────────────────────────────────────────
//
// THE "LOGGED OUT AFTER TEN MINUTES" REPORT. A shell-opened app window is handed an access token
// on its launch url and holds no refresh token, so it cannot renew. The daemon does not REFUSE the
// expired token on the next reconnect — it accepts the page anonymously — so the window went on
// calling itself signed in while the account's agents silently disappeared from it.

test('an expired token with no way to renew is not a session', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: jwt(-60), email: 'a@b.c', accountId: 'acct_1' })
  assert.equal(loadSession(), null, 'the page must show a sign-in form, not go quietly anonymous')
  assert.ok(!store.has('agentd.session.bedtime-kids'), 'and it is EVICTED, not merely ignored')
})

test('an expired token WITH a refresh token survives — renewal is the fix, not sign-out', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: jwt(-60), email: 'a@b.c', accountId: 'acct_1', refreshToken: 'r_1' })
  assert.equal(loadSession()?.refreshToken, 'r_1', 'one HTTP call from being fine')
})

test('a token close to expiry is spent, so the prompt beats the first failed request', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  saveSession({ token: jwt(5), email: '', accountId: '' })
  assert.equal(loadSession(), null)
})

test('a live token is untouched', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  const live = jwt(600)
  saveSession({ token: live, email: '', accountId: '' })
  assert.equal(loadSession().token, live)
})

test('an explicit expiresAt is honored over the claim', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  // A long-lived token the CALLER knows is already spent (the shell stopped pushing renewals).
  saveSession({ token: jwt(3600), email: '', accountId: '', expiresAt: Date.now() - 1000 })
  assert.equal(loadSession(), null)
})

test('a token whose exp cannot be read is left alone', () => {
  at('http://run.example:8787/apps/bedtime-kids/')
  // Three parts, so `usable`, but the middle is not JSON. Evicting on an unreadable claim would
  // sign people out over a decoding quirk; the daemon is the authority on validity either way.
  saveSession({ token: 'aaa.bbb.ccc', email: '', accountId: '' })
  assert.equal(loadSession().token, 'aaa.bbb.ccc')
})

test('accessTokenExpiry reads exp, and reports 0 when it cannot', () => {
  const soon = jwt(120)
  const read = accessTokenExpiry(soon)
  assert.ok(Math.abs(read - (Date.now() + 120_000)) < 2000, 'within clock noise of the claim')
  assert.equal(accessTokenExpiry('not.a.jwt'), 0)
  assert.equal(accessTokenExpiry(''), 0)
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
