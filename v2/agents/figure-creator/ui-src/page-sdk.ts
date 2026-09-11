/** Shared screens use the page's SDK, including its single identity cache and credits bus. */
import type * as SDK from '../../../clients/sdk-js/src/index'

function pageSdk(): typeof SDK {
  return (globalThis as unknown as { agentd: typeof SDK }).agentd
}

export const authLogin: typeof SDK.authLogin = (...args) => pageSdk().authLogin(...args)
export const billing: typeof SDK.billing = (...args) => pageSdk().billing(...args)
export const onCreditsChanged: typeof SDK.onCreditsChanged = (...args) => pageSdk().onCreditsChanged(...args)
export type { Catalog, CreditPack, Credits } from '../../../clients/sdk-js/src/credits'
