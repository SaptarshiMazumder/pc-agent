/**
 * "Continue with Google" on the desktop — the system browser plus a loopback listener.
 *
 * WHY NOT IN THE APP WINDOW. An installed application cannot keep a client secret and must never
 * host the provider's password form: a renderer showing Google's login is indistinguishable, to
 * the user, from a renderer phishing it. The standard answer — RFC 8252, and what every desktop
 * app offering this does — is to hand the sign-in to the real browser, where the address bar and
 * the saved passwords are, and take the authorization code back on a listener bound to loopback.
 *
 * WHY A LOOPBACK AND NOT A CUSTOM SCHEME. `agentd://callback` would work too, but registering a
 * protocol handler is per-install OS state that fails quietly when two builds are installed or
 * the app is run unpacked. A port on 127.0.0.1 exists only while the flow does.
 *
 * WHAT MAKES THE CODE USELESS TO ANYONE ELSE:
 *   * PKCE — the verifier never leaves this process, and the code cannot be redeemed without it.
 *     This is the whole reason a public client is safe without a secret.
 *   * `state` — checked here AND on the accounts service, which refuses one it never issued.
 *   * The listener binds 127.0.0.1 explicitly, not 0.0.0.0: nothing off this machine can reach
 *     it, so the redirect cannot be intercepted by something on the network.
 *   * One request and it closes. A second visit to the same port finds nothing listening.
 *
 * WHO ENDS UP HOLDING THE SESSION. The daemon, exactly as it does for a password sign-in — this
 * exchanges the code for a token pair and hands the REFRESH half to the runtime's `/auth/adopt`,
 * which validates it by using it and becomes the machine's one holder. There is deliberately no
 * second place a desktop session can live.
 */

import { shell } from 'electron'
import http from 'node:http'
import { AddressInfo } from 'node:net'

/** A browser round trip, generously. Past this the user has wandered off and the flow is dead. */
const FLOW_TIMEOUT_MS = 5 * 60 * 1000

/**
 * THE PORTS WE MAY LISTEN ON, and the reason this is a fixed list rather than "any free one".
 *
 * Google hands out an arbitrary-port loopback redirect ONLY to a client registered as a *Desktop
 * app*, which is a public client with no secret. Our accounts service is one confidential Web
 * client — it holds the secret and does the exchange server-side — so every redirect URI it uses
 * has to be registered on that client, exactly, PORT INCLUDED. A port-0 listener would produce a
 * URI nobody registered and the exchange would come back `redirect_uri_mismatch`.
 *
 * So: a short list of high, fixed, unmemorable ports. FOUR of them, not one, because a single
 * port is a single point of failure — another app squatting it, or a second copy of this app
 * mid-flow, would make sign-in impossible with no recourse. Each one must be registered.
 *
 * Adding a port here means adding it in the Google console too, or it will simply fail.
 */
const LOOPBACK_PORTS = [47821, 47822, 47823, 47824]

export interface LoopbackResult {
  code: string
  state: string
}

/** What the browser tab shows once the code is captured. Plain, self-closing, no assets — the
 *  listener is about to stop, so it can serve exactly one thing. */
function donePage(message: string, ok: boolean): string {
  return `<!doctype html><meta charset="utf-8"><title>${ok ? 'Signed in' : 'Sign-in failed'}</title>
<style>
  body{font:15px/1.5 system-ui,sans-serif;background:#0b0810;color:#f4f1f8;
       display:grid;place-items:center;height:100vh;margin:0;text-align:center}
  .c{max-width:32ch}.m{color:${ok ? '#9fd39a' : '#e08a8a'};font-weight:600;margin-bottom:6px}
  .s{color:#9a93aa;font-size:13px}
</style>
<div class="c"><div class="m">${message}</div>
<div class="s">You can close this tab and go back to the app.</div></div>`
}

export interface Loopback {
  redirectUri: string
  result: Promise<LoopbackResult>
  close: () => void
}

/** Bind one specific port on loopback, or reject. Separated out so the caller can walk the list. */
function bindOne(server: http.Server, port: number): Promise<void> {
  return new Promise((resolve, reject) => {
    const onError = (e: NodeJS.ErrnoException): void => {
      server.removeListener('listening', onListening)
      reject(e)
    }
    const onListening = (): void => {
      server.removeListener('error', onError)
      resolve()
    }
    server.once('error', onError)
    server.once('listening', onListening)
    // 127.0.0.1 EXPLICITLY, never 0.0.0.0: nothing off this machine may reach the listener, so
    // the redirect cannot be intercepted from the network.
    server.listen(port, '127.0.0.1')
  })
}

/**
 * Listen on one of the registered loopback ports for the provider's redirect.
 *
 * ASYNC, because the port is part of the redirect URI and `server.address()` is null until the
 * 'listening' event has fired — reading it synchronously after `listen()` yields port 0 and a
 * redirect URI that goes nowhere.
 */
