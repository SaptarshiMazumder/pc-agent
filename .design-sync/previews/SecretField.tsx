/* SecretField — a write-only credential row: you can see THAT it is set, never what it is. */
import { SecretField } from 'agent-app'

const noop = () => {}

export const Unset = () => (
  <SecretField name="HF_TOKEN" isSet={false} value="" revealable={false} locked={false} onChange={noop} />
)

export const SavedHidden = () => (
  <SecretField name="COMFYUI_AUTH" isSet={true} value="" revealable={false} locked={false} onChange={noop} />
)

export const Locked = () => (
  <SecretField name="OPENAI_API_KEY" isSet={true} value="" revealable={false} locked={true} onChange={noop} />
)
