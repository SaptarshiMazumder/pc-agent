/* The reference images a generation is sent, in order — the step's defaults (the cast member, the
 * product) to start, then whatever the user changes: remove one, add the images they selected
 * anywhere (a step's results, the gallery), or go back to the defaults. */

import { Plus, RotateCcw, X } from 'lucide-react'

import { isVideo, type Media } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function ReferenceTray({
  value,
  defaults,
  media,
  onChange,
}: {
  value: string[]
  defaults: string[]
  media: Media
  onChange: (refs: string[]) => void
}) {
  const selection = useApp((s) => s.selection)
  const addable = selection.filter((s) => s.kind === 'image' && !isVideo(s.path) && !value.includes(s.path))
  const changed = value.join('|') !== defaults.join('|')
  return (
    <div className="ref-tray">
      <span className="strip-label">References{changed ? ' (yours)' : ''}</span>
      <div className="ref-row">
        {value.map((path) => (
          <span key={path} className="ref-chip" title={path}>
            <img src={media(path)} alt="" />
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
        {changed && (
          <button className="ref-add" onClick={() => onChange(defaults)} title="Back to the step's references">
            <RotateCcw size={11} /> defaults
          </button>
        )}
      </div>
    </div>
  )
}
