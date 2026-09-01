/**
 * The three defects that made an agent window sign itself out, pinned so they cannot come back.
 *
 * Each of these was live in the SDK's own copy of sign-in, and each one is why this logic is now
 * in ONE place. They are written against the built bundle rather than the source, because what
 * ships to an agent is the bundle — a fix that never reaches `vendor/agentd-client.js` has not
 * been made.
 */

import assert from 'node:assert/strict'
import { after, beforeEach, describe, it } from 'node:test'
import { TokenManager, memorySessionStore } from '../dist/index.js'

/** A JWT whose `exp` is `secondsFromNow`. Unsigned — nothing here verifies, by design. */
function token(secondsFromNow, sub = 'acct_1') {
  const body = Buffer.from(
    JSON.stringify({ sub, exp: Math.floor(Date.now() / 1000) + secondsFromNow })
  ).toString('base64url')
  return `header.${body}.signature`
}

/** A fetch that records every call and answers from a scripted queue. */
function stubFetch(handler) {
  const calls = []
  const fn = async (url, init) => {
    calls.push({ url: String(url), body: JSON.parse(init.body) })
    return handler(String(url), calls.length)
  }
  fn.calls = calls
  return fn
}

function json(data, status = 200) {
  return {
    ok: status >= 200 && status < 300,
    status,
    json: async () => data,
    text: async () => JSON.stringify(data)
  }
}

function build(fetchImpl, stored) {
  const session = memorySessionStore()
  if (stored) session.write(JSON.stringify(stored))
  return new TokenManager({
    accountsUrl: () => 'https://accounts.test',
    session,
    clientId: 'app',
    fetchImpl
  })
}

