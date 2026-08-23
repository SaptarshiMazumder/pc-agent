/* One settings row: a label, why it exists, where its value came from, and the control.
 *
 * The provenance line is not decoration. This page once showed "Model: GPT-5" while every turn
 * was answered by gemini, because cost-efficiency was silently overriding it — a value with no
 * provenance is how that stayed invisible.
 */

import { useEffect, useState } from 'react'
import type { CatalogOption, FieldSpec } from './schema'

/** A text/number box that commits when you LEAVE it, not on every keystroke.
 *
 *  Committing per keystroke writes a half-typed value into the draft, and for a number field an
 *  empty box becomes 0 the moment you clear it — so you cannot retype it. This is the vanilla
 *  page's behaviour (it listened for `change`, which fires on blur) with the editing state kept
 *  where React can see it. */
export function TextControl({
  type,
  value,
  disabled,
  onCommit,
}: {
  type: 'text' | 'number'
  value: string
  disabled: boolean
  onCommit: (raw: string) => void
}) {
  const [draft, setDraft] = useState(value)
  // Follow the value when it changes underneath us — a Save reloads from the daemon, and the
  // override switch moves the whole group to the other layer.
  useEffect(() => setDraft(value), [value])

  return (
    <input
      type={type}
      disabled={disabled}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => draft !== value && onCommit(draft)}
      onKeyDown={(e) => {
        if (e.key === 'Enter') (e.target as HTMLInputElement).blur()
      }}
    />
  )
}

export function Field({
  spec,
  value,
  source,
  onClear,
  pinnedBy,
  catalog,
  onChange,
}: {
  spec: FieldSpec
  value: unknown
  /** Which layer this value came from, for an agent-scoped field. */
  source?: 'this agent' | 'daemon'
  /** Hand this setting back to the daemon. Offered only on a row this agent has actually
   *  overridden — there is nothing to undo on one that is already inheriting. */
  onClear?: () => void
  /** The AGENTD_* variable that fixes this value, if one does. */
  pinnedBy?: string
  catalog?: Array<string | CatalogOption>
  onChange: (value: unknown) => void
}) {
  const options = spec.catalog ? catalog : spec.options
  const disabled = !!pinnedBy

  return (
    <div className="field">
      <div>
        <label>{spec.label}</label>
        {spec.help && <span className="fhelp">{spec.help}</span>}
        {/* A value fixed by an environment variable cannot be changed from here. Say WHY it is
            disabled — a greyed-out control with no explanation reads as a bug. */}
        {pinnedBy ? (
          <span className="fhelp pinned">pinned by {pinnedBy} in .env</span>
        ) : (
          source && (
            <span className="src-row">
              <span className={`fhelp src ${source === 'daemon' ? 'from-daemon' : 'from-agent'}`}>
                {source === 'daemon' ? 'from the daemon' : 'set for this agent'}
              </span>
              {/* The way back. Without it an override is one-way: once a row is set for this
                  agent, nothing on the page returns it to following the daemon, and clearing a
                  select to "" writes an override that happens to be empty. */}
              {source === 'this agent' && onClear && (
                <button type="button" className="src-clear" onClick={onClear}>
                  use daemon default
                </button>
              )}
            </span>
          )
        )}
      </div>

      {spec.type === 'toggle' ? (
        <button
          className={`toggle ${value ? 'on' : ''}`}
          disabled={disabled}
          onClick={() => onChange(!value)}
        />
      ) : spec.type === 'select' && options && options.length ? (
        <select
          disabled={disabled}
          value={String(value ?? '')}
          onChange={(e) => onChange(e.target.value)}
        >
          <SelectOptions options={options} value={value} />
        </select>
      ) : (
        <TextControl
          type={spec.type === 'number' ? 'number' : 'text'}
          disabled={disabled}
          value={value == null ? '' : String(value)}
          onCommit={(raw) => onChange(spec.type === 'number' ? Number(raw) : raw)}
        />
      )}
    </div>
  )
}

function SelectOptions({
  options,
  value,
}: {
  options: Array<string | CatalogOption>
  value: unknown
}) {
  const rows = options.map((o) =>
    typeof o === 'string'
      ? { value: o, label: o }
      : { value: String(o.value ?? o.id ?? ''), label: String(o.label ?? o.name ?? o.value ?? o.id ?? '') },
  )
  const current = String(value ?? '')
  // A value the catalog does not list must still be shown, not silently swapped for the first
  // option — that would change the setting just by rendering the page.
  const unlisted = current && !rows.some((r) => r.value === current)

  return (
    <>
      {unlisted && <option value={current}>{`${current} (current)`}</option>}
      {rows.map((r) => (
        <option key={r.value} value={r.value}>
          {r.label}
        </option>
      ))}
    </>
  )
}
