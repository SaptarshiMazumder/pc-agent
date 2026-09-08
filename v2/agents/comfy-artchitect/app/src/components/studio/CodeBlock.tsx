/* Text with line numbers, and colour when we can honestly give it.
 *
 * NO HIGHLIGHTING LIBRARY, and that is a size decision rather than a purity one. Prism, highlight.js
 * and shiki cost 100-400 KB against a bundle already at 428, and they earn it by covering thirty
 * languages this agent will never produce. What it DOES produce is ComfyUI workflow JSON — one
 * grammar, forty lines to tokenise — plus markdown, which has its own renderer. Anything else falls
 * through to numbered plain text, which is honest: unrecognised is not the same as unstyled-and-
 * pretending.
 *
 * WHY LINE NUMBERS AT ALL. A 27-node graph is 400 lines of JSON, and every conversation about one
 * is "look at node 105" / "the seed on line 212". Without numbers the pane is a wall you scroll
 * past; with them it is something two people can point at.
 */

/** One pass over a line of JSON. Order matters: a KEY is a string followed by a colon, so it must
 *  be tried before the plain-string rule or every key matches as a value. */
const JSON_TOKEN =
  /("(?:\\.|[^"\\])*"\s*:)|("(?:\\.|[^"\\])*")|(-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)|\b(true|false|null)\b|([{}[\],:])/g

function jsonLine(line: string, key: number): JSX.Element {
  const out: JSX.Element[] = []
  let at = 0
  let m: RegExpExecArray | null
  JSON_TOKEN.lastIndex = 0
  while ((m = JSON_TOKEN.exec(line))) {
    if (m.index > at) out.push(<span key={out.length}>{line.slice(at, m.index)}</span>)
    const cls = m[1] ? 'k' : m[2] ? 's' : m[3] ? 'n' : m[4] ? 'b' : 'p'
    out.push(
      <span key={out.length} className={`cb-${cls}`}>
        {m[0]}
      </span>,
    )
    at = m.index + m[0].length
  }
  if (at < line.length) out.push(<span key={out.length}>{line.slice(at)}</span>)
  // A blank line still needs to occupy its row, or the numbers stop lining up with the text.
  return <code key={key}>{out.length ? out : '\u00a0'}</code>
}

export function CodeBlock({ text, language }: { text: string; language: 'json' | 'text' }) {
  const lines = text.split('\n')
  return (
    <div className="cb">
      {/* The gutter is ONE column, not a number per row wrapped in its own box: a long line that
          soft-wraps must not push its own number out of alignment, so wrapping is off and the pane
          scrolls sideways instead — the same bargain every code editor makes. */}
      <div className="cb-gutter" aria-hidden="true">
        {lines.map((_, i) => (
          <span key={i}>{i + 1}</span>
        ))}
      </div>
      <pre className="cb-code">
        {language === 'json'
          ? lines.map((l, i) => jsonLine(l, i))
          : lines.map((l, i) => <code key={i}>{l || '\u00a0'}</code>)}
      </pre>
    </div>
  )
}
