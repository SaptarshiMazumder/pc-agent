/* One field from agent.toml's [[settings]].
 *
 * Same .env plumbing as a provider key — it rides in the same `keys` map — but the daemon only
 * accepts the names THIS agent declared.
 *
 * A secret is write-only: `settingsValues` carries text and url values so a typo is fixable, and
 * never carries a secret. So a secret shows "saved" and takes a replacement, exactly like a
 * provider key on an installed agent.
 */

import { useState } from 'react'
import type { DeclaredField as Declared } from './useSettings'

export function DeclaredField({
  field,
  isSet,
  value,
  onChange,
}: {
  field: Declared
  isSet: boolean
  value: string
  onChange: (value: string) => void
}) {
  const secret = field.kind === 'secret'
  // A secret starts EMPTY, because its value was never sent to this page. Typing into it means
  // "replace"; leaving it alone means "keep what is stored".
  const initial = secret ? '' : value
  const [draft, setDraft] = useState(initial)

  return (
    <div className="field">
      <div>
        <label>
          {field.label || field.key}
          {field.required && <span className="req"> *</span>}
        </label>
        <span className="fhelp">{field.help || field.key}</span>
        {field.required && !isSet && <span className="fhelp missing">required — not set yet</span>}
      </div>
      <input
        type={secret ? 'password' : field.kind === 'url' ? 'url' : 'text'}
        value={draft}
        placeholder={secret ? (isSet ? '•••••••• saved' : 'not set') : isSet ? '' : 'not set'}
        onChange={(e) => setDraft(e.target.value)}
        // Only a real edit counts. Committing on every blur would arm the Save button for a field
        // the user merely tabbed through, and then write it back unchanged.
        onBlur={() => draft !== initial && onChange(draft)}
      />
    </div>
  )
}
