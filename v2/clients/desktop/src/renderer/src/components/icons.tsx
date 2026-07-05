/**
 * Inline SVG icon set — stroke style, currentColor, sized via prop.
 * Self-contained on purpose: the shell's strict CSP forbids icon fonts/CDNs, and
 * inline SVG inherits the theme tokens for free. Extend HERE (same 24px grid,
 * strokeWidth 2) instead of introducing an icon dependency.
 */

import type { ReactNode } from 'react'

interface IconProps {
  size?: number
  className?: string
}

function icon(children: ReactNode) {
  return function Icon({ size = 16, className }: IconProps) {
    return (
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
        className={className}
        aria-hidden="true"
      >
        {children}
      </svg>
    )
  }
}

export const IconPlus = icon(<><path d="M12 5v14" /><path d="M5 12h14" /></>)

export const IconPencil = icon(
  <path d="M17 3a2.8 2.8 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5L17 3z" />
)

export const IconTrash = icon(
  <>
    <path d="M3 6h18" />
    <path d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2" />
    <path d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6" />
    <path d="M10 11v6" />
    <path d="M14 11v6" />
  </>
)

export const IconFolder = icon(
  <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" />
)

/* Gemini-style four-point sparkle — the "agents" mark */
export const IconSparkle = icon(
  <path d="M12 3l2.1 6.9L21 12l-6.9 2.1L12 21l-2.1-6.9L3 12l6.9-2.1L12 3z" />
)

export const IconChat = icon(
  <path d="M21 15a2 2 0 0 1-2 2H8l-5 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2v10z" />
)

export const IconDownload = icon(
  <>
    <path d="M12 3v12" />
    <path d="M7 11l5 5 5-5" />
    <path d="M4 21h16" />
  </>
)

export const IconSliders = icon(
  <>
    <path d="M4 21v-7" /><path d="M4 10V3" />
    <path d="M12 21v-9" /><path d="M12 8V3" />
    <path d="M20 21v-5" /><path d="M20 12V3" />
    <path d="M1 14h6" /><path d="M9 8h6" /><path d="M17 16h6" />
  </>
)

export const IconSun = icon(
  <>
    <circle cx="12" cy="12" r="4" />
    <path d="M12 2v2" /><path d="M12 20v2" />
    <path d="M4.9 4.9l1.4 1.4" /><path d="M17.7 17.7l1.4 1.4" />
    <path d="M2 12h2" /><path d="M20 12h2" />
    <path d="M4.9 19.1l1.4-1.4" /><path d="M17.7 6.3l1.4-1.4" />
  </>
)

export const IconMoon = icon(
  <path d="M21 12.8A9 9 0 1 1 11.2 3a7 7 0 0 0 9.8 9.8z" />
)

export const IconSend = icon(
  <>
    <path d="M22 2L11 13" />
    <path d="M22 2l-7 20-4-9-9-4 20-7z" />
  </>
)

export const IconStop = icon(<rect x="6" y="6" width="12" height="12" rx="2" />)

export const IconRefresh = icon(
  <>
    <path d="M21 12a9 9 0 1 1-2.64-6.36" />
    <path d="M21 3v6h-6" />
  </>
)

export const IconX = icon(<><path d="M18 6L6 18" /><path d="M6 6l12 12" /></>)

export const IconChevronRight = icon(<path d="M9 18l6-6-6-6" />)

export const IconChevronDown = icon(<path d="M6 9l6 6 6-6" />)
