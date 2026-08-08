// Hosted sign-in, tested against a STUB fetch (no network, no DOM — node --test).
//
// The three behaviours here are the ones whose failure is SILENT, which is why they are pinned:
//
//   * a BYOK build must never be asked to sign in (no accountsUrl => no gate, ever)
//   * a lost /platform/connect reply must not read as a failed sign-in — the daemon rewrites its
//     own credential and reconfigures mid-request, so the work can succeed with the reply lost
//   * a HOSTED daemon REFUSES /platform/connect on purpose (keys are server-owned); treating that
//     refusal as an error would make the web deployment permanently unable to sign in
//
// Each of those, done wrong, produces an app that looks signed in and has no model access — or
// one that demands a login it has no server for.
import assert from 'node:assert/strict'
import { test, beforeEach } from 'node:test'

import {
  agentIdFromPage,
  loadSession,
  platformConnect,
  platformStatus,
  resolveAuth,
  saveSession,
  signIn,
  signOut
} from '../dist/index.js'

const ORIGIN = 'http://127.0.0.1:8787'
const OPTS = { origin: ORIGIN, confirmDelayMs: 1, confirmAttempts: 3, storageKey: 'test.session' }

// --- stubs ------------------------------------------------------------------------------
let routes = {}
let calls = []

function json(body, status = 200) {
  return { ok: status >= 200 && status < 300, status, text: async () => JSON.stringify(body) }
}

globalThis.fetch = async (url, init) => {
  const u = String(url)
  calls.push({ url: u, method: (init && init.method) || 'GET' })
  for (const [pattern, handler] of Object.entries(routes)) {
    if (u.includes(pattern)) return handler(u, init)
  }
  throw new Error(`unstubbed fetch: ${u}`)
}

const store = new Map()
globalThis.localStorage = {
  getItem: (k) => (store.has(k) ? store.get(k) : null),
  setItem: (k, v) => store.set(k, String(v)),
  removeItem: (k) => store.delete(k)
}

const status = (accountsUrl, enabled) => ({
  accountsUrl,
  modelProxy: { enabled },
  diagnostics: {}
})

beforeEach(() => {
  routes = {}
  calls = []
  store.clear()
})

// --- the BYOK case: no accounts, therefore no gate --------------------------------------

test('a build with no accountsUrl is not hosted and needs no sign-in', async () => {
  routes['/platform/status'] = () => json(status('', false))
  const s = await platformStatus(OPTS)
  assert.equal(s.hosted, false)
  assert.equal(s.keysLive, false)

  const auth = await resolveAuth(OPTS)
  assert.equal(auth.needsSignIn, false, 'a BYOK install must never be asked to sign in')
})

test('signIn refuses outright on a build with no accounts server', async () => {
  routes['/platform/status'] = () => json(status('', false))
  await assert.rejects(
    () => signIn({ email: 'a@b.c', password: 'password1' }, OPTS),
    /no sign-in server configured/
  )
})

// --- already connected ------------------------------------------------------------------

test('live platform keys mean no gate, even on a hosted build', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', true))
  const auth = await resolveAuth(OPTS)
  assert.equal(auth.needsSignIn, false)
})

test('the pre-rename modelGateway alias is still honoured', async () => {
  // An older daemon sends only modelGateway. Reading one name would report "not signed in"
  // forever there, and the gate would reappear on every launch of a working install.
  routes['/platform/status'] = () =>
    json({ accountsUrl: 'http://accounts.test', modelGateway: { enabled: true } })
  const s = await platformStatus(OPTS)
  assert.equal(s.keysLive, true)
})

test('hosted and not connected, with no stored session, needs a gate', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  const auth = await resolveAuth(OPTS)
  assert.equal(auth.needsSignIn, true)
})

// --- the lost-reply case ----------------------------------------------------------------

