import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'

/** The ONE shared markdown renderer (GitHub-flavored). Every markdown surface — chat messages,
 *  the canvas preview, tool/plugin/skill descriptions — routes through here so formatting is
 *  identical everywhere. `className` selects a size/spacing variant layered on `.markdown`. */
export default function Markdown({ text, className }: { text: string; className?: string }): JSX.Element {
  return (
    <div className={`markdown ${className || ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
    </div>
  )
}
