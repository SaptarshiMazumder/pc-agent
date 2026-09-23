/* LandingShot — one card's picture: a still, a clip, or the frame one goes in.
 *
 * IT DEGRADES BY DESIGN, NOT BY ACCIDENT. A card whose file is absent renders a tinted, labelled
 * panel that reads as art direction; the moment a file exists at the path it is used instead,
 * with no code change. `onError` is the whole mechanism — a missing file is an expected state
 * here, not a fault worth logging. That is what let the page ship before there were any images.
 *
 * VIDEO AUTOPLAYS, SILENTLY, ONCE IT IS SMALL ENOUGH TO DESERVE TO. The source clip was 4.4 MB,
 * which is indefensible as an autoplay for a 320px thumbnail; re-encoded (720px, no audio, crf
 * 30) it is 319 KB, which is less than the page's own stylesheet. Muted and `playsInline` are
 * both required or mobile browsers refuse to autoplay at all.
 *
 * TO ADD OR REPLACE ONE: drop a file in `app/public/marketing/` and rebuild. The names are the
 * `src` values LandingPage passes; nothing else needs touching. Stills should be 640x360 WebP —
 * at the size these render, anything larger is bytes nobody sees (the five here came from 21.9 MB
 * of PNG and land at 125 KB in total).
 */

import { useState } from 'react'

const isVideo = (src: string) => /\.(mp4|webm|mov)$/i.test(src)

export function LandingShot({
  src,
  alt,
  tone = 0,
  className = '',
}: {
  /** Path under public/, e.g. "marketing/influencer.webp". Absent is fine. */
  src: string
  alt: string
  /** Picks one of the placeholder gradients, so a row of them does not read as one flat block. */
  tone?: number
  className?: string
}): JSX.Element {
  const [failed, setFailed] = useState(false)

  const frame = (children: React.ReactNode, extra = '') => (
    <span className={`shot shot-empty tone-${tone % 4} ${extra} ${className}`} role="img" aria-label={alt}>
      {children}
    </span>
  )

  // A caption rather than nothing: an unexplained coloured rectangle looks like a bug, a
  // labelled one looks like a frame waiting for its picture.
  if (failed) return frame(<span className="shot-cap">{alt}</span>)

  if (isVideo(src)) {
    return (
      <video
        className={`shot ${className}`}
        src={src}
        // A SILENT LOOP, NOT A PLAYER. It is a thumbnail: no controls, and the click belongs to
        // the card behind it, which opens sign-in like every other card.
        autoPlay
        muted
        loop
        playsInline
        // Browsers hold an autoplaying video until it is actually on screen, so a carousel of
        // these costs nothing until the card is scrolled to. `metadata` keeps that true even
        // where they do not.
        preload="metadata"
        aria-label={alt}
        onError={() => setFailed(true)}
      />
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
