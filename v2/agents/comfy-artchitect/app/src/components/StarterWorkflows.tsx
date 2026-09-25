/* "Start from your Library" — the saved workflows, on an empty chat.
 *
 * THE PROMISE, AT THE MOMENT IT PAYS OFF. The product is sold on "make it once, run it forever";
 * an empty chat is exactly when somebody who already made something wants to make it again, and
 * the Library is otherwise a click away in the rail. So the newest few kept workflows sit under
 * the composer, each with the Library's own Run again.
 *
 * THE SAME ACTION AS THE LIBRARY'S, not a new one: Run again starts a fresh conversation with the
 * workflow's ask in the composer (App.onRunAgain). Nothing is sent until the person sends it.
 * No Library, or nothing kept yet: this renders nothing — the starter prompts carry the opening.
 */

import { Play, Workflow as WorkflowIcon } from 'lucide-react'
import { useEffect, useState } from 'react'

import type { AgentdClient } from '@agentd/client'

import { latestVersion, readIndex, type LibraryItem } from '../agentd/library'

const SHOWN = 4

export function StarterWorkflows({
  client,
  workspaceVersion,
  onRunAgain,
}: {
  client: AgentdClient | undefined
  /** Bumped when the Library may have changed, so a workflow saved a moment ago shows. */
  workspaceVersion: number
  onRunAgain: (item: LibraryItem) => void
}) {
  const [items, setItems] = useState<LibraryItem[]>([])

  useEffect(() => {
    if (!client) return
    let alive = true
    readIndex(client)
      .then((idx) => alive && setItems(idx.items.filter((i) => i.kind === 'workflow')))
      // A Library that cannot be read is simply not offered here; the Library page says why.
      .catch(() => alive && setItems([]))
    return () => {
      alive = false
    }
  }, [client, workspaceVersion])

  if (!items.length) return null
  // Newest first: the catalogue is in the order things were kept.
  const shown = [...items].reverse().slice(0, SHOWN)

  return (
    <section className="sw" aria-label="Start from your Library">
      <div className="sw-head">
        <span className="sw-title">Start from your Library</span>
        <span className="sw-sub">run a saved workflow again</span>
      </div>
      <div className="sw-grid">
        {shown.map((item) => {
          const v = latestVersion(item)
          const slots = item.versions.find((x) => x.v === v)?.slots || []
          return (
            <article key={item.id} className="sw-card">
              <span className="sw-ico" aria-hidden="true">
                <WorkflowIcon size={16} strokeWidth={1.8} />
              </span>
              <div className="sw-text">
                <b className="sw-name">{item.name}</b>
                <span className="sw-meta">
                  {item.note || (slots.length ? `needs: ${slots.join(', ')}` : item.from?.title ? `from ${item.from.title}` : 'saved workflow')}
                </span>
              </div>
              <button
                type="button"
                className="sw-run"
                onClick={() => onRunAgain(item)}
                title="Start a new conversation that runs this workflow again"
              >
                <Play size={12} strokeWidth={2.2} /> Run again
              </button>
            </article>
          )
        })}
      </div>
    </section>
  )
}

export default StarterWorkflows
