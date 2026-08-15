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
 * Hence the surface here is ONE receive-only channel. No filesystem, no keychain, no daemon
 * control, nothing that can be called INTO — the page cannot use this to ask for anything, only
 * to be told. Adding a method to this file means widening what untrusted agent code can reach,
 * so it should be argued for rather than assumed.
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
  }
})
