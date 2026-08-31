/**
 * Daemon supervisor — the desktop shell's half of "the user never sees Python".
 *
 * Ensure-running mirror of agent_runtime/lifecycle.py: find the live daemon via the
 * rendezvous file, else spawn one DETACHED and wait for the file + an open port.
 * The daemon command resolves (first hit wins):
 *   1. AGENTD_DAEMON_CMD                      (explicit override, also what dev uses)
 *   2. <resources>/python python -m agent_runtime    (packaged: the embedded runtime — a
 *      RELOCATABLE python-build-standalone CPython, NOT a venv (venvs bake in an
 *      absolute base-interpreter path and don't survive being moved to the user's
 *      machine) and NOT a frozen exe, so marketplace pip-plugins can still install)
 *   3. `agentd` on PATH                        (a pipx/uv install on this machine)
 *   4. `python -m agent_runtime`                      (last resort, dev checkouts)
 *
 * The supervisor never stops the daemon on app quit by default: it is a USER-level
 * service (cron jobs, channels keep running) — the shell is just one client of it.
 */

import { app } from 'electron'
import { execFile, spawn } from 'node:child_process'
import { promises as fs } from 'node:fs'
import fsSync from 'node:fs'
import path from 'node:path'

import {
  agentdHome,
  clearGatewayFile,
  findRunning,
  GatewayInfo,
  portOpen,
  readGatewayFile
} from './rendezvous'

export type SupervisorPhase = 'looking' | 'starting' | 'running' | 'failed'

export interface SupervisorStatus {
  phase: SupervisorPhase
  message: string
  info: GatewayInfo | null
}

type StatusListener = (status: SupervisorStatus) => void

const SPAWN_WAIT_MS = 300_000 // cold container builds (imports) can take minutes

const execFileP = (cmd: string, args: string[]): Promise<string> =>
  new Promise((resolve, reject) => {
    execFile(cmd, args, { windowsHide: true }, (err, stdout) =>
      err ? reject(err) : resolve(String(stdout))
    )
  })

/** The ports our spawn could try to bind: explicit override, the stale file's, the default. */
function candidatePorts(before: GatewayInfo | null): number[] {
  const ports = new Set<number>()
  const env = Number(process.env.AGENTD_PORT || '')
  if (Number.isFinite(env) && env > 0) ports.add(env)
  if (before?.port) ports.add(before.port)
  ports.add(8787)
  return [...ports]
}

async function listenerPid(port: number): Promise<number | null> {
  try {
    if (process.platform === 'win32') {
      const out = await execFileP('netstat', ['-ano', '-p', 'tcp'])
      for (const line of out.split(/\r?\n/)) {
        const m = line.match(/^\s*TCP\s+\S+:(\d+)\s+\S+\s+LISTENING\s+(\d+)\s*$/i)
        if (m && Number(m[1]) === port) return Number(m[2])
      }
      return null
    }
    const out = await execFileP('lsof', ['-ti', `tcp:${port}`, '-sTCP:LISTEN'])
    const pid = Number(out.trim().split(/\s+/)[0])
    return Number.isFinite(pid) && pid > 0 ? pid : null
  } catch {
    return null
  }
}

async function processCommandLine(pid: number): Promise<string> {
  try {
    if (process.platform === 'win32') {
      const out = await execFileP('powershell.exe', [
        '-NoProfile',
        '-Command',
        `(Get-CimInstance Win32_Process -Filter "ProcessId=${pid}").CommandLine`
      ])
      return out.trim()
    }
    const out = await execFileP('ps', ['-o', 'command=', '-p', String(pid)])
    return out.trim()
  } catch {
    return ''
  }
}

function commandCandidates(): string[][] {
  const override = (process.env.AGENTD_DAEMON_CMD || '').trim()
  if (override) return [override.split(/\s+/)]
  if (app.isPackaged) {
    const embedded =
      process.platform === 'win32'
        ? path.join(process.resourcesPath, 'python', 'python.exe')
        : path.join(process.resourcesPath, 'python', 'bin', 'python')
    // THE ONLY CANDIDATE when it exists. A shipped product carries the exact runtime it was
    // tested against; falling through to whatever `agentd` or `python` a machine happens to have
    // would run a DIFFERENT version against this user's state, and on a machine with neither it
    // buries the real failure under `No module named agent_runtime` — which is what the daemon
    // log said while the actual error (the port was taken) scrolled past above it.
    if (fsSync.existsSync(embedded)) return [[embedded, '-m', 'agent_runtime']]
  }
  return [
    ['agentd', 'serve'],
    [process.platform === 'win32' ? 'python' : 'python3', '-m', 'agent_runtime']
  ]
}

