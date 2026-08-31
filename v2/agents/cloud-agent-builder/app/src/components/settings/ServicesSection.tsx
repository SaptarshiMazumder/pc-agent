/* Connected services: the MCP servers this agent DECLARED, and the third-party sign-ins it needs.
 *
 * This section is the answer to "why does this agent have no tools", a question that has no answer
 * anywhere else — the model just says it cannot do the thing. So a server that is not up says WHY:
 * a credential nobody filled in, or a server that started and offered no tools.
 *
 * THE EXACT ARGV IS PRINTED, not a friendly name, because the argv is what actually runs. There
 * used to be an "Approve and run" button beside it, gating every stdio server on the user pressing
 * it; declared servers now start on their agent's first run, so the row only reports.
 */

import { useState } from 'react'
import { type McpServer, type OauthConnection } from '../../agentd/services'

export function ServicesSection({
  servers,
  connections,
  onConnect,
  onDisconnect,
}: {
  servers: McpServer[]
  connections: OauthConnection[]
  onConnect: (name: string) => Promise<void>
  onDisconnect: (name: string) => Promise<void>
}) {
  if (!servers.length && !connections.length) return null

  return (
    <section className="set-group">
      <h2>Connected services</h2>
      <p className="ghelp">
        Services this agent connects to. Credentials come from the fields above and never leave
        this machine.
      </p>

      {connections.map((c) => (
        <OauthRow
          key={c.name}
          connection={c}
          onConnect={onConnect}
          onDisconnect={onDisconnect}
        />
      ))}

      {servers.map((s) => (
        <ServerRow key={s.name} server={s} />
      ))}
    </section>
  )
}

function ServerRow({ server }: { server: McpServer }) {
  return (
    <div className="field">
      <div>
        <label>{server.name}</label>
        {server.tools && server.tools.length > 0 && (
          <span className="fhelp">
            {server.tools.length} tool(s): {server.tools.join(', ')}
          </span>
        )}
        {server.transport === 'stdio' && server.command ? (
          <span className="fhelp mono">{server.command.join(' ')}</span>
        ) : (
          server.url && <span className="fhelp mono">{server.url}</span>
        )}
        {server.problem && <span className="fhelp missing">{server.problem}</span>}
      </div>

      <span className="fhelp">{server.problem ? '' : 'connected'}</span>
    </div>
  )
}

/** One declared OAuth connection: signed in as whom, or a button to sign in. */
function OauthRow({
  connection,
  onConnect,
  onDisconnect,
}: {
  connection: OauthConnection
  onConnect: (name: string) => Promise<void>
  onDisconnect: (name: string) => Promise<void>
}) {
  const [state, setState] = useState<'idle' | 'opening' | 'waiting' | string>('idle')
  const connected = !!connection.connected

  const label =
    state === 'opening'
      ? 'opening…'
      : state === 'waiting'
        ? 'waiting for sign-in…'
        : state === 'idle'
          ? connected
            ? 'Disconnect'
            : 'Connect'
          : state

  return (
    <div className="field">
      <div>
        <label>{connection.name}</label>
        <span className="fhelp">
          {connected
            ? connection.account
              ? `signed in as ${connection.account}`
              : 'signed in'
            : 'not signed in'}
        </span>
        {connection.scopes && connection.scopes.length > 0 && (
          <span className="fhelp">{connection.scopes.join(', ')}</span>
        )}
      </div>
      <button
        className={connected ? 'ghost-btn' : 'prime-btn'}
        disabled={state === 'opening' || state === 'waiting'}
        onClick={async () => {
          if (connected) {
            setState('opening')
            try {
              await onDisconnect(connection.name)
              setState('idle')
            } catch (e) {
              setState(`could not disconnect: ${String((e as Error)?.message || e)}`)
            }
            return
          }
          setState('opening')
          try {
            // Resolves after the browser tab has been opened AND this window regains focus, which
            // is when the row is worth re-reading.
            const done = onConnect(connection.name)
            setState('waiting')
            await done
            setState('idle')
          } catch (e) {
            setState(`could not start: ${String((e as Error)?.message || e)}`)
          }
        }}
      >
        {label}
      </button>
    </div>
  )
}
