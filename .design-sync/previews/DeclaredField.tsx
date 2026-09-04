/* DeclaredField — a row for a setting the agent's AUTHOR declared: the buyer sees the author's
 * label and help, and fills in their own value. These are this agent's real declarations. */
import { DeclaredField } from 'agent-app'

const noop = () => {}

export const RequiredUnset = () => (
  <DeclaredField
    field={{
      key: 'COMFYUI_URL',
      label: 'ComfyUI URL',
      kind: 'url',
      required: true,
      help: 'Your instance, e.g. http://127.0.0.1:8188 or https://abc-8188.proxy.runpod.net',
    }}
    isSet={false}
    value=""
    onChange={noop}
  />
)

export const SecretSaved = () => (
  <DeclaredField
    field={{
      key: 'COMFYUI_AUTH',
      label: 'Authorization header',
      kind: 'secret',
      help: "Leave empty for an unprotected instance. Otherwise the full value: 'Bearer …' or 'Basic …'.",
    }}
    isSet={true}
    value=""
    onChange={noop}
  />
)

export const OptionalFilled = () => (
  <DeclaredField
    field={{
      key: 'COMFYUI_MCP_URL',
      label: 'Instance MCP URL',
      kind: 'url',
      help: 'Optional. If an MCP server runs beside your ComfyUI, its URL.',
    }}
    isSet={true}
    value="https://abc-9100.proxy.runpod.net/mcp"
    onChange={noop}
  />
)
