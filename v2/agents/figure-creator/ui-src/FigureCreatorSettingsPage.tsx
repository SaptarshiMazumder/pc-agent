/** Keep the existing settings screen and offer the same credits page from a collapsed rail. */
import type { ComponentProps } from 'react'
import { SettingsPage as SharedSettingsPage } from '../../../clients/canvas/src/settings'
import { FigureCreatorCreditsDialog } from './FigureCreatorCreditsDialog'
import { useCreditRefresh } from './use-credit-refresh'

export function FigureCreatorSettingsPage(props: ComponentProps<typeof SharedSettingsPage>) {
  const account = useCreditRefresh(props.account)
  return (
    <div className="fc-settings-pane">
      <div className="fc-billing-entry"><FigureCreatorCreditsDialog /></div>
      <SharedSettingsPage {...props} account={account} />
    </div>
  )
}

export { FigureCreatorSettingsPage as SettingsPage }
