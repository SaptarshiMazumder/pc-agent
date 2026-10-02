/* "It is working" — the gap between sending and the first token.
 *
 * CSS, NOT AN ANIMATION LIBRARY. The assistant's own window plays Lottie files here, which costs
 * it ~300 KB of JSON and a player. That is a fair trade for a window served off localhost and a
 * bad one for an agent somebody downloads, so this is three dots and a keyframe.
 *
 * IT SAYS WHAT IT IS WAITING FOR when it knows. A spinner that means "thinking", "running a tool"
 * and "the daemon has gone away" equally is a spinner that answers nothing — and the third of
 * those is the one a user needs to be told about.
 */

export function Thinking({ label = 'Working' }: { label?: string }) {
  return (
    <div className="thinking" role="status" aria-live="polite">
      <span className="thinking-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="thinking-label">{label}</span>
    </div>
  )
}

export default Thinking
