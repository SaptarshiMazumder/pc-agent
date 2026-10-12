/* The reference images a generation is sent, in order — the step's defaults (the cast member, the
 * product) to start, then whatever the user changes: remove one, add the images they selected
 * anywhere (a step's results, the gallery), or go back to the defaults. */

import type { AgentdClient } from '@agentd/client'
import { Library, Plus, RotateCcw, X } from 'lucide-react'
import { useState } from 'react'

import { isVideo, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'
import { LibraryPicker } from './LibraryPicker'
import { ExpandMedia } from './ExpandMedia'

export function ReferenceTray({
  client,
  value,
  defaults,
  media,
  onChange,
}: {
  /** For picking references from the library. */
  client: AgentdClient | null
  value: string[]
  defaults: string[]
  media: Media
  onChange: (refs: string[]) => void
}) {
  const [picking, setPicking] = useState(false)
  const selection = useApp((s) => s.selection)
  const addable = selection.filter((s) => s.kind === 'image' && !isVideo(s.path) && !value.includes(s.path))
  const changed = value.join('|') !== defaults.join('|')
  return (
    <div className="ref-tray">
      <span className="strip-label">References{changed ? ' (yours)' : ''}</span>
      <div className="ref-row">
        {value.map((path) => (
          <span key={path} className="ref-chip has-expand" title={path}>
            <img src={media(path)} alt="" />
            <ExpandMedia src={media(path)} path={path} />
            <button className="ref-x" title="Remove" onClick={() => onChange(value.filter((p) => p !== path))}>
              <X size={10} />
            </button>
          </span>
        ))}
        {value.length === 0 && <span className="ref-none">none</span>}
        {addable.length > 0 && (
          <button className="ref-add" onClick={() => onChange([...value, ...addable.map((s) => s.path)])} title="Add the images you selected">
            <Plus size={12} /> add selected ({addable.length})
          </button>
        )}
        <button className="ref-add" disabled={!client} onClick={() => setPicking(true)} title="Add images from your library">
          <Library size={11} /> from library
        </button>
        {changed && (
          <button className="ref-add" onClick={() => onChange(defaults)} title="Back to the step's references">
            <RotateCcw size={11} /> defaults
          </button>
        )}
      </div>
      {picking && (
        <LibraryPicker
          client={client}
          kind="image"
          title="References from your library"
          onPick={(paths) => {
            onChange([...value, ...paths.filter((p) => !value.includes(p))])
            setPicking(false)
          }}
          onClose={() => setPicking(false)}
        />
      )}
    </div>
  )
}
