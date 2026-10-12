/** Bind shared app screens to the SDK the host page already loaded.
 *
 * The daemon supplies that SDK. Bundling another copy here would create a second identity
 * cache, leaving the canvas sign-in form and the app's socket on different sessions.
 */
import type * as PageSdk from '../../sdk-js/src/index'

export type { AuthProvider, AgentdClient } from '../../sdk-js/src/index'

type Sdk = typeof PageSdk

function pageSdk(): Sdk {
  return (globalThis as unknown as { agentd: Sdk }).agentd
}

export const authAuthorize: Sdk['authAuthorize'] = (...args) => pageSdk().authAuthorize(...args)
export const authCallback: Sdk['authCallback'] = (...args) => pageSdk().authCallback(...args)
export const authLogin: Sdk['authLogin'] = (...args) => pageSdk().authLogin(...args)
export const authProviders: Sdk['authProviders'] = (...args) => pageSdk().authProviders(...args)
export const authSignupOpen: Sdk['authSignupOpen'] = (...args) => pageSdk().authSignupOpen(...args)
export const authStatus: Sdk['authStatus'] = (...args) => pageSdk().authStatus(...args)
export const forgetIdentityCache: Sdk['forgetIdentityCache'] = (...args) => pageSdk().forgetIdentityCache(...args)
export const oauthCallbackParams: Sdk['oauthCallbackParams'] = (...args) => pageSdk().oauthCallbackParams(...args)
export const onIdentityChanged: Sdk['onIdentityChanged'] = (...args) => pageSdk().onIdentityChanged(...args)
export const platformStatus: Sdk['platformStatus'] = (...args) => pageSdk().platformStatus(...args)