describe('TokenManager', () => {
  const managers = []
  const track = (m) => (managers.push(m), m)
  after(() => managers.forEach((m) => m.stop()))

  describe('an access token that has ALREADY expired', () => {
    // THE BUG: the old guard was `life > 0 && life < 10min`, so a token that had actually died was
    // the one case renewal declined to handle — and it never handled it again. A sleeping laptop,
    // a throttled background tab or a long agent run was enough. The window then sat holding a
    // dead credential forever, which the daemon does not refuse: it accepts the reconnect
    // ANONYMOUSLY, so the user watched their agents vanish with no error and no sign-in form.
    it('is renewed rather than abandoned', async () => {
      const fetchImpl = stubFetch(() =>
        json({ access_token: token(600), refresh_token: 'r2', expires_in: 600 })
      )
      const m = track(
        build(fetchImpl, {
          accessToken: token(-3600), // an hour dead
          refreshToken: 'r1',
          expiresAt: Date.now() - 3_600_000,
          accountId: 'acct_1',
          email: 'a@b.c'
        })
      )

      const fresh = await m.accessToken()

      assert.equal(fetchImpl.calls.length, 1, 'it must actually attempt the refresh')
      assert.match(fetchImpl.calls[0].url, /\/auth\/refresh$/)
      assert.equal(fresh, m.current().accessToken)
      assert.ok(fresh && fresh !== token(-3600), 'it must hand back the NEW token')
    })

    it('still counts as signed in while a refresh token remains', () => {
      const m = track(
        build(stubFetch(() => json({})), {
          accessToken: token(-3600),
          refreshToken: 'r1',
          expiresAt: Date.now() - 3_600_000
        })
      )
      // Reporting this as signed out would put a login form in front of somebody who never left —
      // renewal is one HTTP call away.
      assert.equal(m.signedIn(), true)
    })

    it('counts as signed OUT when there is nothing to renew with', () => {
      // A window opened by the desktop app holds no refresh token. Once its token dies it is
      // genuinely over, and saying so is what produces a visible sign-in prompt instead of a page
      // quietly running as nobody.
      const m = track(
        build(stubFetch(() => json({})), {
          accessToken: token(-3600),
          refreshToken: '',
          expiresAt: Date.now() - 3_600_000
        })
      )
      assert.equal(m.signedIn(), false)
    })
  })

  describe('concurrent refreshes', () => {
    // THE BUG: refresh tokens are single-use and ROTATING, and the server reads a second use as
    // theft — it revokes the whole family, signing the user out of everything. The old code had no
    // guard at all, so two agent windows waking together were enough to do it. This is the test
    // that says why the manager is memoised per storage key too.
    it('collapse into ONE request, however many callers ask', async () => {
      let served = 0
      const fetchImpl = stubFetch(() => {
        served += 1
        return json({ access_token: token(600), refresh_token: `r${served + 1}`, expires_in: 600 })
      })
      const m = track(
        build(fetchImpl, {
          accessToken: token(-60),
          refreshToken: 'r1',
          expiresAt: Date.now() - 60_000
        })
      )

      const results = await Promise.all([m.refresh(), m.refresh(), m.refresh(), m.accessToken()])

      assert.equal(fetchImpl.calls.length, 1, 'a second use of a rotating token revokes the family')
      assert.equal(results[0].accessToken, results[1].accessToken)
    })
  })

  describe('a refresh the server refuses', () => {
    it('ends the session, because retrying a dead credential never recovers', async () => {
      const m = track(
        build(stubFetch(() => json({ detail: 'reuse detected' }, 401)), {
          accessToken: token(-60),
          refreshToken: 'r1',
          expiresAt: Date.now() - 60_000
        })
      )

      assert.equal(await m.refresh(), null)
      assert.equal(m.current(), null, 'the credential must be cleared so a form is shown')
      assert.equal(m.signedIn(), false)
    })
  })

  describe('a refresh that merely FAILED', () => {
    it('keeps the credential — being offline is not signing out', async () => {
      const m = track(
        build(
          stubFetch(() => {
            throw new Error('network down')
          }),
          {
            accessToken: token(-60),
            refreshToken: 'r1',
            expiresAt: Date.now() - 60_000,
            accountId: 'acct_1',
            email: 'a@b.c'
          }
        )
      )

      assert.equal(await m.refresh(), null)
      assert.equal(m.current()?.refreshToken, 'r1', 'a flaky network must not sign anyone out')
      assert.equal(m.signedIn(), true)
    })

    it('says the same for a 500', async () => {
      const m = track(
        build(stubFetch(() => json({ detail: 'boom' }, 500)), {
          accessToken: token(-60),
          refreshToken: 'r1',
          expiresAt: Date.now() - 60_000
        })
      )
      await m.refresh()
      assert.equal(m.current()?.refreshToken, 'r1')
    })
  })

  describe('sign-in', () => {
    it('posts to /auth/login and names the device', async () => {
      // THE BUG: this client posted to `/login` while the agentd client posted to `/auth/login`.
      // Same credential, but the alias takes no `client_id`, so agent windows never appeared in
      // the user's own device list and the server could not tell two of them apart.
      const fetchImpl = stubFetch(() =>
        json({
          access_token: token(600),
          refresh_token: 'r1',
          expires_in: 600,
          account_id: 'acct_1',
          email: 'a@b.c'
        })
      )
      const m = track(build(fetchImpl))

      await m.login({ email: '  A@B.C  ', password: 'pw' })

      assert.equal(fetchImpl.calls.length, 1)
      assert.equal(fetchImpl.calls[0].url, 'https://accounts.test/auth/login')
      assert.equal(fetchImpl.calls[0].body.email, 'a@b.c', 'trimmed and lowercased')
      assert.equal(fetchImpl.calls[0].body.client_id, 'app')
      assert.ok(fetchImpl.calls[0].body.device_label)
      assert.equal(m.signedIn(), true)
    })

    it('creates the account first when asked, then signs in', async () => {
      const fetchImpl = stubFetch((url) =>
        url.endsWith('/signup')
          ? json({ ok: true })
          : json({ access_token: token(600), refresh_token: 'r1', expires_in: 600 })
      )
      const m = track(build(fetchImpl))

      await m.login({ email: 'a@b.c', password: 'pw', signup: true })

      assert.deepEqual(
        fetchImpl.calls.map((c) => new URL(c.url).pathname),
        ['/signup', '/auth/login']
      )
    })

    it('THROWS on a rejected password rather than reporting itself signed out', async () => {
      // A caller cannot tell "wrong password" from "signed out", and the user is left looking at a
      // form that cleared itself.
      const m = track(build(stubFetch(() => json({ detail: 'incorrect password' }, 401))))

      await assert.rejects(() => m.login({ email: 'a@b.c', password: 'no' }), /incorrect password/)
    })
  })

  describe('adopting a token pushed by the desktop app', () => {
    it('is refused when this window belongs to somebody else', async () => {
      // The push reaches EVERY open window at once and cannot know one of them signed in as a
      // different account. Taking it would land the window on the pusher's account while still
      // showing this one's email.
      const m = track(
        build(stubFetch(() => json({})), {
          accessToken: token(600, 'acct_MINE'),
          refreshToken: '',
          expiresAt: Date.now() + 600_000,
          accountId: 'acct_MINE',
          email: 'mine@b.c'
        })
      )

      assert.equal(await m.adopt(token(600, 'acct_OTHER')), false)
      assert.equal(m.current().accountId, 'acct_MINE')
    })

    it('is accepted by a window that has no account of its own', async () => {
      // The ordinary case: a window opened BY the desktop app took its credential from the launch
      // url and recorded no account, so it has nothing to disagree with.
      const m = track(build(stubFetch(() => json({}))))
      const pushed = token(600, 'acct_1')

      assert.equal(await m.adopt(pushed), true)
      assert.equal(m.current().accessToken, pushed)
      assert.ok(m.current().expiresAt > Date.now(), 'expiry is read from the token itself')
    })

    it('fails closed on a credential it cannot read', async () => {
      const m = track(build(stubFetch(() => json({}))))
      assert.equal(await m.adopt('not-a-jwt'), false)
      assert.equal(m.current(), null)
    })
  })

  describe('a stored session from an older platform', () => {
    it('is evicted rather than presented forever', () => {
      // Opaque `sess_` sessions cannot be resolved by any current daemon. Keeping one meant the
      // page reported itself signed in and reconnect-looped against our own server.
      const session = memorySessionStore()
      session.write(JSON.stringify({ accessToken: 'sess_old', refreshToken: 'r1' }))
      const m = track(
        new TokenManager({
          accountsUrl: () => 'https://accounts.test',
          session,
          clientId: 'app',
          fetchImpl: stubFetch(() => json({}))
        })
      )

      assert.equal(m.current(), null)
      assert.equal(session.read(), null, 'evicted, not merely ignored')
    })
  })
})

