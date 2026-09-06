/** The shared tool catalogue every agent draws from, grouped for the tools grid. */
export interface ToolGroup {
  icon: string
  title: string
  body: string
  /** the actual tool names the daemon registers */
  tools: string[]
  /** rendered with a caution treatment */
  optIn?: boolean
}

export const TOOL_GROUPS: ToolGroup[] = [
  {
    icon: 'FolderTree',
    title: 'Your real filesystem',
    body: 'Reads, writes, and edits the files already on your disk. No upload step, no copy in someone else’s bucket.',
    tools: ['read', 'write', 'edit', 'ls', 'find', 'grep'],
  },
  {
    icon: 'SquareTerminal',
    title: 'A real shell',
    body: 'Runs commands and supervises long-lived processes — the same shell you would have typed into.',
    tools: ['exec', 'process'],
  },
  {
    icon: 'Globe',
    title: 'A real browser',
    body: 'Drives headless Chromium through Playwright, so pages behind scripts and logins are still reachable.',
    tools: ['browser'],
  },
  {
    icon: 'Search',
    title: 'The open web',
    body: 'Search and fetch with a provider chain that fails over on its own — Brave, DuckDuckGo, and friends.',
    tools: ['web_search', 'web_fetch'],
  },
  {
    icon: 'Users',
    title: 'Other agents',
    body: 'Hands work to a specialist and gets an answer back, or reroutes the whole turn with an @mention.',
    tools: ['spawn_subagent', 'message_agent'],
  },
  {
    icon: 'ClipboardCheck',
    title: 'Its own homework',
    body: 'Keeps a live plan and runs an LLM judge over its answer before calling the turn done.',
    tools: ['update_plan', 'verify_answer'],
  },
  {
    icon: 'MousePointerClick',
    title: 'The whole desktop',
    body: 'A screenshot-to-vision-to-mouse loop that drives any GUI app. Off by default; slam the mouse into a corner to kill it.',
    tools: ['computer'],
    optIn: true,
  },
  {
    icon: 'Puzzle',
    title: 'Anything else you bolt on',
    body: 'Every tool is a drop-in plugin folder, and any MCP server can be declared as one more.',
    tools: ['plugins/', 'MCP'],
  },
]
