import { useState } from 'react'
import { FileText, FolderOpen, Eye, Download, Check } from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { fileUrl, humanSize } from '../lib/artifacts'
import { useApp } from '../state/store'
import FileName from './FileName'

/** "Image · PNG" / "Deck · PPTX" style subtitle. */
function subtitle(a: Artifact): string {
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : ''
  const kind =
    a.kind === 'image' ? 'Image' : a.kind === 'video' ? 'Video' : a.kind === 'audio' ? 'Audio' : 'File'
  const size = a.size ? ` · ${humanSize(a.size)}` : ''
  return ext ? `${kind} · ${ext}${size}` : `${kind}${size}`
}

/** View (open in the Canvas) + Download (save a copy) — plus Reveal for documents.
 *  stopPropagation so a button press doesn't also trigger the card's open-canvas click. */
function Actions({ a }: { a: Artifact }): JSX.Element {
  const openCanvas = useApp((s) => s.openCanvas)
  const [saved, setSaved] = useState(false)
  async function download(e: React.MouseEvent): Promise<void> {
    e.stopPropagation()
    const res = await window.agentd.downloadPath(a.path)
    if (res?.ok) {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    }
  }
  // NOTE: self-declared tool actions (e.g. Convert to Vector) live ONLY in the Canvas header now,
  // not on the inline chat card — open the artifact to act on it.
  return (
    <span className="artifact-actions">
      <button className="artifact-btn" title="open in the canvas" onClick={(e) => { e.stopPropagation(); openCanvas(a) }}>
        <Eye size={14} /> View
      </button>
      <button className={`artifact-btn ${saved ? 'ok' : ''}`} title="save a copy" onClick={(e) => void download(e)}>
        {saved ? <Check size={14} /> : <Download size={14} />} {saved ? 'Saved' : 'Download'}
      </button>
      {a.kind === 'file' && (
        <button className="artifact-icon-btn" title="show in folder" onClick={(e) => { e.stopPropagation(); void window.agentd.revealPath(a.path) }}>
          <FolderOpen size={14} />
        </button>
      )}
    </span>
  )
}

/** Name + subtitle + actions — used inside the media overlay and the audio/file rows. */
function Meta({ a }: { a: Artifact }): JSX.Element {
  return (
    <>
      <span className="artifact-meta">
        <FileName name={a.name} className="artifact-name" title={a.path} />
        <span className="artifact-sub">{subtitle(a)}</span>
      </span>
      <Actions a={a} />
    </>
  )
}

function ImageArtifact({ a }: { a: Artifact }): JSX.Element {
  const openCanvas = useApp((s) => s.openCanvas)
  const [broken, setBroken] = useState(false)
  if (broken) return <FileArtifact a={a} />
  return (
    <div className="artifact-card artifact-clickable" onClick={() => openCanvas(a)} title="open in the canvas">
      <img className="artifact-preview" src={fileUrl(a.path)} alt={a.name} loading="lazy" onError={() => setBroken(true)} />
      <div className="artifact-overlay">
        <Meta a={a} />
      </div>
    </div>
  )
}

function VideoArtifact({ a }: { a: Artifact }): JSX.Element {
  // overlay at the TOP so it never fights the video's own controls at the bottom
  return (
    <div className="artifact-card">
      <video className="artifact-preview" src={fileUrl(a.path)} controls preload="metadata" />
      <div className="artifact-overlay artifact-overlay--top">
        <Meta a={a} />
      </div>
    </div>
  )
}

function AudioArtifact({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="artifact-audiocard">
      <div className="artifact-bar">
        <Meta a={a} />
      </div>
      <audio className="artifact-audio" src={fileUrl(a.path)} controls preload="metadata" />
    </div>
  )
}

function FileArtifact({ a }: { a: Artifact }): JSX.Element {
  const openCanvas = useApp((s) => s.openCanvas)
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : 'FILE'
  return (
    <div className="artifact-file artifact-clickable" title="open in the canvas" onClick={() => openCanvas(a)}>
      <span className="artifact-file-icon">
        <FileText size={18} />
        <span className="artifact-file-ext">{ext}</span>
      </span>
      <Meta a={a} />
    </div>
  )
}

function One({ a }: { a: Artifact }): JSX.Element {
  switch (a.kind) {
    case 'image':
      return <ImageArtifact a={a} />
    case 'video':
      return <VideoArtifact a={a} />
    case 'audio':
      return <AudioArtifact a={a} />
    default:
      return <FileArtifact a={a} />
  }
}

/** The media/documents an agent produced in this turn — rendered under the message. */
export default function ArtifactView({ artifacts }: { artifacts?: Artifact[] }): JSX.Element | null {
  if (!artifacts?.length) return null
  return (
    <div className="artifacts">
      {artifacts.map((a) => (
        <One key={a.path} a={a} />
      ))}
    </div>
  )
}
