/* PLACEHOLDER WIDGET — the standing sentence. See ./README.md.
 *
 * @placeholder — SCAFFOLDING, not a decision. It shows the look, the shape and the wiring; it is
 * not what this agent is for. Adopt it (change it, rename the file, delete this tag) or delete
 * the file. `validate_agent` refuses to pack or publish while the tag remains.
 *
 * ONE THING THAT IS ALWAYS TRUE of this agent, in the corner where a user's eye lands when they
 * are deciding whether to trust it: where the data lives, what leaves the machine, what it will
 * never do. It is not a tip and not a status — those change, and this does not.
 *
 * If the agent has no such sentence, delete this. A note saying nothing teaches people to ignore
 * the corner it sits in, which is the corner you will want later.
 */

import './widgets.css'

export default function PlaceholderNote({
  children = 'One sentence that is always true of this agent — where its data lives, or what it will never do. Delete this card if there is no such sentence.',
}: {
  children?: React.ReactNode
}) {
  return <p className="aside-note">{children}</p>
}
