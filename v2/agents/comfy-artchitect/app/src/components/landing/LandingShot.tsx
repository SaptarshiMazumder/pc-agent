/* LandingShot — a place a real render goes, that looks deliberate until one does.
 *
 * THE HONEST PROBLEM THIS SOLVES. This page wants pictures of what the product makes, and there
 * are none to ship: the only images in the repo are e2e reference photos of people, which are
 * test fixtures and not ours to publish. A marketing page built around <img> tags pointing at
 * files that do not exist is a page full of broken-image icons.
 *
 * SO IT DEGRADES BY DEFAULT rather than by accident. With no file present it renders a tinted
 * gradient panel that reads as art direction; the moment somebody drops a real render at the
 * named path it is used instead, with no code change. `onError` is the whole mechanism -- a
 * missing file is an expected state here, not a fault worth logging.
 *
 * TO FILL THESE IN: put files in `app/public/marketing/` and rebuild. The names are the `src`
 * values passed by LandingPage; nothing else needs touching.
 */

import { useState } from 'react'

export function LandingShot({
  src,
  alt,
  tone = 0,
  className = '',
}: {
  /** Path under public/, e.g. "marketing/influencer.webp". Absent is fine. */
  src: string
  alt: string
  /** Picks one of the placeholder gradients, so a grid of them does not read as one flat block. */
  tone?: number
  className?: string
}): JSX.Element {
  const [failed, setFailed] = useState(false)

  if (failed) {
    return (
      <span
        className={`shot shot-empty tone-${tone % 4} ${className}`}
        role="img"
        aria-label={alt}
      >
        {/* A caption rather than nothing: an unexplained coloured rectangle looks like a bug,
            whereas a labelled one looks like a frame waiting for its picture. */}
        <span className="shot-cap">{alt}</span>
      </span>
    )
  }

  return (
    <img
      className={`shot ${className}`}
      src={src}
      alt={alt}
      loading="lazy"
      decoding="async"
      onError={() => setFailed(true)}
    />
  )
}

export default LandingShot
