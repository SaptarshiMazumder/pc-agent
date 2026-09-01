// Shared filename renderer with MIDDLE truncation that keeps the extension (Finder/Gemini/
// Slack-style): a shrinkable head + a pinned tail. Defined once and used by the composer
// attachment chips AND the inline artifact cards, so the behaviour never drifts between them.
// The "…" is pure CSS (.fname-head) and only appears when the name doesn't fit its container;
// pass `className` for font/colour and an optional `title` for the hover tooltip.

type Props = { name: string; className?: string; title?: string }

// Always keep the last TAIL chars (covers the extension + a couple of name chars). Short names
// are returned whole (no split point) so nothing is spent on names that already fit.
function split(name: string): { head: string; tail: string } {
  const TAIL = 7
  if (name.length <= TAIL + 2) return { head: name, tail: '' }
  return { head: name.slice(0, name.length - TAIL), tail: name.slice(name.length - TAIL) }
}

export default function FileName({ name, className, title }: Props): JSX.Element {
  const { head, tail } = split(name)
  return (
    <span className={`fname ${className ?? ''}`} title={title}>
      <span className="fname-head">{head}</span>
      {tail && <span className="fname-tail">{tail}</span>}
    </span>
  )
}
