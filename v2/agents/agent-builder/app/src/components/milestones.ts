/* Tool calls, read as PROGRESS instead of plumbing.
 *
 * A transcript that says `create_tool`, `verify_app`, `validate_agent` is telling the user which
 * functions ran. What they actually want to know is what got DONE — "Tool written · fetch_prices",
 * "Window builds clean", "Ready to ship". Same events, read as milestones.
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
  /** A short detail drawn from the call or its result: a name, a count. */
  detail?: string
}

/** The agent being worked on, as the user named it — the detail that makes a row identifiable
 *  when a session builds more than one. */
function agentName(args: Record<string, unknown>): string | undefined {
  const s = String(args.agentId || args.agent_id || args.id || args.name || '')
  return s ? s.slice(0, 40) : undefined
}

/** `wrote 3 problems — …` -> `3 problems`; the first number and its unit, when the result leads
 *  with one. Result text is written for a model, so this takes only what reads well as a badge. */
function leadCount(result: string, unit: string): string | undefined {
  const m = new RegExp(`(\\d+)\\s*${unit}`, 'i').exec(result || '')
  return m ? `${m[1]} ${unit}${m[1] === '1' ? '' : 's'}` : undefined
}

type Read = (args: Record<string, unknown>, result: string, isError: boolean) => Milestone | null

/** The builder's own vocabulary. Keyed by tool name; null = render as a plain tool row. */
export const MILESTONES: Record<string, Read> = {
  create_agent: (a, _r, isError) =>
    isError ? null : { label: 'Agent created', detail: agentName(a) },

  create_tool: (a, _r, isError) =>
    isError ? null : { label: 'Tool written', detail: String(a.name || '').slice(0, 40) },

  skill_workshop: (a, _r, isError) =>
    isError ? null : { label: 'Skill written', detail: String(a.name || '').slice(0, 40) },

  scaffold_react_app: (a, _r, isError) =>
    isError ? null : { label: 'Window scaffolded', detail: agentName(a) },

  build_app: (_a, _r, isError) =>
    isError ? { label: 'Window does not build yet', detail: 'repairing' } : { label: 'Window built' },

  // PROOF, and the absence of it. e2e_run is the one row that says the thing was actually
  // exercised rather than merely built -- so it announces on both paths, and a skip announces
  // too, because "tests waived" is exactly the kind of thing a user must see rather than infer.
  e2e_run: (_a, result, isError) =>
    isError
      ? { label: 'Tests failed', detail: 'fixing' }
      : { label: 'Tested end to end', detail: leadCount(result, 'check') },

  skip_e2e: (_a, _r, isError) =>
    isError ? null : { label: 'Tests skipped', detail: 'you waived them for this run' },

  // The two gates. A failure here is the single most useful thing to surface loudly, because it
  // is the difference between "it exists" and "it works" — so unlike most tools these announce
  // themselves on the error path too.
  verify_app: (_a, result, isError) =>
    isError
      ? { label: 'Window failed verification', detail: 'repairing' }
      : { label: 'Window verified', detail: (result.split('\n')[0] || '').slice(0, 60) },

  validate_agent: (_a, result, isError) =>
    isError || /\b[1-9]\d*\s*(problem|error|issue)/i.test(result)
      ? {
          label: 'Validation found problems',
          detail: leadCount(result, 'problem') || leadCount(result, 'error') || 'repairing',
        }
      : { label: 'Agent validates clean' },

  run_agent: (a, _r, isError) =>
    isError ? null : { label: 'Test drive complete', detail: agentName(a) },

  reload_agent: (a, _r, isError) =>
    isError ? null : { label: 'Agent reloaded', detail: agentName(a) },

  package_agent: (a, _r, isError) =>
    isError ? null : { label: 'Packaged', detail: agentName(a) },

  publish_agent: (a, _r, isError) =>
    isError ? null : { label: 'Published to the store', detail: agentName(a) },
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