export async function listenForCode(): Promise<Loopback> {
  const server = http.createServer()

  let bound = 0
  let lastError: Error | null = null
  for (const port of LOOPBACK_PORTS) {
    try {
      await bindOne(server, port)
      bound = port
      break
    } catch (e) {
      const err = e as NodeJS.ErrnoException
      // Anything OTHER than "that port is taken" is not going to improve on the next port, so it
      // surfaces immediately rather than being retried three more times and reported as a
      // collision it never was.
      if (err.code !== 'EADDRINUSE' && err.code !== 'EACCES') throw err
      lastError = err
    }
  }
  if (!bound) {
    throw new Error(
      `every sign-in port (${LOOPBACK_PORTS.join(', ')}) is already in use` +
        `${lastError ? ` — ${lastError.message}` : ''}`
    )
  }

  let settle: (r: LoopbackResult) => void
  let fail: (e: Error) => void
  const result = new Promise<LoopbackResult>((res, rej) => {
    settle = res
    fail = rej
  })

  const close = (): void => {
    try {
      server.close()
    } catch {
      /* already closed — the flow finished or was abandoned */
    }
  }

  const timer = setTimeout(() => {
    fail(new Error('sign-in timed out — the browser did not come back'))
    close()
  }, FLOW_TIMEOUT_MS)

  server.on('request', (req, res) => {
    let url: URL
    try {
      url = new URL(req.url || '/', `http://127.0.0.1`)
    } catch {
      res.writeHead(400).end()
      return
    }
    const code = url.searchParams.get('code') || ''
    const state = url.searchParams.get('state') || ''
    // The provider reports a refusal in the query too — a user pressing "Cancel" lands here, and
    // reporting that as "no code" would read as a bug rather than as their own choice.
    const err = url.searchParams.get('error') || ''

    const ok = !!(code && state) && !err
    res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' })
    res.end(donePage(ok ? 'Signed in.' : err ? `Sign-in refused: ${err}` : 'No code received.', ok))

    clearTimeout(timer)
    if (ok) settle({ code, state })
    else fail(new Error(err ? `sign-in refused (${err})` : 'the browser came back with no code'))
    // Give the response a moment to flush before the socket goes away with the server.
    setTimeout(close, 250)
  })

  // Past the bind, the only errors left are per-connection ones; they must not take the flow down
  // silently, and they must not re-reject a promise that already settled.
  server.on('error', (e) => {
    clearTimeout(timer)
    fail(e instanceof Error ? e : new Error(String(e)))
  })

  return {
    // A TRAILING SLASH, and this exact string must appear in the Google console. The clients
    // compute nothing else about it, so what is registered and what is sent cannot drift.
    redirectUri: `http://127.0.0.1:${bound}/`,
    result,
    close
  }
}

/**
 * The whole desktop sign-in, start to finish.
 *
 * `accountsUrl` is the service that owns the flow: it holds the client secret, talks to the
 * provider and mints our own token pair. Nothing about Google is known here — this process only
 * opens a browser and catches what comes back.
 */
export async function desktopOAuthSignIn(args: {
  accountsUrl: string
  provider: string
}): Promise<{ refreshToken: string; accessToken: string; email: string; accountId: string }> {
  const base = args.accountsUrl.replace(/\/$/, '')
  if (!base) throw new Error('this build has no accounts service to sign in to')

  const listener = await listenForCode()
  try {
    const started = await fetch(`${base}/auth/authorize`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ provider: args.provider, redirect_uri: listener.redirectUri })
    })
    const begun = (await started.json().catch(() => ({}))) as {
      authorization_url?: string
      state?: string
      code_verifier?: string
      detail?: string
    }
    if (!started.ok || !begun.authorization_url || !begun.state) {
      throw new Error(String(begun.detail || `could not start sign-in (HTTP ${started.status})`))
    }

    await shell.openExternal(begun.authorization_url)
    const back = await listener.result
    // Checked here as well as on the server: this process knows which attempt it started, and a
    // stray request to the loopback port carrying somebody else's state must not be redeemed.
    if (back.state !== begun.state) throw new Error('sign-in state did not match — start again')

    const done = await fetch(`${base}/auth/callback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        code: back.code,
        state: back.state,
        code_verifier: begun.code_verifier || undefined,
        // NOT cookie mode. The desktop has no browser jar to put it in, and the runtime needs the
        // refresh token itself to become the machine's holder — the opposite of the web, where
        // the point is that no script can read it.
        client_id: 'agentd-desktop',
        device_label: 'desktop'
      })
    })
    const pair = (await done.json().catch(() => ({}))) as {
      access_token?: string
      refresh_token?: string
      email?: string
      account_id?: string
      detail?: string
    }
    if (!done.ok || !pair.refresh_token) {
      throw new Error(String(pair.detail || `sign-in failed (HTTP ${done.status})`))
    }
    return {
      refreshToken: String(pair.refresh_token),
      accessToken: String(pair.access_token || ''),
      email: String(pair.email || ''),
      accountId: String(pair.account_id || '')
    }
  } finally {
    listener.close()
  }
}
