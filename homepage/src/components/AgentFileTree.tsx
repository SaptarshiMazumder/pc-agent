import { Folder, FileText, FileCode2, Puzzle, Layout, BookOpen } from 'lucide-react'

interface Row {
  depth: number
  name: string
  note: string
  icon: typeof Folder
  dir?: boolean
  accent?: boolean
}

const ROWS: Row[] = [
  { depth: 0, name: 'comfy-artchitect/', note: 'the whole agent', icon: Folder, dir: true },
  {
    depth: 1,
    name: 'agent.toml',
    note: 'name, model, which tools it may touch',
    icon: FileCode2,
    accent: true,
  },
  { depth: 1, name: 'IDENTITY.md', note: 'who it is — injected every turn', icon: FileText },
  { depth: 1, name: 'AGENTS.md', note: 'how it must operate', icon: FileText },
  { depth: 1, name: 'skills/', note: 'playbooks it reads on demand', icon: BookOpen, dir: true },
  {
    depth: 2,
    name: 'comfyui-workflows/SKILL.md',
    note: 'the two graph formats, and why never to convert them',
    icon: FileText,
  },
  { depth: 1, name: 'plugins/', note: 'its own private tools, in Python', icon: Puzzle, dir: true },
  { depth: 2, name: 'comfy-bridge/', note: 'comfy_probe · comfy_run · comfy_install', icon: Folder, dir: true },
  { depth: 1, name: 'app/', note: 'a React window — source', icon: Layout, dir: true },
  { depth: 1, name: 'ui/', note: 'the built window the daemon serves', icon: Layout, dir: true },
]

export function AgentFileTree() {
  return (
    <div className="tree" role="img" aria-label="The directory layout of an agent: agent.toml, IDENTITY.md, AGENTS.md, a skills folder, a plugins folder of private Python tools, and app and ui folders holding its React window.">
      <div className="tree__chrome">
        <span className="tree__path">v2/agents/</span>
      </div>
      <ul className="tree__rows">
        {ROWS.map((row) => (
          <li
            key={row.name + row.depth}
            className={`tree__row ${row.accent ? 'is-accent' : ''}`}
            style={{ '--depth': row.depth } as React.CSSProperties}
          >
            <span className={`tree__icon ${row.dir ? 'is-dir' : ''}`}>
              <row.icon size={14} strokeWidth={1.9} />
            </span>
            <span className="tree__name">{row.name}</span>
            <span className="tree__note">{row.note}</span>
          </li>
        ))}
      </ul>
    </div>
  )
}
