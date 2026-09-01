/* PLACEHOLDER SCREEN — the second destination. See ./README.md.
 *
 * @placeholder — SCAFFOLDING, not a decision. It shows the look, the shape and the wiring; it is
 * not what this agent is for. Adopt it (change it, rename the file, delete this tag) or delete
 * the file AND its entry in App.tsx. `validate_agent` refuses to pack or publish while the tag
 * remains.
 *
 * WHY A CHAT AGENT HAS A SECOND SCREEN AT ALL. A conversation is where work happens; it is a poor
 * place to keep what the work produced. The moment an agent says "forty-eight recipes on the
 * shelf" it has a corpus, and a corpus that can only be reached by scrolling back through old
 * turns is a corpus nobody opens twice.
 *
 * THIS IS THE SHAPE, NOT THE CONTENT. A shelf of made things is one answer; a list of sources, a
 * queue of pending jobs, a table of accounts are others. What carries over is that it is a full
 * screen reached from the rail, headed by what it is FOR, and empty until the agent has made
 * something — never pre-filled with examples that a user will mistake for their own data.
 *
 * DELETE THE WHOLE THING if this agent's answers are prose and there is nothing to keep. One
 * destination that goes nowhere costs more than the screen you did not build.
 */

import './widgets.css'

import PlaceholderResultCard, { SAMPLE_RESULT } from './PlaceholderResultCard'

/* SAMPLE ROWS, and the reason this file cannot ship. Real data comes from the agent's own tools —
   read a directory, query a table, list artifacts (see `agentd/artifacts.ts`). Until then these
   three are here to show the grid, and shipping them would mean shipping a screen that lies. */
const SAMPLE_ROWS = [
  SAMPLE_RESULT,
  { ...SAMPLE_RESULT, title: 'A second one', meta: 'so the grid has something to be' },
  { ...SAMPLE_RESULT, title: 'A third', meta: 'delete all three with the file', chips: undefined },
]

export default function PlaceholderLibraryView({ name }: { name: string }) {
  return (
    <>
      <header className="page-head">
        <div className="page-head-text">
          <h1 className="page-title">Saved</h1>
          <p className="page-sub">
            {SAMPLE_ROWS.length} things {name} has made — a placeholder shelf
          </p>
        </div>
      </header>

      <div className="stage">
        <div className="stage-main">
          <div className="shelf">
            {SAMPLE_ROWS.map((row, i) => (
              <PlaceholderResultCard key={i} data={row} onOpen={() => {}} />
            ))}
          </div>

          {/* THE EMPTY STATE IS THE ONE THAT SHIPS. A real shelf is empty on the day the agent is
              installed, and that is the screen most users see first — so write it before you
              write the full one. */}
          <p className="shelf-empty">
            When this is real, an empty shelf says what to do to fill it — not “no items”.
          </p>
        </div>
      </div>
    </>
  )
}
