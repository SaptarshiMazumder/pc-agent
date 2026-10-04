/* The one strip of chrome the studio gets.
 *
 * WHAT IT REPLACES, and why the replacement is smaller. The old toolbar carried a search box, a
 * status chip, a "new run" button and an avatar; below it sat a title, a paragraph of prose, a
 * range switcher and four KPI cards with meters and sparklines. Fifteen surfaces competing before
 * you had looked at a single thing the agent made. None of it was the work.
 *
 * So: the two tabs, where the work runs, and the balance. "Comfy Cloud" is a plain label, not a
 * status — every workflow runs there with the person's own key, and nothing here checks it; a
 * failure says so when a workflow is validated or run.
 */

import { useLibraryFlash } from '../../state/use-library-flash'

/** The stage's two tabs: this chat's own files, and the Library every chat shares. */
export type StudioPanel = 'workspace' | 'library'

export function StudioTopBar({
  credits,
  onCredits,
  panel,
  onPanel,
  attention = false,
}: {
  credits: number | null
  onCredits: () => void
  panel: StudioPanel
  onPanel: (p: StudioPanel) => void
  /** The Workspace holds an input the agent is waiting on. */
  attention?: boolean
}) {
  const flash = useLibraryFlash()
  return (
    <header className="sb">
      {/* TWO TABS, TWO SCOPES. Workspace is this chat's — its inputs, renders, workflow and
          files. Library is everyone's — what the person chose to keep, reachable from any chat. */}
      <div className="sb-tabs" role="tablist" aria-label="Studio panel">
        <button
          type="button"
          role="tab"
          aria-selected={panel === 'workspace'}
          className={`sb-tab${panel === 'workspace' ? ' on' : ''}${attention ? ' is-attention' : ''}`}
          onClick={() => onPanel('workspace')}
          title="This chat's inputs, renders, workflow and files"
        >
          Workspace
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={panel === 'library'}
          className={`sb-tab${panel === 'library' ? ' on' : ''}${flash ? ' is-flash' : ''}`}
          onClick={() => onPanel('library')}
          title="What you kept — shared by every chat"
        >
          Library
        </button>
      </div>

      <span className="sb-spacer" />

      <span className="sb-engine" title="Every workflow runs on Comfy Cloud with your Comfy API key">
        Comfy Cloud
      </span>

      <button className="sb-credits" onClick={onCredits} title="Credits">
        {credits != null ? `${credits.toLocaleString()} cr` : '—'}
      </button>
    </header>
  )
}
