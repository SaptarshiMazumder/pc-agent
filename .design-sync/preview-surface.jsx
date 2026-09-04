/* The ground every preview stands on — what the app's own `body` rule paints.
 *
 * This design system is DARK: `body { background: var(--bg); color: var(--text) }` lives in
 * styles.css, and the tokens put near-white text on a deep surface with an ambient glow. The
 * preview card's chrome paints a white page, so without this wrapper every cell renders
 * light-on-white — technically styled, visually invisible. Wired as `cfg.provider`, so every
 * story gets the same ground the window gives its components. Not a reimplementation: the two
 * declarations are the body rule's, verbatim.
 */
export function PreviewSurface({ children }) {
  return (
    <div
      style={{
        background: 'var(--bg)',
        color: 'var(--text)',
        padding: 20,
        minHeight: '100%',
        boxSizing: 'border-box',
        /* the theme's ambient glow is painted by an absolutely-positioned child; without this
           it bleeds past the surface onto the card's white chrome */
        overflow: 'hidden',
        position: 'relative',
      }}
    >
      {children}
    </div>
  )
}
