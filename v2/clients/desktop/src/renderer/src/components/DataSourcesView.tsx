import { useCallback, useEffect, useState } from 'react'
import { Plug, Plus, Trash2, Loader2, RefreshCw } from 'lucide-react'

import { gateway } from '../gateway/client'
import { useApp } from '../state/store'
import PageShell from './PageShell'

interface McpServer {
  name: string
  transport: string
  command?: string[] | null
  url?: string | null
  connected: boolean
}

/** Data sources — connect external tools/data via MCP servers (live, no restart). Real:
 *  reads mcp.list, adds via mcp.add, removes via mcp.remove. */
export default function DataSourcesView() {
  const connection = useApp((s) => s.connection)
  const [servers, setServers] = useState<McpServer[] | null>(null)
  const [err, setErr] = useState('')
  const [adding, setAdding] = useState(false)
  const [busy, setBusy] = useState(false)
  const [note, setNote] = useState('')
  const [form, setForm] = useState({ name: '', kind: 'stdio' as 'stdio' | 'http', command: '', url: '' })

  const load = useCallback(async () => {
    try {
      const r = await gateway.request<{ servers: McpServer[] }>('mcp.list')
      setServers(r.servers || [])
      setErr('')
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e))
    }
  }, [])

  useEffect(() => {
    if (connection === 'open') void load()
  }, [connection, load])

  const canAdd = form.name.trim() && (form.kind === 'http' ? form.url.trim() : form.command.trim())

  const add = async (): Promise<void> => {
    if (!canAdd || busy) return
    setBusy(true)
    setNote('')
    try {
      const params: Record<string, unknown> = { name: form.name.trim() }
      if (form.kind === 'http') params.url = form.url.trim()
      else params.command = form.command.trim().split(/\s+/).filter(Boolean)
      const r = await gateway.request<{ added: boolean; error?: string }>('mcp.add', params)
      if (!r.added) throw new Error(r.error || 'could not connect')
      setAdding(false)
      setForm({ name: '', kind: 'stdio', command: '', url: '' })
      await load()
      setNote(`Connected “${params.name}”.`)
    } catch (e) {
      setNote(`Couldn’t add: ${e instanceof Error ? e.message : String(e)}`)
    } finally {
      setBusy(false)
    }
  }

  const remove = async (name: string): Promise<void> => {
    try {
      await gateway.request('mcp.remove', { name })
      await load()
    } catch (e) {
      setNote(`Couldn’t remove: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const actions = (
    <>
      <button className="btn ghost" onClick={() => void load()}>
        <RefreshCw size={14} />Refresh
      </button>
      <button className="btn primary" onClick={() => setAdding((a) => !a)}>
        <Plus size={14} />Add source
      </button>
    </>
  )

  return (
    <PageShell
      title="Data sources"
      sub="Connect external tools & data via MCP servers. They load live — no restart."
      actions={actions}
    >
      {note && <div className={`ds-note ${note.startsWith('Couldn') ? 'err' : ''}`}>{note}</div>}

        {adding && (
          <div className="settings-card ds-form">
            <div className="field">
              <span className="field-label">Name</span>
              <input
                className="field-input"
                placeholder="e.g. gmail"
                value={form.name}
                spellCheck={false}
                onChange={(e) => setForm((f) => ({ ...f, name: e.target.value }))}
              />
            </div>
            <div className="field">
              <span className="field-label">Type</span>
              <div className="seg seg--start">
                <button className={form.kind === 'stdio' ? 'on' : ''} onClick={() => setForm((f) => ({ ...f, kind: 'stdio' }))}>
                  Command (stdio)
                </button>
                <button className={form.kind === 'http' ? 'on' : ''} onClick={() => setForm((f) => ({ ...f, kind: 'http' }))}>
                  URL (http)
                </button>
              </div>
            </div>
            {form.kind === 'stdio' ? (
              <div className="field">
                <span className="field-label">Command</span>
                <input
                  className="field-input"
                  placeholder="uvx some-mcp-server --flag"
                  value={form.command}
                  spellCheck={false}
                  onChange={(e) => setForm((f) => ({ ...f, command: e.target.value }))}
                />
              </div>
            ) : (
              <div className="field">
                <span className="field-label">URL</span>
                <input
                  className="field-input"
                  placeholder="https://server.example.com/mcp"
                  value={form.url}
                  spellCheck={false}
                  onChange={(e) => setForm((f) => ({ ...f, url: e.target.value }))}
                />
              </div>
            )}
            <div className="ds-form-actions">
              <button className="btn ghost" onClick={() => setAdding(false)} disabled={busy}>Cancel</button>
              <button className="btn primary" onClick={() => void add()} disabled={!canAdd || busy}>
                {busy ? <Loader2 size={14} className="spin" /> : <Plus size={14} />}
                {busy ? 'Connecting…' : 'Connect'}
              </button>
            </div>
          </div>
        )}

        <div className="settings-group">
          <div className="settings-section">Connected sources</div>
          {err && <div className="settings-empty danger-text">Couldn’t load: {err}</div>}
          {!servers && !err && <div className="settings-empty">Loading…</div>}
          {servers && servers.length === 0 && (
            <div className="settings-card">
              <div className="settings-empty">
                No data sources yet. Add an MCP server to connect tools like Gmail, Drive, databases and more.
              </div>
            </div>
          )}
          {servers &&
            servers.map((s) => (
              <div className="ds-row" key={s.name}>
                <div className="ds-icon"><Plug size={18} /></div>
                <div className="ds-main">
                  <div className="ds-name">
                    {s.name}
                    <span className={`ds-dot ${s.connected ? 'on' : ''}`} title={s.connected ? 'connected' : 'not connected'} />
                  </div>
                  <div className="ds-sub">{s.transport === 'http' ? s.url : (s.command || []).join(' ')}</div>
                </div>
                <button className="hover-btn danger" title="remove" onClick={() => void remove(s.name)}>
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
        </div>
    </PageShell>
  )
}
