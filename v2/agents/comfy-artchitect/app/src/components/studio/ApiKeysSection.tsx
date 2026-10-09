/* The person's own keys, in the Workspace: the Comfy API key (every workflow runs on Comfy Cloud with
 * it) and the Civitai key (imports Civitai files that need a login). One row per key this agent
 * declares in agent.toml [[settings]] — label and help come from there.
 *
 * Nothing is checked here: a key Comfy Cloud or Civitai refuses says so when it is used. A row only
 * saves its key (useOwnComfyKeys) and says whether one is saved; it never reads a key back.
 */

import { useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import { useOwnComfyKeys, type OwnKey } from './useOwnComfyKeys'
import { WorkspaceSection } from './WorkspaceSection'

export function ApiKeysSection({ client, plain = false }: { client?: AgentdClient; plain?: boolean }) {
  const { keys, message, save } = useOwnComfyKeys(client)
  const saved = keys.filter((k) => k.isSet).length

  const body = (
    <div className="ck">
      {keys.map((k) => (
        <KeyRow key={k.key} item={k} onSave={(value) => save({ [k.key]: value })} />
      ))}
      {message && message !== 'Saved.' && (
        <p className={message.startsWith('could not') ? 'ck-err' : 'ck-note'}>{message}</p>
      )}
    </div>
  )
  /* PLAIN on the Settings page, which carries its own head; folded inside a workspace section
     anywhere else. */
  if (plain) return body
  return (
    <WorkspaceSection title="Your keys" count={keys.length ? `${saved}/${keys.length} saved` : ''} defaultOpen={false}>
      {body}
    </WorkspaceSection>
  )
}

function KeyRow({ item, onSave }: { item: OwnKey; onSave: (value: string) => Promise<void> | undefined }) {
  const [draft, setDraft] = useState('')
  const [replacing, setReplacing] = useState(false)

  const saveKey = () =>
    void Promise.resolve(onSave(draft.trim())).then(() => {
      setDraft('')
      setReplacing(false)
    })

  return (
    <div className="ck-key">
      {item.isSet && !replacing ? (
        <div className="ck-row">
          <span className="ck-note">
            {item.label} <span className="ck-set">· saved</span>
          </span>
          <button className="ck-link-btn" onClick={() => setReplacing(true)}>
            Replace
          </button>
        </div>
      ) : (
        <div className="ck-row">
          <input
            type="password"
            className="ck-input"
            value={draft}
            placeholder={item.label}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && draft.trim() && saveKey()}
          />
          <button className="refs-slot-btn" disabled={!draft.trim()} onClick={saveKey}>
            Save
          </button>
          {replacing && (
            <button className="ck-link-btn" onClick={() => setReplacing(false)}>
              Cancel
            </button>
          )}
        </div>
      )}
      {item.help && <p className="ck-note">{item.help}</p>}
    </div>
  )
}
