import type { ReactNode } from 'react'
import { useReveal } from '../lib/useReveal'

/**
 * Wraps content in the scroll-reveal transition. `delay` staggers siblings —
 * keep it under ~250ms so a grid never feels like it is loading.
 */
export function Reveal({
  children,
  delay = 0,
  className = '',
  as: Tag = 'div',
}: {
  children: ReactNode
  delay?: number
  className?: string
  as?: 'div' | 'li' | 'section'
}) {
  const ref = useReveal<HTMLDivElement>()
  return (
    <Tag
      ref={ref as never}
      className={`reveal ${className}`.trim()}
      style={delay ? ({ '--reveal-delay': `${delay}ms` } as React.CSSProperties) : undefined}
    >
      {children}
    </Tag>
  )
}
