/** Bind shared app screens to the SDK the host page already loaded.
 *
 * The daemon supplies that SDK. Bundling another copy here would create a second identity
 * cache, leaving the canvas sign-in form and the app's socket on different sessions.
 */
import type { authLogin as sdkAuthLogin } from '../../sdk-js/src/auth'

export function authLogin(...args: Parameters<typeof sdkAuthLogin>): ReturnType<typeof sdkAuthLogin> {
  const page = globalThis as unknown as { agentd: { authLogin: typeof sdkAuthLogin } }
  return page.agentd.authLogin(...args)
}
