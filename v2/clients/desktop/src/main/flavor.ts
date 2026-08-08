/**
 * Flavor — what THIS RUN of the desktop app is (M6 build flavors, not forks).
 *
 * The same distribution.toml the daemon reads decides the shell's branding too:
 * product name, default agent, whether the store tab shows, and which bundles to
 * preinstall on first run.
 *
 * ONE ENGINE, MANY PRODUCTS
 * -------------------------
 * A product is a PAYLOAD directory, not a build:
 *
 *     distribution.toml      what product this is ([product] app_agent -> the agent's own UI)
 *     bundles/*.agentpkg     the agent itself, installed on first run
 *     icon.ico               its icon (optional)
 *
 * Point the shell at one with `--app-dir <path>` (or AGENTD_APP_DIR) and it BECOMES that
 * product. Nothing about the executable changes, so a single installed engine serves every
 * agent on the machine: one Electron, one CPython, one daemon, N products. Before this, each
 * product baked its own distribution.toml into <resources> — a 253 MB installer to deliver
 * ~1 MB of difference, duplicated per agent.
 *
 * That split is also what makes code signing affordable later. The payload lives OUTSIDE the
 * signed executable, so publishing an agent never re-signs a binary and never mints a new one:
 * every product shares the engine's single publisher identity, and every download of any agent
 * builds the same SmartScreen reputation. Signing stays a build-config concern (electron-builder
 * `win`), never a code concern — there is deliberately nothing to change here when it lands.
 *
 * Resolution order, first readable file wins:
 *   payload   -> <--app-dir|AGENTD_APP_DIR>/distribution.toml   (any build, incl. packaged)
 *   packaged  -> <resources>/distribution.toml                  (a baked single-product build)
 *   dev       -> flavors/<AGENTD_FLAVOR || core>/distribution.toml
 *             -> dist/app-flavors/<AGENTD_FLAVOR>/distribution.toml  (gen-app-flavor output)
 * No file => the open "agentd" flavor.
 *
 * A payload dir is EXACTLY the shape gen-app-flavor.mjs already emits, so its output doubles
 * as a live payload with no extra tooling: `--app-dir dist/app-flavors/<id>`.
 */

import TOML from '@iarna/toml'
import { app } from 'electron'
import { existsSync, promises as fs } from 'node:fs'
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
  /** the hosted LiteLLM Model Proxy; the daemon reads the same file itself — the shell only
   *  needs this for display/diagnostics. */
  modelProxyUrl: string
  /** absolute path of the loaded file; '' => open default (nothing to pass on) */
  sourcePath: string
  /** absolute paths of .agentpkg files shipped with this build (resources/bundles) */
  bundledPackages: string[]
  /** absolute path of the PAYLOAD dir this run was pointed at; '' => none (the engine's own
   *  baked resources, i.e. a single-product installer). Reported so diagnostics can say which
   *  product is live on a machine where one engine serves several. */
  payloadDir: string
  /** the product's own icon when the payload ships one; '' => the engine's default icon. */
  iconPath: string
  /** Windows AppUserModelID. With one shared executable the OS cannot tell products apart by
   *  path, so every product's windows would collapse into ONE taskbar button and pinned
   *  shortcuts would launch the wrong thing. This must match the AUMID the launching shortcut
   *  sets. `[product] app_id` wins; else derived from the product id (the same
   *  `dev.agentd.app.<id>` shape gen-app-flavor gives electron-builder). */
  appUserModelId: string
}

const OPEN: Flavor = {
  productId: 'agentd',
  productName: 'agentd',
  defaultAgent: '',
  appAgent: '',
  storeEnabled: true,
  preinstalledBundles: [],
  accountsUrl: '',
  modelProxyUrl: '',
  sourcePath: '',
  bundledPackages: [],
  payloadDir: '',
  iconPath: '',
  appUserModelId: ''
}

const APP_DIR_FLAG = '--app-dir'

/** `--app-dir <path>` or `--app-dir=<path>`, scanned across the whole argv because its
 *  position differs between a packaged run ([exe, ...args]) and electron-vite dev
 *  ([electron, script, ...args]). '' when absent. */
