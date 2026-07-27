/**
 * Flavor — what THIS BUILD of the desktop app is (M6 build flavors, not forks).
 *
 * The same distribution.toml the daemon reads decides the shell's branding too:
 * product name, default agent, whether the store tab shows, and which bundles to
 * preinstall on first run. Resolution:
 *   packaged  -> <resources>/distribution.toml (placed by electron-builder per flavor)
 *   dev       -> flavors/<AGENTD_FLAVOR || core>/distribution.toml
 * No file => the open "agentd" flavor.
 */

import TOML from '@iarna/toml'
import { app } from 'electron'
import { promises as fs } from 'node:fs'
import path from 'node:path'

export interface Flavor {
  productId: string
  productName: string
  defaultAgent: string
  /** AGENT-APP shell mode: the id of the agent this build IS. Set => the shell never
   *  loads the JARVIS renderer — it boots the daemon and opens the agent's own UI
   *  (/apps/<id>/) as the product window. '' => the normal desktop client. */
  appAgent: string
  storeEnabled: boolean
  preinstalledBundles: string[]
  /** HOSTED PLATFORM ([platform] in distribution.toml): where sign-in lives. '' => BYOK-only
   *  install, no sign-in gate — exactly the pre-platform behavior. */
  accountsUrl: string
  /** the hosted LiteLLM model gateway; the daemon reads the same file itself — the shell only
   *  needs this for display/diagnostics. */
  modelGatewayUrl: string
  /** absolute path of the loaded file; '' => open default (nothing to pass on) */
  sourcePath: string
  /** absolute paths of .agentpkg files shipped with this build (resources/bundles) */
  bundledPackages: string[]
}

const OPEN: Flavor = {
  productId: 'agentd',
  productName: 'agentd',
  defaultAgent: '',
  appAgent: '',
  storeEnabled: true,
  preinstalledBundles: [],
  accountsUrl: '',
  modelGatewayUrl: '',
  sourcePath: '',
  bundledPackages: []
}

function candidatePaths(): string[] {
  if (app.isPackaged) return [path.join(process.resourcesPath, 'distribution.toml')]
  const flavorName = (process.env.AGENTD_FLAVOR || 'core').trim()
  return [
    // authored flavors (core, studio, …)
    path.join(app.getAppPath(), 'flavors', flavorName, 'distribution.toml'),
    // GENERATED per-agent product flavors (gen-app-flavor.mjs) — build output, not authored
    path.join(app.getAppPath(), 'dist', 'app-flavors', flavorName, 'distribution.toml')
  ]
}

async function bundledPackages(): Promise<string[]> {
  const dir = app.isPackaged
    ? path.join(process.resourcesPath, 'bundles')
    : path.join(app.getAppPath(), 'flavors', (process.env.AGENTD_FLAVOR || 'core').trim(), 'bundles')
  try {
    const entries = await fs.readdir(dir)
    return entries.filter((f) => f.endsWith('.agentpkg')).map((f) => path.join(dir, f))
  } catch {
    return []
  }
}

export async function loadFlavor(): Promise<Flavor> {
  const packages = await bundledPackages()
  for (const candidate of candidatePaths()) {
    try {
      const parsed = TOML.parse(await fs.readFile(candidate, 'utf-8')) as Record<string, any>
      const product = (parsed.product as Record<string, any>) || {}
      const store = (parsed.store as Record<string, any>) || {}
      const platform = (parsed.platform as Record<string, any>) || {}
      return {
        productId: String(product.id || 'agentd'),
        productName: String(product.name || 'agentd'),
        defaultAgent: String(product.default_agent || ''),
        appAgent: String(product.app_agent || ''),
        storeEnabled: store.enabled !== false,
        preinstalledBundles: ((product.preinstalled_bundles as string[]) || []).map(String),
        accountsUrl: String(platform.accounts_url || '').replace(/\/$/, ''),
        modelGatewayUrl: String(platform.model_gateway_url || '').replace(/\/$/, ''),
        sourcePath: candidate,
        bundledPackages: packages
      }
    } catch {
      /* fall through to the next candidate / open default */
    }
  }
  return { ...OPEN, bundledPackages: packages }
}
