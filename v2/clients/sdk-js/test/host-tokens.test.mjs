// Tokens pushed DOWN from the desktop shell — and the one they must never be applied to.
//
// An app window opened by the shell holds no refresh token (it runs third-party code, and a
// refresh token is a 30-day credential for the whole account), so it cannot renew itself. The
// shell, which does hold one, mints short-lived access tokens and broadcasts them to every open
// app window.
//
// EVERY window. That is the trap this file pins. A window where the user signed in through the
// app's own form is a DIFFERENT account on purpose — one daemon, many sockets, many answers — and
// the shell has no way to know. Accepting the broadcast there used to write this account's email
// and refresh token beside the other account's access token; `auth.update` was then refused by
// the daemon for mismatching the connection, and the reconnect in the catch presented the token
// just stored — silently moving the window onto the shell's account while it went on displaying
// the signed-in one's email.

import assert from 'node:assert/strict'
import { beforeEach, test } from 'node:test'

import { acceptHostTokens, loadSession, saveSession } from '../dist/index.js'

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
}

function jwt(sub, expiresInS = 600) {
  const b64 = (o) => Buffer.from(JSON.stringify(o)).toString('base64url')
  return `${b64({ alg: 'EdDSA', typ: 'JWT' })}.${b64({
    sub,
    exp: Math.floor(Date.now() / 1000) + expiresInS
  })}.notasignature`
}

/** The desktop preload's one receive-only channel (clients/desktop/src/preload/app.ts). */
function fakeShell() {
  const subscribers = new Set()
  globalThis.agentdHost = {
    onAccessToken(cb) {
      subscribers.add(cb)
      return () => subscribers.delete(cb)
    }
  }
  return { push: (token) => subscribers.forEach((cb) => cb(token)) }
}

/** Records what the SDK tried to do to the live socket. */
function fakeClient() {
  const calls = []
  return {
    calls,
    request(method, params) {
      calls.push({ method, params })
      return Promise.resolve({})
    },
    reconnect() {
      calls.push({ method: 'reconnect' })
    }
  }
}

beforeEach(() => {
  store.clear()
  globalThis.location = { href: 'http://run.example:8787/apps/agent-builder/' }
  delete globalThis.agentdHost
})

test('a window running on the SHELL account takes every pushed token', () => {
  // No accountId: this window adopted its credential from the launch url (client.ts fromPage),
  // so it has nothing to disagree with and the push is the only thing keeping it alive.
  saveSession({ token: jwt('acct_X'), email: '', accountId: '' })
  const shell = fakeShell()
  const client = fakeClient()
  acceptHostTokens({ client })

  const next = jwt('acct_X')
  shell.push(next)

  assert.equal(loadSession().token, next)
  assert.deepEqual(client.calls[0], { method: 'auth.update', params: { accessToken: next } })
})

test("a window signed in as SOMEBODY ELSE ignores the shell's token", () => {
  saveSession({
    token: jwt('acct_Y'),
    email: 'y@example.com',
    accountId: 'acct_Y',
    refreshToken: 'r_Y'
  })
  const shell = fakeShell()
  const client = fakeClient()
  acceptHostTokens({ client })

  shell.push(jwt('acct_X')) // the shell renewing ITS account, broadcast to every window

  const after = loadSession()
  assert.equal(after.accountId, 'acct_Y', 'the window is still Y')
  assert.equal(after.refreshToken, 'r_Y', "and still holds Y's own credential")
  assert.notEqual(accountOf(after.token), 'acct_X', "X's token was never adopted")
  assert.deepEqual(client.calls, [], 'nothing was pushed at the live socket')
})

test('a pushed token for the SAME account is still taken', () => {
  saveSession({
    token: jwt('acct_Y'),
    email: 'y@example.com',
    accountId: 'acct_Y',
    refreshToken: 'r_Y'
  })
  const shell = fakeShell()
  const client = fakeClient()
  acceptHostTokens({ client })

  const next = jwt('acct_Y')
  shell.push(next)

  assert.equal(loadSession().token, next)
  assert.equal(loadSession().refreshToken, 'r_Y', 'renewing must not drop the refresh token')
})

test('an unattributable token is refused — fail closed', () => {
  saveSession({ token: jwt('acct_Y'), email: '', accountId: 'acct_Y' })
  const shell = fakeShell()
  acceptHostTokens({ client: fakeClient() })

  shell.push('not.a.jwt')

  assert.equal(accountOf(loadSession().token), 'acct_Y')
})

test('a pushed token carries its own expiry, so an unattended window knows when it dies', () => {
  saveSession({ token: jwt('acct_X'), email: '', accountId: '' })
  const shell = fakeShell()
  acceptHostTokens({ client: fakeClient() })

  shell.push(jwt('acct_X', 600))

  const life = loadSession().expiresAt - Date.now()
  assert.ok(life > 590_000 && life < 601_000, `expected ~10 minutes of life, got ${life}ms`)
})

test('without the shell bridge this is a no-op, not a crash (a browser tab)', () => {
  saveSession({ token: jwt('acct_X'), email: '', accountId: '' })
  const stop = acceptHostTokens({ client: fakeClient() })
  assert.equal(typeof stop, 'function')
  stop()
})

function accountOf(token) {
  return JSON.parse(Buffer.from(token.split('.')[1], 'base64url').toString()).sub
}