test('a lost connect reply is confirmed by polling status, not reported as failure', async () => {
  let statusCalls = 0
  routes['/platform/connect'] = () => {
    throw new Error('socket hang up') // the work SUCCEEDED; the reply did not arrive
  }
  routes['/platform/status'] = () => {
    statusCalls++
    return json(status('http://accounts.test', statusCalls >= 2)) // becomes live on the 2nd look
  }
  const s = await platformConnect('sess_abc', OPTS)
  assert.equal(s.keysLive, true)
  assert.ok(statusCalls >= 2, 'must have re-checked rather than trusted the lost reply')
})

test('a connect that never activates eventually fails with a human message', async () => {
  routes['/platform/connect'] = () => json(status('http://accounts.test', false))
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  await assert.rejects(() => platformConnect('sess_abc', OPTS), /did not activate/)
})

// --- the hosted-daemon refusal is SUCCESS ----------------------------------------------

test('a daemon that manages keys server-side counts as connected', async () => {
  // The exact refusal from gateway._platform_connect when accounts.enabled(). The session token
  // is the socket credential there; there is no local credential to bind and nothing to retry.
  routes['/platform/connect'] = () =>
    json({ error: 'this deployment manages platform keys server-side' }, 400)
  routes['/platform/status'] = () => json(status('http://accounts.test', false))

  const s = await platformConnect('sess_abc', OPTS)
  assert.equal(s.keysLive, true, 'a server-side-keys refusal must not read as a failed sign-in')
})

// --- the happy path ---------------------------------------------------------------------

test('signIn logs in, binds the device, and remembers the session', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/login'] = () => json({ token: 'sess_live' })
  routes['/platform/connect'] = () => json(status('http://accounts.test', true))

  const result = await signIn({ email: ' A@B.C ', password: 'password1' }, OPTS)
  assert.equal(result.token, 'sess_live')
  assert.equal(result.keysLive, true)
  assert.equal(loadSession(OPTS).token, 'sess_live')
  assert.equal(loadSession(OPTS).email, 'a@b.c', 'email is normalised before it is stored')

  const login = calls.find((c) => c.url.includes('/login'))
  assert.equal(login.method, 'POST')
  assert.ok(!calls.some((c) => c.url.includes('/signup')), 'must not sign up when not asked')
})

test('signup posts /signup before /login', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/signup'] = () => json({ ok: true })
  routes['/login'] = () => json({ token: 'sess_new' })
  routes['/platform/connect'] = () => json(status('http://accounts.test', true))

  await signIn({ email: 'a@b.c', password: 'password1', signup: true }, OPTS)
  const order = calls.filter((c) => c.method === 'POST').map((c) => c.url)
  assert.ok(order[0].includes('/signup'))
  assert.ok(order[1].includes('/login'))
})

test('a login that returns no token fails loudly', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/login'] = () => json({ ok: true }) // authenticated, but nothing usable came back
  await assert.rejects(
    () => signIn({ email: 'a@b.c', password: 'password1' }, OPTS),
    /no session token/
  )
})

test('the accounts server error is passed through verbatim', async () => {
  // The user needs "wrong password", not "HTTP 401".
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/login'] = () => json({ error: 'invalid email or password' }, 401)
  await assert.rejects(
    () => signIn({ email: 'a@b.c', password: 'nope' }, OPTS),
    /invalid email or password/
  )
})

// --- stored sessions --------------------------------------------------------------------

test('a stored session is re-bound silently, with no gate', async () => {
  saveSession({ token: 'sess_stored', email: 'a@b.c' }, OPTS)
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/platform/connect'] = () => json(status('http://accounts.test', true))

  const auth = await resolveAuth(OPTS)
  assert.equal(auth.needsSignIn, false, 'a returning user should not retype their password')
  assert.equal(auth.token, 'sess_stored')
})

test('a stored session the daemon rejects is discarded, then the gate is needed', async () => {
  saveSession({ token: 'sess_stale', email: 'a@b.c' }, OPTS)
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/platform/connect'] = () => json({ error: 'unknown session' }, 401)

  const auth = await resolveAuth(OPTS)
  assert.equal(auth.needsSignIn, true)
  assert.equal(loadSession(OPTS), null, 'a rejected token must not be retried forever')
})

test('signOut forgets the session', () => {
  saveSession({ token: 'sess_x' }, OPTS)
  signOut(OPTS)
  assert.equal(loadSession(OPTS), null)
})

