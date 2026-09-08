import { ExternalLink, FileText } from 'lucide-react'
import { useState } from 'react'

import { fileUrl, humanSize, thumbnailUrl, type Artifact } from '../agentd/artifacts'
import { FileModal } from './studio/FileModal'

/**
 * Files an agent produced, shown under the answer that produced them.
 *
 * WHY IT IS NOT agentd's ArtifactView. That one offers View (opens the file in its Canvas editor),
 * Download (Electron's save dialog) and Reveal (Electron's file manager). This window has no
 * canvas and no desktop bridge — it is a page the daemon serves — so those three buttons would be
 * three things that do nothing. What is here is what a page can honestly do: render a light
 * preview inline, then open the original only when it is requested.
 *
 * THE INSPECTOR STILL LISTS THEM. This is not a replacement for the file tree; it answers a
 * different question. The tree says what the agent HAS, in one place, whenever you want it. This
 * says what this turn just MADE, at the point in the conversation where it happened.
 */

/** "Image · PNG · 240 KB" */
function subtitle(a: Artifact): string {
  const ext = a.name.includes('.') ? a.name.split('.').pop()!.toUpperCase() : ''
  const kind =
    a.kind === 'image'
      ? 'Image'
      : a.kind === 'video'
        ? 'Video'
        : a.kind === 'audio'
          ? 'Audio'
          : 'File'
  const size = a.size ? ` · ${humanSize(a.size)}` : ''
  return ext ? `${kind} · ${ext}${size}` : `${kind}${size}`
}

function One({ a }: { a: Artifact }) {
  const href = fileUrl(a.path)
  const [open, setOpen] = useState(false)

  // Media renders ITSELF. An agent that just drew a chart should show the chart, not a row saying
  // a chart exists — seeing it is how you know whether it is right.
  if (a.kind === 'image') {
    return (
      <>
        <button
          type="button"
          className="artifact-media"
          title={`Open ${a.name}`}
          aria-haspopup="dialog"
          onClick={() => setOpen(true)}
        >
          <img src={thumbnailUrl(a.path)} alt={a.name} loading="lazy" decoding="async" />
        </button>
        {open && <FileModal file={a} onClose={() => setOpen(false)} />}
      </>
    )
  }
  if (a.kind === 'video') {
    return <video className="artifact-media" src={href} controls preload="metadata" />
  }
  if (a.kind === 'audio') {
    return <audio className="artifact-audio" src={href} controls preload="metadata" />
  }

  return (
    <a className="artifact-card" href={href} target="_blank" rel="noreferrer">
      <span className="artifact-icon">
        <FileText size={16} />
      </span>
      <span className="artifact-main">
        <span className="artifact-name">{a.name}</span>
        <span className="artifact-sub">{subtitle(a)}</span>
      </span>
      <span className="artifact-open">
        <ExternalLink size={14} />
      </span>
    </a>
  )
}

export default function ArtifactView({ artifacts }: { artifacts?: Artifact[] }) {
  if (!artifacts?.length) return null
  return (
    <div className="artifacts">
      {artifacts.map((a) => (
        <One key={a.path} a={a} />
      ))}
    </div>
  )
}
