/**
 * A hosted socket's access token is renewed BEFORE it expires, on a clock — not only when some
 * hook on the page happens to ask for identity.
 *
 * The defect this pins: a window opened and left alone for an hour sent its first model call
 * with a dead token, because nothing on that page had asked `state()` and so nothing had pushed
 * a fresh one down the socket. Written against the built bundle, like the other identity tests:
 * what ships to an agent is the bundle.
 */
import assert from 'node:assert/strict'
import { afterEach, beforeEach, describe, it, mock } from 'node:test'
import { identity } from '../dist/index.js'

function json(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
  }
}

/** A hosted daemon (no /auth/token) fronting an accounts service that hands out `expiresIn`
 *  second tokens, numbered by call so a renewal is visible as a different token. */
function hostedFetch(expiresIn) {
  let refreshes = 0
  const fn = async (url) => {
    const u = String(url)
    if (u.includes('/auth/token')) return json({}, 404)
    if (u.includes('/platform/status')) return json({ accountsUrl: 'https://accounts.test' })
    if (u.includes('/auth/refresh')) {
      refreshes += 1
      return json({
        access_token: `tok-${refreshes}`,
        expires_in: expiresIn,
        account_id: 'acct_1',
        email: 'a@b.c',
      })
    }
    throw new Error(`unexpected fetch ${u}`)
  }
  fn.refreshes = () => refreshes
  return fn
}

/** Yield to the real event loop a few times so every await in a fetch chain settles. */
async function drain() {
  for (let i = 0; i < 20; i++) await new Promise((r) => setImmediate(r))
}

/** The only part of a client the fetcher touches: where `auth.update` lands. */
function fakeClient() {
  const pushed = []
  return {
    pushed,
    request: async (method, params) => {
      pushed.push({ method, params })
      return {}
    },
  }
}

describe('the socket token is renewed before it expires', () => {
  let n = 0
  beforeEach(() => {
    mock.timers.enable({ apis: ['setTimeout', 'Date'] })
  })
  afterEach(() => {
    mock.timers.reset()
  })

  it('re-resolves two minutes before expiry and pushes the new token', async () => {
    const fetch = hostedFetch(3600)
    globalThis.fetch = fetch
    const client = fakeClient()
    const origin = `https://daemon-${++n}.test`

    const first = await identity({ origin, client }).state()
    assert.equal(first.accessToken, 'tok-1')
    assert.equal(fetch.refreshes(), 1)
    assert.deepEqual(
      client.pushed.map((p) => p.params.accessToken),
      ['tok-1'],
      'boot pushes the first token',
    )

    // Nothing asks for identity for the next 57 minutes.
    mock.timers.tick(57 * 60 * 1000)
    assert.equal(fetch.refreshes(), 1, 'no renewal yet — the token is still good')

    // ...but at T-120s the fetcher renews on its own.
    mock.timers.tick(60 * 1000 + 1000)
    // Let the fetch/promise chain drain. setImmediate is not mocked, so this yields to the
    // real event loop until the refresh has been read and pushed.
    await drain()
    assert.equal(fetch.refreshes(), 2, 'renewed before expiry with nobody asking')
    assert.deepEqual(
      client.pushed.map((p) => p.params.accessToken),
      ['tok-1', 'tok-2'],
      'the fresh token went down the socket',
    )
  })

  it('never arms without a socket to push to', async () => {
    const fetch = hostedFetch(3600)
    globalThis.fetch = fetch
    const origin = `https://daemon-${++n}.test`

    await identity({ origin }).state()
    mock.timers.tick(2 * 3600 * 1000)
    await drain()
    assert.equal(fetch.refreshes(), 1, 'a bare script is not a window: nothing to keep fresh')
  })
})
