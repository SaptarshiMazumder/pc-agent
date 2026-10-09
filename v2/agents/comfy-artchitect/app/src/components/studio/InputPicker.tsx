/* The list a stage's input is chosen from — everything this chat has that fits it.
 *
 * AN INPUT THE PERSON FILLS (a face, a product, an audio track): every file of this chat of the
 * input's kind — what they added and what any step made — plus Upload and the Library. Choosing
 * one puts it in the input's slot, the same slot the Inputs section shows.
 *
 * AN INPUT AN EARLIER STEP FILLS: that step's results. Choosing one is picking it on that step —
 * what goes on is one fact, shown the same on both stages.
 */

import { Library, Upload } from 'lucide-react'
import { useRef } from 'react'

import type { Artifact } from '../../agentd/artifacts'
import { OutputThumb } from './OutputsGrid'

export function InputPicker({
  candidates,
  current,
  fromStage,
  disabled,
  onChoose,
  onUpload,
  onFromLibrary,
}: {
  candidates: Artifact[]
  current: Artifact | null
  /** The step that makes this input, or "" when the person fills it. */
  fromStage: string
  disabled: boolean
  onChoose: (a: Artifact) => void
  onUpload?: (file: File) => void
  onFromLibrary?: () => void
}) {
  const pickRef = useRef<HTMLInputElement>(null)
  return (
    <div className="sp-picker">
      {candidates.length ? (
        <div className="sp-picker-grid">
          {candidates.map((a) => (
            <button
              key={a.path}
              type="button"
              className={`sp-tile${current?.path === a.path ? ' is-picked' : ''}`}
              title={a.name}
              disabled={disabled}
              onClick={() => onChoose(a)}
            >
              <OutputThumb file={a} />
              <span className="sp-tile-name">{a.name}</span>
            </button>
          ))}
        </div>
      ) : (
        <p className="sp-picker-none">
          {fromStage ? `Nothing made by ${fromStage.replace(/_/g, ' ')} yet.` : 'No file of this kind in this chat yet.'}
        </p>
      )}
      {!fromStage && (onUpload || onFromLibrary) && (
        <div className="sp-picker-doors">
          {onUpload && (
            <button type="button" className="sp-link" disabled={disabled} onClick={() => pickRef.current?.click()}>
              <Upload size={13} /> Upload a file
            </button>
          )}
          {onFromLibrary && (
            <button type="button" className="sp-link" disabled={disabled} onClick={onFromLibrary}>
              <Library size={13} /> From Library
            </button>
          )}
          <input
            ref={pickRef}
            type="file"
            accept="image/*,video/*,audio/*"
            hidden
            onChange={(e) => {
              const f = e.target.files?.[0]
              if (f && onUpload) onUpload(f)
              e.target.value = ''
            }}
          />
        </div>
      )}
    </div>
  )
}
