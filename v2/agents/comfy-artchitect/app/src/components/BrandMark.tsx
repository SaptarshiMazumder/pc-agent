/* The penguin — Comfy Penguin's mark, inline.
 *
 * ONE COLOUR, `currentColor`, so the same drawing is ink on the lime brand tile, lime on the ink
 * rail, or whatever the surface around it says. The belly, the eyes and the seam under the hard
 * hat are CUT OUT with masks rather than painted in a second colour, which is what lets the
 * ground show through and the mark sit on anything. Mask ids come from `useId` so two marks on
 * one page never share a mask.
 *
 * INLINE, NOT AN <img>. An image file cannot take its colour from the text around it, and the
 * one place this mark must adapt is exactly there — the brand tile is lime today and whatever
 * the theme says tomorrow. The favicon (public/favicon.svg) is the same drawing frozen on a lime
 * tile, because a tab icon has no surrounding text to borrow from.
 */

import { useId } from 'react'

export function BrandMark({ size = 24 }: { size?: number }) {
  const id = useId()
  const body = `${id}-body`
  const pencil = `${id}-pencil`
  return (
    <svg viewBox="0 0 64 64" width={size} height={size} aria-hidden="true" focusable="false">
      <defs>
        <mask id={body}>
          <rect width="64" height="64" fill="#fff" />
          <path
            d="M32 33 C 40 33, 45.5 39.5, 45.5 47 C 45.5 53, 39.5 55.5, 32 55.5 C 24.5 55.5, 18.5 53, 18.5 47 C 18.5 39.5, 24 33, 32 33 Z"
            fill="#000"
          />
          <path
            d="M22 28 Q 25.5 24.8, 29 28 M35 28 Q 38.5 24.8, 42 28"
            fill="none"
            stroke="#000"
            strokeWidth="2.4"
            strokeLinecap="round"
          />
          <path d="M13 24.2 L 51 24.2" stroke="#000" strokeWidth="2.2" strokeLinecap="round" />
        </mask>
        <mask id={pencil}>
          <rect width="64" height="64" fill="#fff" />
          <ellipse cx="48.5" cy="47.5" rx="6.8" ry="8.4" transform="rotate(-28 48.5 47.5)" fill="#000" />
        </mask>
      </defs>
      <g fill="currentColor" mask={`url(#${body})`}>
        <path d="M32 9 C 46 9, 53 22, 53 40 C 53 52, 44 58, 32 58 C 20 58, 11 52, 11 40 C 11 22, 18 9, 32 9 Z" />
        <path d="M17 21 C 17 5, 47 5, 47 21 Z" />
        <rect x="10.5" y="19" width="43" height="5" rx="2.5" />
        <path d="M13 31 C 6 35, 5 47, 12 52 C 15 46, 15 37, 13 31 Z" />
        <ellipse cx="25" cy="59.5" rx="4.6" ry="2.6" />
        <ellipse cx="39" cy="59.5" rx="4.6" ry="2.6" />
      </g>
      <path d="M28.5 31.5 L 32 35.5 L 35.5 31.5 Z" fill="currentColor" />
      <g fill="none" stroke="currentColor" strokeLinecap="round" mask={`url(#${pencil})`}>
        <path d="M51.5 52 L 58.5 30" strokeWidth="4.4" />
        <path d="M58.5 30 L 59.8 25.6" strokeWidth="2" />
      </g>
      <ellipse cx="48.5" cy="47.5" rx="5" ry="6.6" transform="rotate(-28 48.5 47.5)" fill="currentColor" />
    </svg>
  )
}
