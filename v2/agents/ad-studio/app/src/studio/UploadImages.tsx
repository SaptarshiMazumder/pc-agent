/* Bring your own images into an image step — a button, or drop files anywhere on its results.
 * Each lands among the step's results, marked "yours", and is picked, fixed, referenced or
 * animated like a generated one. Free and instant (importImage: upload, then step_import). */

import type { AgentdClient } from '@agentd/client'
import { Loader2, Upload } from 'lucide-react'
import { useRef, useState, type ReactNode } from 'react'

import { importImage } from '../agentd/campaigns'
import { useApp } from '../state/store'

export function UploadImages({
  client,
  campaign,
  step,
  label = 'Upload images',
  multiple = true,
  onDone,
  children,
}: {
  client: AgentdClient | null
  campaign: string
  /** The image step they go into. */
  step: string
  label?: string
  multiple?: boolean
  /** The new results' paths, in order. */
  onDone?: (paths: string[]) => void
  /** What files can be dropped onto (a step's results). */
  children?: ReactNode
}) {
  const bump = useApp((s) => s.bumpStudio)
  const pick = useRef<HTMLInputElement>(null)
  const [busy, setBusy] = useState(false)
  const [over, setOver] = useState(false)
  const [error, setError] = useState('')

  const take = async (files: FileList | File[]) => {
    if (!client) return
    const list = Array.from(files).filter((f) => f.type.startsWith('image/') || /\.(png|jpe?g|webp)$/i.test(f.name))
    if (!list.length) return
    setBusy(true)
    setError('')
    const done: string[] = []
    try {
      for (const f of multiple ? list : list.slice(0, 1)) done.push(await importImage(client, campaign, step, f))
    } catch (e) {
      setError(String((e as Error)?.message || e))
    } finally {
      setBusy(false)
      if (done.length) {
        bump()
        onDone?.(done)
      }
    }
  }

  return (
    <div
      className={`upload-zone${over ? ' over' : ''}`}
      onDragOver={(e) => {
        if (!Array.from(e.dataTransfer.types).includes('Files')) return
        // Kept from the window-wide drop (which attaches files to the chat message).
        e.preventDefault()
        e.stopPropagation()
        setOver(true)
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        if (!e.dataTransfer.files.length) return
        e.preventDefault()
        e.stopPropagation()
        setOver(false)
        void take(e.dataTransfer.files)
      }}
    >
      <button className="ref-add" disabled={!client || busy} onClick={() => pick.current?.click()} title="Your own images, as results of this step">
        {busy ? <Loader2 size={12} className="spin" /> : <Upload size={12} />} {busy ? 'Adding…' : label}
      </button>
      {children}
      {over && <div className="upload-over">Drop to add to this step</div>}
      {error && <div className="studio-error">{error}</div>}
      <input
        ref={pick}
        type="file"
        accept="image/png,image/jpeg,image/webp"
        multiple={multiple}
        hidden
        onChange={(e) => {
          if (e.target.files?.length) void take(e.target.files)
          e.target.value = ''
        }}
      />
    </div>
  )
}
