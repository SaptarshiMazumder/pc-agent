/* A small markdown renderer.

   Agent app pages load no CDN, so this is hand-rolled — which makes ESCAPING the whole story:
   every span of source is HTML-escaped BEFORE any tag is introduced, so model output can never
   inject markup. The only tags that ever reach innerHTML are the ones written here.

   Inline code is handled by SPLITTING on backticks rather than swapping in placeholder
   markers. A placeholder needs a sentinel that cannot occur in the text, which in practice
   means a control character — invisible in the source, and enough to make git and grep treat
   the file as binary. Splitting needs no sentinel at all: odd segments are code, even segments
   are prose, and neither can be mistaken for the other. */

window.MD = (function () {
  const esc = (s) =>
    String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')

  /** Emphasis, links and images — applied to ALREADY-ESCAPED prose. */
  function fmt(s) {
    return s
      .replace(/!\[([^\]]*)\]\(([^)\s]+)\)/g, (_, alt, url) =>
        /^https?:|^\//i.test(url) ? `<img src="${url}" alt="${alt}">` : alt)
      .replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, (_, t, url) =>
        /^https?:/i.test(url) ? `<a href="${url}" target="_blank" rel="noopener">${t}</a>` : t)
      .replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>')
      .replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>')
      .replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>')
      .replace(/~~([^~]+)~~/g, '<del>$1</del>')
  }

  /** Inline: split on `code`, so its contents are escaped but never formatted. */
  function inline(src) {
    return String(src)
      .split(/`([^`]+)`/)
      .map((part, i) => (i % 2 ? `<code>${esc(part)}</code>` : fmt(esc(part))))
      .join('')
  }

  function render(src) {
    const lines = String(src || '').replace(/\r\n?/g, '\n').split('\n')
    const out = []
    let i = 0

    const listOf = (marker) => {
      const ordered = marker === 'ol'
      const items = []
      while (i < lines.length) {
        const m = lines[i].match(ordered ? /^\s*\d+[.)]\s+(.*)$/ : /^\s*[-*+]\s+(.*)$/)
        if (!m) break
        items.push(`<li>${inline(m[1])}</li>`)
        i++
      }
      out.push(`<${marker}>${items.join('')}</${marker}>`)
    }

    while (i < lines.length) {
      const line = lines[i]

      // fenced code — contents are escaped and NOTHING else is applied
      const fence = line.match(/^\s*```+\s*([\w+-]*)\s*$/)
      if (fence) {
        const body = []
        i++
        while (i < lines.length && !/^\s*```+\s*$/.test(lines[i])) body.push(lines[i++])
        i++ // closing fence (or EOF — an unterminated block still renders)
        const lang = fence[1] ? ` class="lang-${fence[1]}"` : ''
        out.push(`<pre><code${lang}>${esc(body.join('\n'))}</code></pre>`)
        continue
      }

      if (!line.trim()) { i++; continue }

      const h = line.match(/^(#{1,6})\s+(.*)$/)
      if (h) { out.push(`<h${h[1].length}>${inline(h[2])}</h${h[1].length}>`); i++; continue }

      if (/^\s*([-*_])\s*\1\s*\1[\s\-*_]*$/.test(line)) { out.push('<hr>'); i++; continue }

      if (/^\s*[-*+]\s+/.test(line)) { listOf('ul'); continue }
      if (/^\s*\d+[.)]\s+/.test(line)) { listOf('ol'); continue }

      if (/^\s*>\s?/.test(line)) {
        const body = []
        while (i < lines.length && /^\s*>\s?/.test(lines[i])) {
          body.push(lines[i++].replace(/^\s*>\s?/, ''))
        }
        out.push(`<blockquote>${render(body.join('\n'))}</blockquote>`)
        continue
      }

      // table: a header row followed by a |---|---| separator
      if (line.includes('|') && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1] || '')) {
        const cells = (r) => r.trim().replace(/^\||\|$/g, '').split('|').map((c) => c.trim())
        const head = cells(line)
        i += 2
        const rows = []
        while (i < lines.length && lines[i].includes('|')) rows.push(cells(lines[i++]))
        out.push(
          `<table><thead><tr>${head.map((c) => `<th>${inline(c)}</th>`).join('')}</tr></thead>` +
          `<tbody>${rows
            .map((r) => `<tr>${r.map((c) => `<td>${inline(c)}</td>`).join('')}</tr>`)
            .join('')}</tbody></table>`
        )
        continue
      }

      // paragraph — runs until a blank line or the start of another block
      const para = []
      while (
        i < lines.length && lines[i].trim() &&
        !/^\s*(```|#{1,6}\s|>|[-*+]\s|\d+[.)]\s)/.test(lines[i])
      ) para.push(lines[i++])
      out.push(`<p>${inline(para.join('\n'))}</p>`)
    }
    return out.join('\n')
  }

  return { render, esc }
})()
