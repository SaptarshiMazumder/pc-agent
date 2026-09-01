/* WHAT A SECTION IS. Not a placeholder — this is the template's contract, and the shape every
 * screen in this window takes.
 *
 * THE RAIL'S MIDDLE IS THE AGENT'S OWN SECTIONS, so a section is a whole screen rather than a
 * tab: its own head, its own panels, its own table. `App.tsx` renders the head from the fields
 * below and hands the body to `render` — which means adding a screen is one entry in a list, and
 * the crumb line, the title, the icon and the rail row all follow from it.
 *
 * WHY A LIST AND NOT A ROUTER. An agent has a handful of screens, and they are known at build
 * time. A route table would add a dependency and an indirection to solve a problem this window
 * does not have — and `SECTIONS` being a plain array is what lets an agent author add a screen
 * without learning anything but this file.
 */

import type { AgentdClient } from '@agentd/client'

export interface SectionSpec {
  /** Also the view id — the rail marks a row active by matching it. Keep it url-ish. */
  id: string
  /** The rail row's text. */
  label: string
  icon: JSX.Element
  /** A number beside the rail row: how many things need attention in here. Omit it unless the
   *  figure is real — a count that is a guess is worse than no count. */
  count?: string
  /** THE POINT OF THE SCREEN, not a greeting. "Spend is up 22% on last week" earns the space;
   *  "Welcome back" does not. Until it can be computed, say what the section is for. */
  headline: string
  blurb: string
  /** The body, under the head. Gets the connection so a section can fetch its own data. */
  render: (ctx: { client: AgentdClient; connected: boolean }) => JSX.Element
}
