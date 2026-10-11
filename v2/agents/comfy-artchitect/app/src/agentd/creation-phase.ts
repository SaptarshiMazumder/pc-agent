/* WHERE A CREATION STANDS — the three phases of AGENTS.md (design → set up → run), read off facts
 * the window already has. Nothing is stored: the phase is a pure function of the thread (is the
 * agent's card waiting for an answer?), the stage records (is there a design yet?) and the install
 * feed (is Comfy Cloud still importing?).
 *
 *   design   the DESIGN card (pipeline_present) waits for its answer, or nothing is designed yet —
 *            no stage plan and no workflow in the chat
 *   setup    something is installing, or a setup call is in flight in the running turn
 *   run      the rest: a pipeline's stages, or a template's / a kept workflow's files
 *
 * ONLY THE DESIGN CARD IS DESIGN. A question the agent asks mid-run (ask_user) is answered in the
 * chat and leaves the stages on screen; it used to flip the whole studio back to Design. And a
 * chat run from a template or a kept workflow has no stage plan — it is in Run once its workflow
 * is there, or its outputs and files had no way onto the screen.
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

/** The design card waiting for its answer (pipeline_present), or null. The studio shows it; the
 *  thread shows a pointer. Any other card stays in the thread. */
export function designCard(items: ThreadItem[]): ToolItem | null {
  const item = pendingAsk(items)
  return item && item.name === 'pipeline_present' ? item : null
}

/** The tools that set Comfy Cloud up for a design — importing models, installing a template. */
const SETUP_TOOLS = new Set(['pipeline_provision', 'template_setup', 'comfy_install'])

export function phaseOf(
  items: ThreadItem[],
  plan: StagePlan | null,
  installing: boolean,
  running: boolean,
  hasWorkflow: boolean,
): Phase {
  if (designCard(items) || (!plan && !hasWorkflow)) return 'design'
  // Set up while a download streams (the install feed) OR a setup call is in flight IN THE
  // RUNNING TURN — a provision that is checking rather than downloading shows no rows yet. Tied to
  // the turn because a failed call is never marked done: the label stuck on Set up until reload.
  if (installing || (running && items.some((i) => i.kind === 'tool' && !i.done && SETUP_TOOLS.has(i.name)))) return 'setup'
  return 'run'
}
