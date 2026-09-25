/* LandingShot — one card's picture: a still, a clip, or the frame one goes in.
 *
 * IT DEGRADES BY DESIGN, NOT BY ACCIDENT. A card whose file is absent renders a tinted, labelled
 * panel that reads as art direction; the moment a file exists at the path it is used instead,
 * with no code change. `onError` is the whole mechanism — a missing file is an expected state
 * here, not a fault worth logging. That is what let the page ship before there were any images.
 *
 * STILLS LOAD LAZILY: `loading="lazy"`, so the browser fetches only what is near the screen.
 *
 * CLIPS LOAD NOTHING UNTIL THEY ARE ABOUT TO BE SEEN. `autoPlay` makes a browser download the
 * whole file wherever the video sits on the page, so the page's clips used to cost ~1 MB on first
 * load for videos nobody had scrolled to. Instead: `preload="none"` with the clip's poster frame
 * (`<name>-poster.jpg` beside it, 20–40 KB) as the picture, and an IntersectionObserver that
 * starts it a little before it scrolls in and pauses it once it leaves. Muted and `playsInline`
 * are both required or mobile browsers refuse to play without a tap.
 *
 * TO ADD OR REPLACE ONE: drop a file in `app/public/marketing/` and rebuild. The names are the
 * `src` values LandingPage passes; nothing else needs touching. A clip also wants its
 * `-poster.jpg` (one frame); without it the frame is simply dark until the clip starts.
 */

import { useEffect, useRef, useState } from 'react'

const isVideo = (src: string) => /\.(mp4|webm|mov)$/i.test(src)
/** `marketing/x.mp4` -> `marketing/x-poster.jpg`, the frame shown before the clip loads. */
const posterFor = (src: string) => src.replace(/\.(mp4|webm|mov)$/i, '-poster.jpg')

/** Plays while on screen (and just before), pauses once off it; loads nothing until then. */
function useOnScreenPlayback() {
  const ref = useRef<HTMLVideoElement | null>(null)
  useEffect(() => {
    const v = ref.current
    if (!v) return
    /* MUTED AS A PROPERTY, NOT ONLY THE PROP: React does not reliably reflect `muted` onto the
       element on first render, and a browser refuses `play()` without a gesture on anything it
       believes has sound. */
    v.muted = true
    // No observer (an old browser): just play, as the page always did.
    if (typeof IntersectionObserver === 'undefined') {
      void v.play().catch(() => {})
      return
    }
    const io = new IntersectionObserver(
      ([entry]) => {
        if (entry.isIntersecting) void v.play().catch(() => {})
        else v.pause()
      },
      { rootMargin: '200px 0px' },
    )
    io.observe(v)
    return () => io.disconnect()
  }, [])
  return ref
}

export function LandingShot({
  src,
  alt,
  tone = 0,
  className = '',
}: {
  /** Path under public/, e.g. "marketing/selfie-face.webp". Absent is fine. */
  src: string
  alt: string
  /** Picks one of the placeholder gradients, so a row of them does not read as one flat block. */
  tone?: number
  className?: string
}): JSX.Element {
  const [failed, setFailed] = useState(false)
  const videoRef = useOnScreenPlayback()

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
        ref={videoRef}
        className={`shot ${className}`}
        src={src}
        poster={posterFor(src)}
        // A SILENT LOOP, NOT A PLAYER. It is a thumbnail: no controls, and the click belongs to
        // the card behind it, which opens sign-in like every other card.
        muted
        loop
        playsInline
        preload="none"
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
