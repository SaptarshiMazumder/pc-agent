/**
 * Presentation metadata for agents — colour, tagline, initials.
 * These are NOT server fields; they're a UI nicety. Known agents get curated
 * values; unknown agents get a stable colour hashed from their id.
 */

export interface AgentPresentation {
  color: string
  tag: string
}

const KNOWN: Record<string, AgentPresentation> = {
  main: { color: '#a3e635', tag: 'general · all tools' },
  'expense-tracker': { color: '#f5b13d', tag: 'finance · gmail' },
  'presentation-creator': { color: '#5aa9f0', tag: 'decks · video' },
  'figure-creator': { color: '#a78bfa', tag: 'scientific figures' },
  'sakana-sushi': { color: '#f2849e', tag: 'front desk' }
}

const PALETTE = ['#a3e635', '#f5b13d', '#5aa9f0', '#a78bfa', '#f2849e', '#5eead4']

export function agentColor(id: string): string {
  if (KNOWN[id]) return KNOWN[id].color
  let h = 0
  for (let i = 0; i < id.length; i++) h = (h * 31 + id.charCodeAt(i)) >>> 0
  return PALETTE[h % PALETTE.length]
}

export function agentTag(id: string): string {
  return KNOWN[id]?.tag || 'agent'
}

export function agentInitials(name: string | undefined, id: string): string {
  const s = (name || id).trim()
  const parts = s.split(/[\s-]+/).filter(Boolean)
  if (parts.length >= 2) return (parts[0][0] + parts[1][0]).toUpperCase()
  return s.slice(0, 2).toUpperCase()
}
