/* WorkflowDiagram — a ComfyUI graph, drawn, with a run travelling through it.
 *
 * WHY THIS IS THE HERO IMAGE. The thing this product makes is a workflow, so a picture of one
 * says more than any photograph could, and it is the one illustration that cannot be mistaken
 * for a stock image. A page selling "ComfyUI without the wiring" that shows no wiring is asking
 * to be taken on faith.
 *
 * SVG AND CSS, NO ASSET. It is a few hundred bytes, it is sharp on every display, it takes its
 * colours from the theme, and it renders on the first frame -- a marketing page that waits on a
 * hero image has already lost the visitor it exists for.
 *
 * THE PULSE IS THE POINT. A static graph reads as a screenshot; one with a run moving through it
 * reads as a machine doing something. `stroke-dasharray` + an offset animation costs nothing and
 * needs no JavaScript, and `prefers-reduced-motion` stops it dead.
 */

/** left-to-right stages, positioned on a 720x260 canvas. */
const NODES: { x: number; y: number; w: number; label: string; sub: string }[] = [
  { x: 8, y: 96, w: 128, label: 'Checkpoint', sub: 'what you have' },
  { x: 176, y: 24, w: 136, label: 'Prompt', sub: 'your words' },
  { x: 176, y: 160, w: 136, label: 'Reference', sub: 'optional' },
  { x: 352, y: 96, w: 128, label: 'Sampler', sub: 'steps · cfg' },
  { x: 520, y: 96, w: 128, label: 'Output', sub: 'image · video' },
]

/** [from, to] as indices into NODES. */
const EDGES: [number, number][] = [
  [0, 1],
  [0, 2],
  [1, 3],
  [2, 3],
  [3, 4],
]

const NODE_H = 56

function centreRight(i: number) {
  const n = NODES[i]
  return { x: n.x + n.w, y: n.y + NODE_H / 2 }
}
function centreLeft(i: number) {
  const n = NODES[i]
  return { x: n.x, y: n.y + NODE_H / 2 }
}

/** A cubic with horizontal handles — the shape a node editor actually draws. */
function edgePath(from: number, to: number): string {
  const a = centreRight(from)
  const b = centreLeft(to)
  const bow = Math.max(36, Math.abs(b.x - a.x) * 0.45)
  return `M ${a.x} ${a.y} C ${a.x + bow} ${a.y}, ${b.x - bow} ${b.y}, ${b.x} ${b.y}`
}

export function WorkflowDiagram(): JSX.Element {
  return (
    <svg
      className="wfd"
      viewBox="0 0 660 240"
      role="img"
      aria-label="A ComfyUI graph: checkpoint and prompt feeding a sampler, producing an image"
    >
      <g className="wfd-edges">
        {EDGES.map(([f, t]) => (
          <g key={`${f}-${t}`}>
            <path className="wfd-edge" d={edgePath(f, t)} />
            {/* The same curve again, dashed and sliding: one run moving through the graph. */}
            <path className="wfd-flow" d={edgePath(f, t)} />
          </g>
        ))}
      </g>

      {NODES.map((n, i) => (
        <g key={n.label} className="wfd-node" style={{ animationDelay: `${i * 90}ms` }}>
          <rect x={n.x} y={n.y} width={n.w} height={NODE_H} rx={10} className="wfd-box" />
          {/* The port a cable lands on. Purely decorative, and the detail that makes it read as
              a node editor rather than a flowchart. */}
          <circle cx={n.x} cy={n.y + NODE_H / 2} r={3.5} className="wfd-port" />
          <circle cx={n.x + n.w} cy={n.y + NODE_H / 2} r={3.5} className="wfd-port" />
          <text x={n.x + 14} y={n.y + 23} className="wfd-label">
            {n.label}
          </text>
          <text x={n.x + 14} y={n.y + 41} className="wfd-sub">
            {n.sub}
          </text>
        </g>
      ))}
    </svg>
  )
}

export default WorkflowDiagram