function argValue(flag: string): string {
  const argv = process.argv
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i]
    if (a === flag) return (argv[i + 1] || '').trim()
    if (a.startsWith(`${flag}=`)) return a.slice(flag.length + 1).trim()
  }
  return ''
}

/** The payload directory for this run, or '' for none.
 *
 *  A directory without a distribution.toml is NOT a payload: we warn and fall through rather
 *  than boot a half-configured product. Falling back means the engine's own flavor — for a
 *  shortcut written by the installer that can only happen if the payload was deleted, and a
 *  loud line in the log is what makes that diagnosable. */
export function payloadDir(): string {
  const raw = argValue(APP_DIR_FLAG) || (process.env.AGENTD_APP_DIR || '').trim()
  if (!raw) return ''
  const dir = path.resolve(raw)
  if (existsSync(path.join(dir, 'distribution.toml'))) return dir
  console.warn(`flavor: --app-dir "${dir}" has no distribution.toml — ignoring it`)
  return ''
}

function devFlavorName(): string {
  return (process.env.AGENTD_FLAVOR || 'core').trim()
}

function candidatePaths(payload: string): string[] {
  const out: string[] = []
  if (payload) out.push(path.join(payload, 'distribution.toml'))
  if (app.isPackaged) {
    out.push(path.join(process.resourcesPath, 'distribution.toml'))
  } else {
    const flavorName = devFlavorName()
    // authored flavors (core, studio, …)
    out.push(path.join(app.getAppPath(), 'flavors', flavorName, 'distribution.toml'))
    // GENERATED per-agent product flavors (gen-app-flavor.mjs) — build output, not authored
    out.push(path.join(app.getAppPath(), 'dist', 'app-flavors', flavorName, 'distribution.toml'))
  }
  return out
}

async function packagesIn(dir: string): Promise<string[]> {
  try {
    const entries = await fs.readdir(dir)
    return entries.filter((f) => f.endsWith('.agentpkg')).map((f) => path.join(dir, f))
  } catch {
    return []
  }
}

/** The .agentpkg files this product ships. A payload's bundles/ is EXCLUSIVE — it must not
 *  inherit the engine's, or an engine built from the old fat-installer path would preinstall
 *  its own agent alongside every product it hosts. */
async function bundledPackages(payload: string): Promise<string[]> {
  if (payload) return packagesIn(path.join(payload, 'bundles'))
  if (app.isPackaged) return packagesIn(path.join(process.resourcesPath, 'bundles'))
  return packagesIn(path.join(app.getAppPath(), 'flavors', devFlavorName(), 'bundles'))
}

/** The product's icon: the payload's own, else '' (the caller uses the engine default). */
function iconIn(payload: string, declared: string): string {
  if (!payload) return ''
  for (const rel of [declared, 'icon.ico', 'icon.png']) {
    if (!rel) continue
    const p = path.join(payload, rel)
    if (existsSync(p)) return p
  }
  return ''
}

export async function loadFlavor(): Promise<Flavor> {
  const payload = payloadDir()
  const packages = await bundledPackages(payload)
  for (const candidate of candidatePaths(payload)) {
    try {
      const parsed = TOML.parse(await fs.readFile(candidate, 'utf-8')) as Record<string, any>
      const product = (parsed.product as Record<string, any>) || {}
      const store = (parsed.store as Record<string, any>) || {}
      const platform = (parsed.platform as Record<string, any>) || {}
      const productId = String(product.id || 'agentd')
      return {
        productId,
        productName: String(product.name || 'agentd'),
        defaultAgent: String(product.default_agent || ''),
        appAgent: String(product.app_agent || ''),
        storeEnabled: store.enabled !== false,
        preinstalledBundles: ((product.preinstalled_bundles as string[]) || []).map(String),
        accountsUrl: String(platform.accounts_url || '').replace(/\/$/, ''),
        modelProxyUrl: String(
          platform.model_proxy_url || platform.model_gateway_url || ''
        ).replace(/\/$/, ''),
        sourcePath: candidate,
        bundledPackages: packages,
        payloadDir: payload,
        iconPath: iconIn(payload, String(product.icon || '')),
        appUserModelId: String(product.app_id || `dev.agentd.app.${productId}`)
      }
    } catch {
      /* fall through to the next candidate / open default */
    }
  }
  return { ...OPEN, bundledPackages: packages, payloadDir: payload }
}
