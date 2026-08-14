import { formatSize, GLYPH, type TreeEntry, type TreeRow } from '../agentd/agent-files'

export function FileTree({
  rows,
  error,
  onToggle,
  onOpen,
}: {
  rows: TreeRow[]
  error: string
  onToggle: (rel: string) => void
  onOpen: (entry: TreeEntry) => void
}) {
  if (error) return <div className="tree">{<div className="tree-empty">{error}</div>}</div>
  if (!rows.length) return <div className="tree">{<div className="tree-empty">no files yet</div>}</div>

  return (
    <div className="tree">
      {rows.map((row) => (
        <div
          key={row.path}
          className={`node ${row.kind === 'folder' ? 'dir' : ''} ${row.fresh ? 'fresh' : ''}`}
          style={{ paddingLeft: `${7 + row.depth * 13}px` }}
          onClick={() => (row.kind === 'folder' ? onToggle(row.rel) : onOpen(row))}
        >
          <span className="glyph">
            {row.kind === 'folder' ? (row.expanded ? '▾' : '▸') : GLYPH[row.kind] || GLYPH.file}
          </span>
          <span className="nname">{row.name}</span>
          {row.kind !== 'folder' && <span className="nsize">{formatSize(row.size)}</span>}
        </div>
      ))}
    </div>
  )
}
