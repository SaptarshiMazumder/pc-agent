import type { ReactNode } from 'react'
import {
  Sparkles,
  Play,
  Receipt,
  MessageSquare,
  Cpu,
  Globe,
  Eye,
  Package,
  FileText,
  Image,
  Video,
  Music,
  Wrench,
  FlaskConical,
  BarChart3,
  Bot,
  type LucideIcon
} from 'lucide-react'

/**
 * Design-system glyph registry: resolves a glyph NAME carried in data (bundle
 * manifests, catalog rows, …) to an icon component. Data declares the name;
 * this is the one place names map to glyphs — never key icons off product ids.
 */
const GLYPHS: Record<string, LucideIcon> = {
  sparkles: Sparkles,
  play: Play,
  receipt: Receipt,
  message: MessageSquare,
  cpu: Cpu,
  globe: Globe,
  eye: Eye,
  package: Package,
  document: FileText,
  image: Image,
  video: Video,
  music: Music,
  wrench: Wrench,
  flask: FlaskConical,
  chart: BarChart3,
  bot: Bot
}

/** The named glyph, or the package default for unknown/empty names. */
export function glyph(name: string | undefined, size = 20): ReactNode {
  const Icon = GLYPHS[(name || '').toLowerCase()] || Package
  return <Icon size={size} />
}
