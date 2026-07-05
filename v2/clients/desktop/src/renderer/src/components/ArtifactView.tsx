import { useState } from 'react'
import { FileText, FolderOpen, ExternalLink } from 'lucide-react'

import type { Artifact } from '../lib/artifacts'
import { fileUrl, humanSize } from '../lib/artifacts'

/** Open an artifact in the OS default app (double-click a chip / click an image). */
function openExternally(path: string): void {
  void window.agentd.openPath(path)
}

function ImageArtifact({ a }: { a: Artifact }): JSX.Element {
  const [broken, setBroken] = useState(false)
  if (broken) return <FileArtifact a={a} />
  return (
    <button
      className="artifact-image"
      title={`${a.name} — click to open`}
      onClick={() => openExternally(a.path)}
    >
      <img src={fileUrl(a.path)} alt={a.name} loading="lazy" onError={() => setBroken(true)} />
    </button>
  )
}

function VideoArtifact({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="artifact-media">
      <video src={fileUrl(a.path)} controls preload="metadata" />
      <div className="artifact-cap">
        <span className="artifact-cap-name" title={a.path}>{a.name}</span>
        <button className="artifact-cap-btn" title="open in default app" onClick={() => openExternally(a.path)}>
          <ExternalLink size={13} />
        </button>
      </div>
    </div>
  )
}

function AudioArtifact({ a }: { a: Artifact }): JSX.Element {
  return (
    <div className="artifact-audio">
      <div className="artifact-cap-name" title={a.path}>{a.name}</div>
      <audio src={fileUrl(a.path)} controls preload="metadata" />
    </div>
  )
}

function FileArtifact({ a }: { a: Artifact }): JSX.Element {
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : 'FILE'
  return (
    <div className="artifact-file" title={a.path} onDoubleClick={() => openExternally(a.path)}>
      <span className="artifact-file-icon">
        <FileText size={18} />
        <span className="artifact-file-ext">{ext}</span>
      </span>
      <span className="artifact-file-meta">
        <span className="artifact-file-name">{a.name}</span>
        {a.size ? <span className="artifact-file-size">{humanSize(a.size)}</span> : null}
      </span>
      <span className="artifact-file-actions">
        <button className="artifact-cap-btn" title="open" onClick={() => openExternally(a.path)}>
          <ExternalLink size={14} />
        </button>
        <button className="artifact-cap-btn" title="show in folder" onClick={() => void window.agentd.revealPath(a.path)}>
          <FolderOpen size={14} />
        </button>
      </span>
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
  const media = artifacts.filter((a) => a.kind === 'image' || a.kind === 'video' || a.kind === 'audio')
  const files = artifacts.filter((a) => a.kind === 'file')
  return (
    <div className="artifacts">
      {media.length > 0 && (
        <div className="artifact-grid">
          {media.map((a) => (
            <One key={a.path} a={a} />
          ))}
        </div>
      )}
      {files.map((a) => (
        <One key={a.path} a={a} />
      ))}
    </div>
  )
}