/**
 * The directory to put on PATH so `node` and `npm` resolve to the SHIPPED ones, or '' when this
 * build carries none.
 *
 * The two official layouts differ and neither is a detail we get to choose: the Windows zip puts
 * `node.exe` at the root of the tree, every other platform puts `bin/node` one level down. Probing
 * for the executable rather than assuming a layout is also what makes this honest about an
 * incomplete bundle — a directory that exists and holds nothing runnable returns '' and the
 * developer's own toolchain is used, instead of a PATH entry that shadows it with nothing.
 */
function bundledNodeBin(root: string): string {
  for (const candidate of [root, path.join(root, 'bin')]) {
    for (const exe of ['node.exe', 'node']) {
      if (fsSync.existsSync(path.join(candidate, exe))) return candidate
    }
  }
  return ''
}

export class Supervisor {
  private listeners: StatusListener[] = []
  private status: SupervisorStatus = { phase: 'looking', message: 'looking for agentd…', info: null }

  // Circuit-breaker. The renderer reconnects on a backoff and calls ensure() forever;
  // without a ceiling a broken build re-spawns doomed processes on every cycle — a
  // storm that can wedge the whole machine. After maxSpawnFailures failed cycles we
  // stop spawning and fail fast. Reset when a live daemon is found or on restart().
  private consecutiveFailures = 0
  private readonly maxSpawnFailures = 3

  /** getFlavorPath: injected by the composition root (index.ts) — the flavored
   *  distribution.toml the spawned daemon must inherit ('' => open build). */
  constructor(private getFlavorPath: () => string = () => '') {}

  onStatus(listener: StatusListener): void {
    this.listeners.push(listener)
    listener(this.status)
  }

  current(): SupervisorStatus {
    return this.status
  }

  private set(phase: SupervisorPhase, message: string, info: GatewayInfo | null = null): void {
    this.status = { phase, message, info }
    for (const listener of this.listeners) listener(this.status)
  }

  /** Find or start the daemon. Resolves with the live GatewayInfo, or throws.
   *  Serialized: app-ready and the renderer both call this at startup, and a second
   *  concurrent spawn would fight the daemon's single-instance guard — so they share
   *  one in-flight attempt. */
  ensure(): Promise<GatewayInfo> {
    if (!this.ensurePromise) {
      this.ensurePromise = this.doEnsure().finally(() => {
        this.ensurePromise = null
      })
    }
    return this.ensurePromise
  }

  private ensurePromise: Promise<GatewayInfo> | null = null

  /** Restart the daemon so restart-gated config changes take effect: stop the running one
   *  (kill by the pid in the rendezvous file), clear the file, then spawn a fresh daemon —
   *  which reloads agentd.config.json from scratch. The renderer's gateway auto-reconnects. */
  async restart(): Promise<GatewayInfo> {
    this.consecutiveFailures = 0 // an explicit restart re-arms the circuit-breaker
    this.set('starting', 'restarting agentd to apply changes…')
    try {
      const info = await readGatewayFile()
      if (info?.pid) {
        try {
          process.kill(info.pid)
        } catch {
          /* already gone */
        }
      }
    } catch {
      /* no rendezvous file — nothing to stop */
    }
    await clearGatewayFile()
    // let the OS release the port before we (or the renderer) re-ensure
    await new Promise((resolve) => setTimeout(resolve, 400))
    this.ensurePromise = null // force a fresh find-or-spawn, not a cached ensure
    return this.ensure()
  }

  private async doEnsure(): Promise<GatewayInfo> {
    this.set('looking', 'looking for a running agentd…')
    const existing = await findRunning()
    if (existing) {
      this.consecutiveFailures = 0 // a live daemon means we've recovered — re-arm the breaker
      this.set('running', `agentd ${existing.version} (pid ${existing.pid})`, existing)
      return existing
    }
    if (this.consecutiveFailures >= this.maxSpawnFailures) {
      // Breaker open: STOP re-spawning. We still re-checked findRunning() above, so a
      // daemon started some other way is picked up — but we will not keep spawning
      // doomed processes on every reconnect. An explicit restart() re-arms us.
      this.set(
        'failed',
        `agentd failed to start ${this.consecutiveFailures} times — not retrying automatically. See the daemon log, then use restart to try again.`
      )
      throw new Error('agentd could not start (circuit-breaker open)')
    }
    await this.evictSquatters()
    return this.spawnDaemon()
  }

