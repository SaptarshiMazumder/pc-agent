/* DeclaredField — a row for a setting the agent's AUTHOR declared: the buyer sees the author's
 * label and help, and fills in their own value. These are this agent's real declarations.
 *
 * The row's styles are scoped to `.settings-page`, so it renders as the app renders it only
 * inside that page, in the "What this agent needs" group's bordered card. */
import { DeclaredField } from 'agent-app'

const noop = () => {}

const Card = ({ children }: { children: React.ReactNode }) => (
  <div className="settings-page" style={{ width: 600 }}>
    <div className="settings-card">{children}</div>
  </div>
)

export const RequiredUnset = () => (
  <Card>
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
  </Card>
)

export const SecretSaved = () => (
  <Card>
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
  </Card>
)

export const OptionalFilled = () => (
  <Card>
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
  </Card>
)
