/* Tool calls, read as PROGRESS instead of plumbing.
 *
 * A transcript that says `comfy_emit`, `comfy_validate`, `comfy_run` is telling the user which
 * functions ran. What they actually want to know is what got DONE — "Workflow written · 16 nodes",
 * "Compiles against your instance", "Rendered 1 output". Same events, read as milestones.
 *
 * WHY A TABLE AND NOT A RENAME. The rows still exist and still expand: a tool that failed, or that
 * somebody wants the arguments of, must stay inspectable — this is a headline, not a replacement.
 * And the mapping is per-agent by nature (a builder's milestones are not a ComfyUI agent's), so it
 * lives beside the app that knows its own tools rather than inside the shared component.
 *
 * A tool with no entry here simply renders as it always did. Adding one is one line.
 */

export interface Milestone {
  /** The headline — what happened, in the user's terms. */
  label: string
  /** A short detail drawn from the call or its result: a count, a filename. */
  detail?: string
}

/** `wrote 16 nodes — …` -> `16 nodes`; the first number and its unit, when the result leads with
 *  one. Result text is written for a model, so this takes only what reads well as a badge. */
function leadCount(result: string, unit: string): string | undefined {
  const m = new RegExp(`(\\d+)\\s*${unit}`, 'i').exec(result || '')
  return m ? `${m[1]} ${unit}${m[1] === '1' ? '' : 's'}` : undefined
}

function fileName(v: unknown): string | undefined {
  const s = String(v || '')
  if (!s) return undefined
  return s.split(/[\\/]/).pop() || undefined
}

type Read = (args: Record<string, unknown>, result: string, isError: boolean) => Milestone | null

/** The ComfyUI agent's own vocabulary. Keyed by tool name; null = render as a plain tool row. */
export const MILESTONES: Record<string, Read> = {
  comfy_probe: (_a, result, isError) =>
    isError
      ? { label: 'Instance unreachable' }
      : { label: 'Connected to your ComfyUI', detail: (result.split('\n')[0] || '').slice(0, 60) },

  comfy_emit: (a, result, isError) =>
    isError
      ? null
      : { label: 'Workflow written', detail: leadCount(result, 'node') || fileName(a.name) },

  comfy_validate: (_a, result, isError) =>
    isError
      ? { label: 'Workflow does not compile yet', detail: 'repairing' }
      : { label: 'Compiles against your instance', detail: leadCount(result, 'node') },

  comfy_install: (a, _r, isError) =>
    isError ? null : { label: 'Model installed', detail: fileName(a.filename) },

  comfy_node_install: (a, _r, isError) =>
    isError ? null : { label: 'Node pack installed', detail: String(a.pack || '').slice(0, 40) },

  comfy_run: (_a, result, isError) =>
    isError
      ? null
      : /still rendering/i.test(result)
        ? { label: 'Rendering…', detail: 'continues on the instance' }
        : { label: 'Render complete', detail: leadCount(result, 'output') },

  comfy_run_status: (_a, result, isError) =>
    isError || /still rendering/i.test(result)
      ? null
      : { label: 'Render complete', detail: leadCount(result, 'output') },

  comfy_download: (_a, _r, isError) => (isError ? null : { label: 'Outputs pulled into the chat' }),

  comfy_upload: (_a, _r, isError) => (isError ? null : { label: 'Reference sent to the instance' }),
}

/** The milestone for one finished tool call, or null to render it as an ordinary tool row. */
export function milestoneFor(
  name: string,
  args: Record<string, unknown>,
  result: string,
  isError: boolean,
): Milestone | null {
  const read = MILESTONES[name]
  if (!read) return null
  try {
    return read(args || {}, result || '', isError)
  } catch {
    return null // a badge must never be able to break the transcript
  }
}
