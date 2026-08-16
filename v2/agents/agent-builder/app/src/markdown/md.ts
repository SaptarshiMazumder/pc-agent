/* The markdown renderer — BORROWED, not reimplemented.
 *
 * `md.js` is one of the two files every scaffolded agent is given a copy of (the other is the
 * vendored SDK). Writing a TypeScript twin of it here would put two renderers in one product,
 * which is exactly the drift `templates/_borrowed/` exists to prevent: a fix applied to the one
 * a generated agent ships with would quietly not apply to the one Agent Builder itself uses.
 *
 * So this imports the real file for its side effect — it is a plain script that assigns
 * `window.MD` — and does nothing but give it a type and a name worth importing.
 *
 * ESCAPING IS THE WHOLE POINT of that file: every span of source is HTML-escaped before any tag
 * is introduced, so model output reaches `dangerouslySetInnerHTML` with no markup of its own.
 * Read it before changing anything here.
 */

import '../../../skills/build-agent/templates/_borrowed/md.js'

interface MarkdownRenderer {
  /** Markdown -> HTML. Everything is escaped first; the only tags in the output are its own. */
  render(src: string): string
  /** HTML-escape a string. */
  esc(s: unknown): string
}

const MD = (window as unknown as { MD?: MarkdownRenderer }).MD

// Not a defensive fallback — a hard stop. A missing renderer means the borrow root moved or the
// build dropped the import, and rendering raw model output as HTML instead would be an XSS hole
// opened by a silent failure.
if (!MD) {
  throw new Error('md.js did not load — window.MD is missing; the markdown renderer is required')
}

export const renderMarkdown = (src: string): string => MD.render(src)
export const escapeHtml = (s: unknown): string => MD.esc(s)
