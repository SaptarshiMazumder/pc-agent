/** Turn a saved session's wire-form transcript into readable Markdown + save it to disk. */

/** Render user + assistant text turns as Markdown (thinking / tool calls are omitted so the
 *  export reads as a clean conversation). Assistant content is a block array; user is a string. */
export function sessionToMarkdown(title: string, messages: any[]): string {
  const out: string[] = [`# ${title || 'Chat'}`, '']
  for (const m of messages) {
    if (m.role === 'user') {
      const text = String(m.content ?? '').trim()
      if (text) out.push('## 🧑 User', '', text, '')
    } else if (m.role === 'assistant') {
      const text = (Array.isArray(m.content) ? m.content : [])
        .filter((b: any) => b?.type === 'text' && b.text)
        .map((b: any) => String(b.text))
        .join('\n\n')
        .trim()
      if (text) out.push('## 🤖 Assistant', '', text, '')
    }
  }
  return out.join('\n')
}

/** A filesystem-safe basename derived from a chat title. */
export function safeFileName(name: string): string {
  return (name || 'chat').replace(/[^\w.\- ]+/g, '_').trim().slice(0, 80) || 'chat'
}

/** Trigger a browser download of text content (Blob + transient anchor). */
export function downloadTextFile(filename: string, text: string, mime = 'text/markdown'): void {
  const url = URL.createObjectURL(new Blob([text], { type: `${mime};charset=utf-8` }))
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  document.body.appendChild(a)
  a.click()
  a.remove()
  setTimeout(() => URL.revokeObjectURL(url), 1000)
}