describe('a window that was handed a token and no key', () => {
  it('mints a session of its own, then renews itself', async () => {
    // The state a desktop-opened agent window boots in: a live access token, nothing behind it.
    const store = memorySessionStore()
    store.write(
      JSON.stringify({ accessToken: token(300), refreshToken: '', expiresAt: Date.now() + 300_000 })
    )
    const calls = []
    const manager = new TokenManager({
      accountsUrl: () => 'https://accounts.test',
      session: store,
      clientId: 'app',
      fetchImpl: async (url) => {
        calls.push(new URL(url).pathname)
        return json({ access_token: token(600), refresh_token: 'own-key-1', expires_in: 600 })
      }
    })

    assert.equal(manager.current()?.refreshToken, '', 'starts with no key of its own')
    await manager.restore()

    assert.deepEqual(calls, ['/auth/derive'], 'derives rather than refreshing')
    assert.equal(manager.current()?.refreshToken, 'own-key-1', 'now holds its own key')
    manager.stop()
  })

  it('keeps working when the server declines', async () => {
    // Degrading matters more than deriving: a window that cannot mint a key is no worse off than
    // it was, and must not fail to start over it.
    const store = memorySessionStore()
    store.write(
      JSON.stringify({ accessToken: token(300), refreshToken: '', expiresAt: Date.now() + 300_000 })
    )
    const manager = new TokenManager({
      accountsUrl: () => 'https://accounts.test',
      session: store,
      clientId: 'app',
      fetchImpl: async () => json({ detail: 'nope' }, 401)
    })

    await manager.restore()
    assert.ok(manager.current()?.accessToken, 'still holds the token it arrived with')
    manager.stop()
  })

  it('does not trade a DEAD token — that would make expiry meaningless', async () => {
    const store = memorySessionStore()
    let called = false
    const manager = new TokenManager({
      accountsUrl: () => 'https://accounts.test',
      session: store,
      clientId: 'app',
      fetchImpl: async () => {
        called = true
        return json({})
      }
    })
    store.write(JSON.stringify({ accessToken: token(-60), refreshToken: '', expiresAt: Date.now() - 60_000 }))

    await manager.restore()
    assert.equal(called, false, 'a spent token proves nothing and must buy nothing')
    manager.stop()
  })
})
