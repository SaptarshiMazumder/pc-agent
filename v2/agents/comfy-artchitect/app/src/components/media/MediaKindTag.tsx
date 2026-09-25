/* The small label that says what a piece of output IS — an image, a video, a reusable workflow.
 *
 * ALWAYS A WORD AND A GLYPH, never colour alone: the tag is read by people who cannot tell the
 * warm gold from the lavender, and by everyone at 22px on top of a busy render. `over` is the
 * variant that sits ON media, where the tinted grounds would disappear into the picture.
 *
 * Shared by the landing page and the studio, so the three kinds look the same everywhere.
 */

import { Clapperboard, Image as ImageIcon, Workflow } from 'lucide-react'

import './media.css'

export type MediaKind = 'image' | 'video' | 'workflow'

const LABEL: Record<MediaKind, string> = { image: 'Image', video: 'Video', workflow: 'Workflow' }

export function MediaKindTag({ kind, over = false }: { kind: MediaKind; over?: boolean }) {
  const Icon = kind === 'video' ? Clapperboard : kind === 'workflow' ? Workflow : ImageIcon
  return (
    <span className={`mk-tag mk-${kind}${over ? ' mk-over' : ''}`}>
      <Icon size={12} strokeWidth={2} aria-hidden="true" />
      {LABEL[kind]}
    </span>
  )
}

export default MediaKindTag
