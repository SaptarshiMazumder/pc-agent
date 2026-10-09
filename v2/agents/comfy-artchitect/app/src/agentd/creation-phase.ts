/* WHERE A CREATION STANDS — the three phases of AGENTS.md (design → set up → run), read off facts
 * the window already has. Nothing is stored: the phase is a pure function of the thread (is the
 * agent's card waiting for an answer?), the stage records (is there a design yet?) and the install
 * feed (is Comfy Cloud still importing?).
 *
 *   design   no stage plan yet, or the latest card (pipeline_present / ask_user) has no answer
 *   setup    the plan is approved and something is still installing
 *   run      the plan is there, approved, and set up — the stages are the person's to run
 */

import { askOf, type ThreadItem } from './chat'
import type { StagePlan } from './stage-plan'

export type Phase = 'design' | 'setup' | 'run'

export type ToolItem = Extract<ThreadItem, { kind: 'tool' }>

/** The latest card in the thread that no user message has answered yet — the same rule the thread
 *  marks answered by (a user message after the call). */
export function pendingAsk(items: ThreadItem[]): ToolItem | null {
  for (let i = items.length - 1; i >= 0; i--) {
    const item = items[i]
    if (item.kind === 'user') return null
    if (item.kind === 'tool' && askOf(item)) return item
  }
  return null
}

/** The tools that set Comfy Cloud up for a design — importing models, installing a template. */
const SETUP_TOOLS = new Set(['pipeline_provision', 'template_setup', 'comfy_install'])

export function phaseOf(items: ThreadItem[], plan: StagePlan | null, installing: boolean): Phase {
  if (pendingAsk(items) || !plan) return 'design'
  // Set up while a download streams (the install feed) OR a setup call is still in flight —
  // a provision that is checking rather than downloading shows no rows yet.
  if (installing || items.some((i) => i.kind === 'tool' && !i.done && SETUP_TOOLS.has(i.name))) return 'setup'
  return 'run'
}
