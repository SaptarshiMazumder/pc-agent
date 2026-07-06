import { useState } from 'react'
import { FileText, FolderOpen, Eye, Download, Check } from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { fileUrl, humanSize } from '../lib/artifacts'

/** "Image · PNG" / "Deck · PPTX" style subtitle. */
function subtitle(a: Artifact): string {
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : ''
  const kind =
    a.kind === 'image' ? 'Image' : a.kind === 'video' ? 'Video' : a.kind === 'audio' ? 'Audio' : 'File'
  const size = a.size ? ` · ${humanSize(a.size)}` : ''
  return ext ? `${kind} · ${ext}${size}` : `${kind}${size}`
}

/** View (open in the OS app) + Download (save a copy) — plus Reveal for documents. */
function Actions({ a }: { a: Artifact }): JSX.Element {
  const [saved, setSaved] = useState(false)
  async function download(): Promise<void> {
    const res = await window.agentd.downloadPath(a.path)
    if (res?.ok) {
      setSaved(true)
      setTimeout(() => setSaved(false), 2000)
    }
  }
  return (
    <span className="artifact-actions">
      <button className="artifact-btn" title="open in the default app" onClick={() => void window.agentd.openPath(a.path)}>
        <Eye size={14} /> View
      </button>
      <button className={`artifact-btn ${saved ? 'ok' : ''}`} title="save a copy" onClick={() => void download()}>
        {saved ? <Check size={14} /> : <Download size={14} />} {saved ? 'Saved' : 'Download'}
      </button>
      {a.kind === 'file' && (
        <button className="artifact-icon-btn" title="show in folder" onClick={() => void window.agentd.revealPath(a.path)}>
          <FolderOpen size={14} />
        </button>
      )}
    </span>
  )
}

function ImageArtifact({ a }: { a: Artifact }): JSX.Element {
  const [broken, setBroken] = useState(false)
  if (broken) return <FileArtifact a={a} />
  return (
    <div className="artifact-card">
      <img className="artifact-preview" src={fileUrl(a.path)} alt={a.name} loading="lazy" onError={() => setBroken(true)} />
      <div className="artifact-bar">
        <span className="artifact-meta">
          <span className="artifact-name" title={a.path}>{a.name}</span>
          <span className="artifact-sub">{subtitle(a)}</span>
        </span>
        <Actions a={a} />
      </div>
    </div>
  )
}

function VideoArtifact({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="artifact-card">
      <video className="artifact-preview" src={fileUrl(a.path)} controls preload="metadata" />
      <div className="artifact-bar">
        <span className="artifact-meta">
          <span className="artifact-name" title={a.path}>{a.name}</span>
          <span className="artifact-sub">{subtitle(a)}</span>
        </span>
        <Actions a={a} />
      </div>
    </div>
  )
}

function AudioArtifact({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="artifact-card">
      <div className="artifact-bar">
        <span className="artifact-meta">
          <span className="artifact-name" title={a.path}>{a.name}</span>
          <span className="artifact-sub">{subtitle(a)}</span>
        </span>
        <Actions a={a} />
      </div>
      <audio className="artifact-audio" src={fileUrl(a.path)} controls preload="metadata" />
    </div>
  )
}

function FileArtifact({ a }: { a: Artifact }): JSX.Element {
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : 'FILE'
  return (
    <div className="artifact-file" title={a.path}>
      <span className="artifact-file-icon">
        <FileText size={18} />
        <span className="artifact-file-ext">{ext}</span>
      </span>
      <span className="artifact-meta">
        <span className="artifact-name">{a.name}</span>
        <span className="artifact-sub">{subtitle(a)}</span>
      </span>
      <Actions a={a} />
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
