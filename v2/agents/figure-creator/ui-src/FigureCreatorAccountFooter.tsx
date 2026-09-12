/** Figure Creator's credits entry point around the unchanged shared account controls. */
import type { ComponentProps } from 'react'
import { AccountFooter as SharedAccountFooter } from '../../../clients/canvas/src/account'
import { FigureCreatorCreditsDialog } from './FigureCreatorCreditsDialog'
import { useCreditRefresh } from './use-credit-refresh'

export type { AccountAdapter } from '../../../clients/canvas/src/account'

export function FigureCreatorAccountFooter(props: ComponentProps<typeof SharedAccountFooter>) {
  const account = useCreditRefresh(props.account)
  if (!account) return null
  return (
    <div className="fc-account-container">
      <SharedAccountFooter {...props} account={account} />
      <div className="fc-billing-entry"><FigureCreatorCreditsDialog /></div>
    </div>
  )
}

// The local build supplies this implementation to the shared shell's AccountFooter import.
export { FigureCreatorAccountFooter as AccountFooter }