  /** ORPHAN EVICTION — the port is taken, but nothing answering describes itself in the
   *  rendezvous file. That is, by construction, an agentd whose file was overwritten or deleted
   *  (today's dev daemons, a kill that outraced a respawn): nothing can ever adopt it again, so
   *  every spawn dies on the bind and the breaker opens with a message that names neither cause
   *  nor cure. Killing it is the RECOVERY, not a risk — but only after reading the process's
   *  command line and proving it is ours. A port held by anything else is REPORTED, named and
   *  left alone: killing an arbitrary pid by port number is how somebody's dev server dies.
   *
   *  findRunning() ran just before this, so anything adoptable was adopted — whatever holds the
   *  port now is unreachable by definition. */
  private async evictSquatters(): Promise<void> {
    const before = await readGatewayFile()
    for (const port of candidatePorts(before)) {
      if (!(await portOpen('127.0.0.1', port))) continue
      const pid = await listenerPid(port)
      if (!pid || pid === process.pid) continue
      const cmd = await processCommandLine(pid)
      if (!/agent_runtime|agentd/i.test(cmd)) {
        // Not ours. Say exactly who it is — the honest failure the old message buried.
        this.set(
          'starting',
          `port ${port} is held by pid ${pid} (${cmd.slice(0, 100) || 'unknown process'}) — not an agentd, leaving it alone`
        )
        continue
      }
      this.set('starting', `evicting an orphaned agentd (pid ${pid}) from port ${port}…`)
      try {
        process.kill(pid)
      } catch {
        /* already gone */
      }
      const deadline = Date.now() + 4000
      while (Date.now() < deadline && (await portOpen('127.0.0.1', port))) {
        await new Promise((resolve) => setTimeout(resolve, 200))
      }
    }
  }

