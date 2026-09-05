import {
  ChartNoAxesCombined,
  ClipboardCheck,
  CloudSun,
  Dices,
  FlaskConical,
  FolderTree,
  Globe,
  Inbox,
  MousePointerClick,
  Puzzle,
  Receipt,
  Search,
  SquareTerminal,
  Store,
  Users,
  Wand2,
  type LucideIcon,
} from 'lucide-react'

/**
 * The data files name their icon as a string so they stay pure content. This is
 * the one place those names resolve to components — add the import here when a
 * new entry needs a new icon.
 */
const ICONS: Record<string, LucideIcon> = {
  ChartNoAxesCombined,
  ClipboardCheck,
  CloudSun,
  Dices,
  FlaskConical,
  FolderTree,
  Globe,
  Inbox,
  MousePointerClick,
  Puzzle,
  Receipt,
  Search,
  SquareTerminal,
  Store,
  Users,
  Wand2,
}

export function iconFor(name: string): LucideIcon {
  const icon = ICONS[name]
  if (!icon) throw new Error(`No icon registered for "${name}" — add it to src/lib/icons.ts`)
  return icon
}
