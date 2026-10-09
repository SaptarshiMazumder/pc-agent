/* Settings — the person's own keys, as a page of their own in the rail. They used to sit folded
 * inside the Workspace, where "the key goes in Settings" (AGENTS.md) pointed at nothing. The keys
 * themselves are the same section (ApiKeysSection): Comfy API key, Civitai key, from agent.toml. */

import type { AgentdClient } from '@agentd/client'

import { ApiKeysSection } from '../studio/ApiKeysSection'

export function KeysPage({ client }: { client: AgentdClient | undefined }) {
  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Settings</h1>
          <p className="page-sub">
            Your keys. Every workflow runs on Comfy Cloud with your Comfy API key; your plan pays the GPU time. Nothing is checked here — a key
            Comfy Cloud refuses says so when it is used.
          </p>
        </div>
      </header>
      <div className="keys-page">
        <ApiKeysSection client={client} plain />
      </div>
    </>
  )
}

export default KeysPage