  private async spawnDaemon(): Promise<GatewayInfo> {
    const logDir = path.join(agentdHome(), 'logs')
    await fs.mkdir(logDir, { recursive: true })
    const logPath = path.join(logDir, 'daemon.log')
    const logFile = fsSync.openSync(logPath, 'a')
    // REMEMBER the rendezvous, do not DELETE it. We still need to tell our daemon's file from a
    // pre-existing one (the `agentd` console script forks a child python, so the spawned pid does
    // not match), but deleting is the wrong way to get that: the file belongs to whatever daemon
    // is alive, and one engine serves every agent app on the machine.
    //
    // What deleting it did, once: a product app launched while another agentd held the port,
    // removed that daemon's file, and made it PERMANENTLY undiscoverable — the token lives in the
    // file, so nothing can attach to it again. Every later launch then found no daemon, spawned
    // one, failed to bind, and fell through to a python with no agent_runtime in it. A loop with
    // no exit, caused entirely by the recovery step.
    const before = await readGatewayFile()

    const env = { ...process.env }
    // A flavored build carries its distribution.toml; the daemon it spawns must be
    // the same product (provisioning, default agent, store wiring) — pass it down.
    const flavorPath = this.getFlavorPath()
    if (flavorPath && !env.AGENTD_DISTRIBUTION) env.AGENTD_DISTRIBUTION = flavorPath

    // THE BUNDLED BROWSER. The installer ships chromium next to the runtime (see
    // build-runtime.ps1 + extraResources), but playwright only looks in the USER'S cache unless
    // told otherwise — so without this line the browser is packaged, present, and never found:
    // every page-opening tool fails on a fresh machine exactly as if we had shipped nothing.
    //
    // Only when it is really there. A dev checkout has no bundle, and pointing playwright at an
    // empty directory would turn "install chromium" into "the download is corrupt".
    if (app.isPackaged) {
      const bundled = path.join(process.resourcesPath, 'ms-playwright')
      if (!env.PLAYWRIGHT_BROWSERS_PATH && fsSync.existsSync(bundled)) {
        env.PLAYWRIGHT_BROWSERS_PATH = bundled
      }
    }

    // THE BUNDLED NODE, for the same reason and by the same route.
    //
    // An agent's window is a React project: source in `app/`, built output in `ui/`, and the
    // daemon serves only the second. Turning one into the other is `npm run build` — so a user
    // who installed this product and built an agent through Agent Builder needs a toolchain they
    // never agreed to install, to change a window they own. Shipping it removes the requirement
    // rather than documenting it.
    //
    // ON THE PATH, not only in a variable. Everything that builds an agent shells out (`npm`,
    // `npx vite`), and a path in an env var that each of those has to remember to consult is a
    // path one of them will not consult. `AGENTD_NODE_DIR` is set as well, so a tool that wants
    // to name the executable exactly can, without parsing PATH back apart.
    //
    // PREPENDED, so the shipped Node wins over whatever happens to be installed. A user's own
    // Node may be years old, and an agent that builds here and not on their machine is the exact
    // class of failure this bundle exists to end. Only when it is really there — a dev checkout
    // has none, and the developer's own toolchain is the right answer in that case.
    if (app.isPackaged) {
      const bin = bundledNodeBin(path.join(process.resourcesPath, 'node'))
      if (bin) {
        env.AGENTD_NODE_DIR = bin
        // WRITE BACK TO THE KEY THAT IS ALREADY THERE. Windows names this variable `Path`, and
        // `process.env` is only case-insensitive on ITSELF — spreading it into a plain object
        // above keeps the original casing, so `env.PATH` is undefined here and `env.PATH = …`
        // ADDS A SECOND VARIABLE holding nothing but this one directory. The child gets both,
        // picks that one, and every daemon we spawn runs without System32: no `where`, no
        // `findstr`, no PowerShell, no user-installed CLI. Agent Builder rediscovered that
        // broken shell by trial and error at the start of every session.
        const pathKey = Object.keys(env).find((k) => k.toLowerCase() === 'path') ?? 'PATH'
        env[pathKey] = `${bin}${path.delimiter}${env[pathKey] ?? ''}`
      }
      // THE SHARED DEPENDENCY STORE. Every agent app declares the same seven packages, because
      // they all come from the same starter — so the product carries ONE copy and each agent's
      // build is pointed at it. Installing per agent would mean a few hundred MB from the network
      // every time a user creates one, and nothing at all on a machine that is offline.
      const deps = path.join(process.resourcesPath, 'app-deps', 'node_modules')
      if (fsSync.existsSync(deps)) env.AGENTD_APP_DEPS = deps
    }

    let lastError = ''
    for (const command of commandCandidates()) {
      this.set('starting', `starting agentd (${command[0]})… first start can take a minute`)
      try {
        const child = spawn(command[0], command.slice(1), {
          detached: true,
          stdio: ['ignore', logFile, logFile],
          windowsHide: true,
          env
        })
        child.unref()
        // The daemon is detached and runs forever on success, so an 'exit' before the
        // rendezvous appears means it FAILED (e.g. couldn't bind) — surface it fast
        // with the log tail instead of waiting out the full timeout.
        const spawnFailed = new Promise<never>((_, reject) => {
          child.once('error', (e) => reject(new Error(`${command[0]}: ${e.message}`)))
          child.once('exit', (code) => {
            if (code !== null && code !== 0) {
              reject(new Error(`${command[0]} exited (code ${code}) — see ${logPath}`))
            }
          })
        })
        const info = await Promise.race([this.waitForDaemon(before), spawnFailed])
        this.consecutiveFailures = 0 // it came up — re-arm the breaker
        this.set('running', `agentd ${info.version} (pid ${info.pid})`, info)
        return info
      } catch (error) {
        lastError = error instanceof Error ? error.message : String(error)
      }
    }
    this.consecutiveFailures++ // trips the breaker once it reaches maxSpawnFailures
    this.set('failed', `could not start agentd: ${lastError} — see ${logPath}`)
    throw new Error(lastError || 'could not start agentd')
  }

  /** Wait for a rendezvous that is NOT the one we saw before spawning, with an open port.
   *
   *  Identity is (pid, startedAt) rather than the spawned pid, because the `agentd` console
   *  script forks a child python and the pid we hold is the wrapper's. Comparing against the
   *  PREVIOUS file gives the same certainty deleting it used to, without destroying state that
   *  belongs to a daemon which may still be alive and serving other apps.
   *
   *  A pre-existing file that is merely STALE costs nothing here: its port is closed, so it never
   *  satisfies the wait, and our daemon's file replaces it on startup. */
  private async waitForDaemon(before: GatewayInfo | null): Promise<GatewayInfo> {
    const isOurs = (info: GatewayInfo): boolean =>
      before === null || info.pid !== before.pid || info.startedAt !== before.startedAt
    const deadline = Date.now() + SPAWN_WAIT_MS
    while (Date.now() < deadline) {
      const info = await readGatewayFile()
      if (info && isOurs(info) && (await portOpen(info.host, info.port))) {
        return info
      }
      await new Promise((resolve) => setTimeout(resolve, 400))
    }
    throw new Error(`daemon did not come up within ${SPAWN_WAIT_MS / 1000}s`)
  }
}
