/* One provider key.
 *
 * `envValues` carries the actual strings and is ABSENT for an installed agent — the daemon strips
 * it — so the field renders as "•••• saved" with no way to read it back. Intended, not a failure:
 * a page that shipped inside someone else's package must never lift the user's key, and /apps/
 * pages have no CSP, so anything readable is exfiltratable.
 *
 * Keys are DAEMON-WIDE and deliberately not overridable — one .env, one source.
 */

import { useState } from 'react'

export function SecretField({
  name,
  isSet,
  /** The saved value, when this page is allowed to see it at all. */
  value,
  revealable,
  locked,
  onChange,
}: {
  name: string
  isSet: boolean
  value: string
  revealable: boolean
  locked: boolean
  onChange: (value: string) => void
}) {
  const initial = revealable && isSet ? value : ''
  const [draft, setDraft] = useState(initial)

  return (
    <div className="field">
      <div>
        <label>{name.replace(/_API_KEY$|_KEY$|_API_TOKEN$/, '')}</label>
        <span className="fhelp">
          {isSet ? (revealable ? name : `${name} · saved (hidden for installed agents)`) : name}
        </span>
      </div>
      <input
        type="password"
        disabled={locked}
        value={draft}
        placeholder={isSet ? '•••••••• saved' : 'not set'}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => draft !== initial && onChange(draft)}
      />
    </div>
  )
}
