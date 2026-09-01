/**
 * Auth smoke test — drives the REAL client modules against a REAL accounts service.
 *
 * WHY THIS EXISTS, AND WHY TYPECHECKING IS NOT ENOUGH. The token layer is mostly TIMING and
 * ORDERING: who configures what before whom, whether ten concurrent callers collapse into one
 * request, whether a rotated token actually replaces the old one. None of that has a type, and
 * all of it is invisible until a user is mysteriously signed out ten minutes after logging in.
 *
 * It found exactly that on its first run: `configureTokens` was called when RESTORING a session
 * but not when signing in fresh, so the renewal timer had no address to send to and every refresh
 * returned null before making a request. Typecheck was clean, unit tests were green, and the bug
 * would have shipped.
 *
 *   1) start an accounts service:
 *      cd v2 && AGENTD_AUTH_ISSUER=http://127.0.0.1:4100 ACCOUNTS_RATE_LIMIT=0/0  *        python -m uvicorn app:app --port 4100 --app-dir accounts
 *   2) npm run smoke:auth        (from v2/clients/ui)
 */

// Drives the REAL client modules (lib/auth.ts + lib/tokens.ts) against a live accounts service.
import { configurePlatform, discoverPlatform } from '../src/lib/discovery'
import { login, signup, restoreSession, currentAccessToken, accountsUrl, authProviders, signOut } from '../src/lib/auth'
import { currentPair, refresh } from '../src/lib/tokens'

const ok = (m: string) => console.log('PASS  ' + m)
const bad = (m: string) => { console.log('FAIL  ' + m); process.exitCode = 1 }

;(async () => {
  configurePlatform(process.env.SMOKE_PLATFORM || 'http://127.0.0.1:4100')
  const doc = await discoverPlatform()
  doc ? ok('discovery: ' + doc.issuer) : bad('discovery returned null')
  accountsUrl() === (process.env.SMOKE_PLATFORM || 'http://127.0.0.1:4100') ? ok('accountsUrl from discovery') : bad('accountsUrl=' + accountsUrl())
  JSON.stringify(authProviders()) === '[{"id":"local","label":"Email","kind":"password"}]'
    ? ok('providers from discovery') : bad('providers=' + JSON.stringify(authProviders()))

  const email = `ui${Date.now()}@test.local`
  const s = await signup(email, 'hunter2hunter2')
  s.accountId.startsWith('acct_') ? ok('signup+login -> ' + s.accountId) : bad('signup gave ' + JSON.stringify(s))
  const p = currentPair()
  p?.refreshToken?.startsWith('rt_') ? ok('refresh token stored') : bad('no refresh token')
  s.token.split('.').length === 3 ? ok('access token is a JWT') : bad('token not a JWT')

  const t1 = await currentAccessToken()
  t1 === s.token ? ok('currentAccessToken reuses the live token') : bad('token churned')

  const rotated = await refresh()
  rotated && rotated.refreshToken !== p!.refreshToken ? ok('refresh rotated the token') : bad('refresh did not rotate')

  // Ten concurrent callers must produce ONE refresh, not ten (single-flight).
  const before = currentPair()!.refreshToken
  const results = await Promise.all(Array.from({ length: 10 }, () => refresh()))
  const distinct = new Set(results.map((r) => r?.refreshToken))
  distinct.size === 1 && currentPair()!.refreshToken !== before
    ? ok('single-flight: 10 concurrent refreshes -> 1 rotation')
    : bad('single-flight broken, distinct=' + distinct.size)

  await restoreSession() ? ok('restoreSession from stored refresh token') : bad('restoreSession failed')

  signOut()
  await new Promise((r) => setTimeout(r, 300))
  const dead = await refresh()
  dead === null ? ok('signOut revoked the session server-side') : bad('token still worked after signOut')
})()
