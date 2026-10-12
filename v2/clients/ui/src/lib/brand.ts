/** The platform brand; product apps retain their own names. */
export const APP_NAME = 'AgentBear'

/** Older installers may still supply the platform's previous display name. */
export function productDisplayName(name?: string): string {
  const value = name?.trim()
  if (!value) return APP_NAME
  return value.replace(/^(?:agentd|rapidbase)(?=$|\s+\()/i, APP_NAME)
}
