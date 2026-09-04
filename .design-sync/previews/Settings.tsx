/* Settings — the shared settings page, populated the way the daemon would populate it: the
 * client stub answers `config.get` with a realistic ConfigData payload (this agent's own
 * declared fields among it), so the page renders its real rows. `config.set` is a no-op. */
import { Settings } from 'agent-app'

const CONFIG = {
  values: {
    model: 'claude-sonnet-5',
    reasoning_effort: 'medium',
    verify_tool: true,
    memory_enabled: true,
  },
  providerKeys: ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY'],
  env: { ANTHROPIC_API_KEY: true, OPENAI_API_KEY: false },
  envOverrides: {},
  authored: {},
  settings: [
    {
      key: 'COMFYUI_URL',
      label: 'ComfyUI URL',
      kind: 'url',
      required: true,
      help: 'Your instance, e.g. http://127.0.0.1:8188',
    },
    {
      key: 'COMFYUI_AUTH',
      label: 'Authorization header',
      kind: 'secret',
      help: "Leave empty for an unprotected instance. Otherwise the full value: 'Bearer …'.",
    },
    {
      key: 'HF_TOKEN',
      label: 'Hugging Face token',
      kind: 'secret',
      help: 'Optional. Needed only for gated models you have accepted access to.',
    },
  ],
  settingsValues: { COMFYUI_URL: 'https://abc-8188.proxy.runpod.net', COMFYUI_AUTH: '••saved' },
}

const client = {
  request: async (method: string) => (method === 'config.get' ? CONFIG : {}),
  on: () => () => {},
}

export const Page = () => <Settings client={client} agentId="comfy-artchitect" />
