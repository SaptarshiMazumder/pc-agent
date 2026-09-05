/**
 * The aurora glow behind the hero — the same three-hue wash the app paints
 * behind an empty chat. Pure CSS blobs; decorative, so it is aria-hidden and
 * its drift stops under prefers-reduced-motion.
 */
export function Aurora() {
  return (
    <div className="aurora" aria-hidden="true">
      <span className="aurora__blob aurora__blob--lime" />
      <span className="aurora__blob aurora__blob--blue" />
      <span className="aurora__blob aurora__blob--violet" />
    </div>
  )
}