test('storage being unavailable does not break sign-in', () => {
  const real = globalThis.localStorage
  globalThis.localStorage = {
    getItem: () => { throw new Error('denied') },
    setItem: () => { throw new Error('denied') },
    removeItem: () => { throw new Error('denied') }
  }
  try {
    assert.equal(loadSession(OPTS), null)
    saveSession({ token: 'x' }, OPTS) // must not throw: private mode is not a failure
  } finally {
    globalThis.localStorage = real
  }
})

// --- the daemon bearer token ------------------------------------------------------------

test('the daemon token is sent on BOTH platform endpoints', async () => {
  // /platform/status and /platform/connect authorize like /file: the daemon's bearer token must
  // match when one is set. Omitting it 401s every call, the gate cannot read status, and the app
  // runs on with no model access and no error anyone sees. This shipped once — hence the test.
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/platform/connect'] = () => json(status('http://accounts.test', true))

  await platformConnect('sess_abc', { ...OPTS, token: 'daemon_tok' })

  const statusCall = calls.find((c) => c.url.includes('/platform/status'))
  const connectCall = calls.find((c) => c.url.includes('/platform/connect'))
  assert.ok(connectCall.url.includes('token=daemon_tok'), 'connect must carry the daemon token')
  if (statusCall) assert.ok(statusCall.url.includes('token=daemon_tok'))
})

test('the session token and the daemon token are separate params', async () => {
  routes['/platform/status'] = () => json(status('http://accounts.test', false))
  routes['/platform/connect'] = () => json(status('http://accounts.test', true))

  await platformConnect('sess_ACCOUNT', { ...OPTS, token: 'daemon_tok' })
  const url = calls.find((c) => c.url.includes('/platform/connect')).url
  assert.ok(url.includes('session=sess_ACCOUNT'), 'accounts token rides in ?session=')
  assert.ok(url.includes('token=daemon_tok'), 'daemon token rides in ?token=')
})

test('the daemon token defaults to the page URL, like fromPage()', async () => {
  globalThis.location = { href: 'http://127.0.0.1:8787/apps/gm/?token=from_page&scope=agent:gm' }
  routes['/platform/status'] = () => json(status('', false))
  try {
    await platformStatus({ origin: ORIGIN }) // no explicit token
    const url = calls.find((c) => c.url.includes('/platform/status')).url
    assert.ok(url.includes('token=from_page'), 'must reuse the token the opener put in the URL')
  } finally {
    delete globalThis.location
  }
})

test('an unauthenticated daemon (no token set) is still callable', async () => {
  routes['/platform/status'] = () => json(status('', false))
  const s = await platformStatus({ origin: ORIGIN, token: '' })
  assert.equal(s.hosted, false)
  const url = calls.find((c) => c.url.includes('/platform/status')).url
  assert.ok(!url.includes('token='), 'no empty token param when there is no token')
})

// --- per-agent session keys -------------------------------------------------------------

test('the session key is derived from the agent, so two apps never share one', () => {
  globalThis.location = { href: 'http://127.0.0.1:8787/apps/game-master/?scope=agent:game-master' }
  assert.equal(agentIdFromPage(), 'game-master')

  globalThis.location = { href: 'http://127.0.0.1:8787/apps/figure-creator/' }
  assert.equal(agentIdFromPage(), 'figure-creator', 'falls back to the /apps/<id>/ path')

  globalThis.location = { href: 'http://127.0.0.1:8787/' }
  assert.equal(agentIdFromPage(), '', 'unknown rather than a wrong guess')
  delete globalThis.location
})

test('two agents on one machine keep separate sessions', () => {
  saveSession({ token: 'sess_a' }, { origin: ORIGIN, storageKey: 'agentd.session.a' })
  saveSession({ token: 'sess_b' }, { origin: ORIGIN, storageKey: 'agentd.session.b' })
  assert.equal(loadSession({ storageKey: 'agentd.session.a' }).token, 'sess_a')
  assert.equal(loadSession({ storageKey: 'agentd.session.b' }).token, 'sess_b')
})
