/* SecretField — a write-only credential row: you can see THAT it is set, never what it is.
 *
 * The row's styles are scoped to `.settings-page` (they would leak into every window's `.field`
 * otherwise), so a row renders as the app renders it only inside that page, in the group's
 * bordered card — the API keys group of Settings. */
import { SecretField } from 'agent-app'

const noop = () => {}

const Card = ({ children }: { children: React.ReactNode }) => (
  <div className="settings-page" style={{ width: 600 }}>
    <div className="settings-card">{children}</div>
  </div>
)

export const Unset = () => (
  <Card>
    <SecretField name="HF_TOKEN" isSet={false} value="" revealable={false} locked={false} onChange={noop} />
  </Card>
)

export const SavedHidden = () => (
  <Card>
    <SecretField name="COMFYUI_AUTH" isSet={true} value="" revealable={false} locked={false} onChange={noop} />
  </Card>
)

export const Locked = () => (
  <Card>
    <SecretField name="OPENAI_API_KEY" isSet={true} value="" revealable={false} locked={true} onChange={noop} />
  </Card>
)
