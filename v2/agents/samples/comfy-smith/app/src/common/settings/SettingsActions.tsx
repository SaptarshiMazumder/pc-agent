/* What an `extras` slot can do to the page it sits in.
 *
 * COPIED VERBATIM from the common modules. Do not edit; `validate_agent` compares it against the
 * source.
 *
 * WHY THIS EXISTS. `extras` renders a window's own controls inside the shared page, and one of
 * them needs something back: a "Test connection" button, beside the URL field the agent declared
 * in `[[settings]]`, has to SAVE before it probes. The tool it calls reads the agent's
 * environment, not this form — so a button that tests without saving first reports success or
 * failure about the value you just replaced. That was a real bug in a sample: paste a new server
 * URL, press Test, and be told about the old one.
 *
 * The edit buffer belongs to the page (`useSettings`), so the button cannot save on its own. This
 * hands it the two things it needs and nothing else: is there anything unsaved, and save it.
 *
 * A CONTEXT RATHER THAN PROPS because `extras` is a ReactNode — already constructed by the time
 * the page receives it, so there is nowhere to pass arguments. Anything rendered inside the page
 * can ask; anything outside it gets the inert default and a clear error if it tries to save.
 */

import { createContext, useContext } from 'react'

export interface SettingsActions {
  /** Is there an unsaved edit anywhere on the page? */
  dirty: boolean
  /** Save every pending edit. Resolves to whether the daemon needs a restart to apply them —
   *  the page handles that part; a caller testing a connection can ignore it. */
  commit: () => Promise<boolean>
}

const FALLBACK: SettingsActions = {
  dirty: false,
  // NOT a silent no-op. A control that calls this is outside the page, which means it is wired
  // wrong — and a save that quietly does nothing is the failure that looks exactly like success.
  commit: () => {
    throw new Error(
      'useSettingsActions() outside <Settings>. Render this control through the `extras` prop.',
    )
  },
}

export const SettingsActionsContext = createContext<SettingsActions>(FALLBACK)

/** For a control rendered through `extras` that needs to save before it acts. */
export function useSettingsActions(): SettingsActions {
  return useContext(SettingsActionsContext)
}
