/** A display name -> the kebab-case id the daemon files it under. Mirrors `_slug` in
 *  create_agent_tool.py; the tool re-derives it anyway, so a disagreement is cosmetic rather
 *  than a second source of truth. Lives here because two surfaces now derive it — the shell's
 *  createAgent call and the Blueprint page's live slug preview — and they must agree. */
export function slugOf(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '')
}
