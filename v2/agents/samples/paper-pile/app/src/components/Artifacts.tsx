import type { FileRow } from '../agentd'

/** Everything the agent has produced, as files.
 *
 *  The notes, the filed source copies, the watch findings and the weekly digests all land in the
 *  workspace. This is the plain view of them — the one that proves the work is real and on disk,
 *  not a claim in a transcript. */
export function Artifacts({
  entries,
  error,
  path,
  onOpen,
  onDelete,
  onRefresh,
}: {
  entries: FileRow[]
  error: string
  path: string
  onOpen: (rel: string) => void
  onDelete: (rel: string) => void
  onRefresh: (path: string) => void
}) {
  const up = path ? path.split('/').slice(0, -1).join('/') : ''
  const folders = entries.filter((e) => e.kind === 'folder')
  const files = entries.filter((e) => e.kind !== 'folder')

  return (
    <div className="scroll">
      <div className="page-head">
        <h1>Artifacts</h1>
        <p className="muted">
          The agent's workspace: notes in <code>library/</code>, filed source copies in{' '}
          <code>library/sources/</code>, findings in <code>watch/</code>.
        </p>
      </div>

      <div className="crumbs">
        <button className="ghost small" onClick={() => onRefresh('')}>
          workspace
        </button>
        {path && <span className="crumb-sep">/</span>}
        {path && <span className="crumb">{path}</span>}
        {path && (
          <button className="ghost small" onClick={() => onRefresh(up)}>
            up
          </button>
        )}
      </div>

      {/* An unreadable workspace returns an empty list plus this — without showing it, a failure
          is indistinguishable from "you have no files". */}
      {error && <p className="err">could not read the workspace: {error}</p>}

      {!error && entries.length === 0 && (
        <p className="muted pad">Nothing here yet.</p>
      )}

      <ul className="files">
        {folders.map((e) => (
          <li key={e.rel}>
            <button className="file folder" onClick={() => onRefresh(e.rel)}>
              <span className="file-name">{e.name}/</span>
            </button>
          </li>
        ))}
        {files.map((e) => (
          <li key={e.rel}>
            <button className="file" onClick={() => onOpen(e.rel)}>
              <span className="file-name">{e.name}</span>
              <span className="file-meta">
                {formatSize(e.size)}
                {e.modified ? ` · ${new Date(e.modified * 1000).toLocaleDateString()}` : ''}
              </span>
            </button>
            <button className="ghost small" title="Delete" onClick={() => onDelete(e.rel)}>
              🗑
            </button>
          </li>
        ))}
      </ul>
    </div>
  )
}

function formatSize(bytes: number): string {
  if (!bytes) return '0 B'
  const units = ['B', 'KB', 'MB', 'GB']
  const i = Math.min(units.length - 1, Math.floor(Math.log(bytes) / Math.log(1024)))
  return `${(bytes / 1024 ** i).toFixed(i ? 1 : 0)} ${units[i]}`
}
