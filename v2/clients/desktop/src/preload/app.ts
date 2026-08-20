/**
 * Preload for AGENT APP windows — deliberately almost nothing.
 *
 * WHAT PROBLEM THIS SOLVES. An app window receives its credential on the launch URL
 * (`?session=`), which is an ACCESS token and lives about ten minutes. The page has no refresh
 * token and no way to get one, so when that token expires the daemon starts refusing turns
 * ("auth_expired") — or worse, accepts the reconnect ANONYMOUSLY, at which point the account's
 * own agents quietly vanish from the app. Ten minutes is not a session.
 *
 * WHY THE SHELL PUSHES RATHER THAN THE APP PULLING. The refresh token is a 30-day credential for
 * the user's whole account, and an agent app is THIRD-PARTY CODE — that is the entire premise of
 * the marketplace. Handing it the refresh token, or the keychain the shell's own preload exposes,
 * would give every published agent a durable credential to the user's account. So the shell keeps
 * the refresh token, mints short-lived access tokens, and hands those down. The app can only ever
 * hold something that expires in minutes.
 *
 * Hence the surface here is almost entirely a receive-only channel. No filesystem, no keychain,
 * no daemon control. Adding a method here widens what untrusted agent code can reach, so it is
 * argued for rather than assumed — and there is exactly one, argued below.
 *
 * `openAppWindow` — REQUESTED, NOT GRANTED. Every window this preload is loaded into can call it,
 * because a preload is per-window-type and not per-agent; what stops an arbitrary agent using it
 * is the MAIN PROCESS, which looks up which window the request came from and serves only Agent
 * Builder (see `app:openWindow` in src/main/index.ts). The check is on the sender's identity,
 * which the caller cannot state or forge — not on anything passed in here.
 *
 * Why it exists at all: building an agent's window and looking at it are one loop, and without
 * this the second half lived in another application. An app window's only other route is
 * `window.open`, which this shell deliberately sends to the SYSTEM BROWSER — so the app opened
 * outside the desktop app entirely, with none of the token-pushing above.
 */

import { contextBridge, ipcRenderer } from 'electron'

/** Sent by the shell whenever it rotates its access token (see src/main/index.ts). */
const CHANNEL = 'agentd:access-token'

contextBridge.exposeInMainWorld('agentdHost', {
  /**
   * Subscribe to freshly-minted access tokens. Returns an unsubscribe.
   *
   * The SDK calls this and, on each token, updates its stored session and pushes the value to the
   * daemon with `auth.update` — which swaps the credential on the LIVE socket, so a running agent
   * is never interrupted by a renewal.
   */
  onAccessToken(callback: (token: string) => void): () => void {
    const handler = (_event: unknown, token: string): void => {
      if (typeof token === 'string' && token) callback(token)
    }
    ipcRenderer.on(CHANNEL, handler)
    return () => ipcRenderer.removeListener(CHANNEL, handler)
  },

  /**
   * Open an agent's own app window — the same call, and the same window, as the desktop client's
   * "Open app" button (`openAppWindow` in src/preload/index.ts).
   *
   * Answers `{ ok: false, error }` rather than throwing when the caller is not allowed to, so a
   * refusal is something the page can show rather than an unhandled rejection in a console nobody
   * is reading.
   */
  openAppWindow(url: string, title?: string): Promise<{ ok: boolean; error?: string }> {
    return ipcRenderer.invoke('app:openWindow', url, title)
  }
})
