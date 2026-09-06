/** Agents shipped in the box or published to the registry. Content only — no JSX. */
export interface AgentEntry {
  id: string
  name: string
  tagline: string
  /** lucide-react icon name, resolved by the card component */
  icon: string
  /** short capability chips */
  tags: string[]
  /** true for agents that ship their own window app */
  window?: boolean
  featured?: boolean
}

export const AGENTS: AgentEntry[] = [
  {
    id: 'comfy-artchitect',
    name: 'Comfy Artchitect',
    tagline:
      'Connects to your live ComfyUI box, reads what is actually installed, builds the graph, runs it, and repairs it from the server’s own errors.',
    icon: 'Wand2',
    tags: ['12 custom tools', 'studio dashboard', 'installs models'],
    window: true,
    featured: true,
  },
  {
    id: 'figure-create',
    name: 'Bio Figure',
    tagline:
      'Publication-grade scientific figures with an interactive canvas editor and editable vector labels.',
    icon: 'FlaskConical',
    tags: ['canvas editor', 'vector labels'],
    window: true,
    featured: true,
  },
  {
    id: 'inbox-triage',
    name: 'Inbox Triage',
    tagline:
      'Wakes itself every ten minutes, watches the inbox, and has the what-needs-a-reply digest waiting by morning.',
    icon: 'Inbox',
    tags: ['autonomous', '10m heartbeat'],
    featured: true,
  },
  {
    id: 'expense-summarizer',
    name: 'Expense Summarizer',
    tagline: 'Reads your bank and card CSVs, totals and categorizes the month, draws the charts.',
    icon: 'Receipt',
    tags: ['local files', 'charts'],
  },
  {
    id: 'weather',
    name: 'Weather',
    tagline: 'An 8am briefing in your inbox and a live conditions dashboard you can open any time.',
    icon: 'CloudSun',
    tags: ['scheduled', 'dashboard'],
    window: true,
  },
  {
    id: 'sakana-sushi',
    name: 'Sakana Sushi',
    tagline:
      'A customer-facing reservation agent that answers on a real messaging channel, with egress privacy gating.',
    icon: 'Store',
    tags: ['customer-facing', 'channel-bound'],
  },
  {
    id: 'game-master',
    name: 'Game Master',
    tagline: 'A tabletop RPG referee that rolls the dice, looks up the monsters, and narrates.',
    icon: 'Dices',
    tags: ['long sessions'],
  },
  {
    id: 'aws-cost-monitor',
    name: 'AWS Cost Monitor',
    tagline: 'Watches spend per resource and tells you the moment a threshold gets crossed.',
    icon: 'ChartNoAxesCombined',
    tags: ['autonomous', 'alerts'],
  },
]
