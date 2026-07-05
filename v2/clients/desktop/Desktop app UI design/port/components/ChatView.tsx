import { FormEvent, useEffect, useRef, useState } from 'react'
import { Paperclip, ArrowUp, Square, Loader2, Terminal, Check, MessageSquare } from 'lucide-react'

import logo from '../assets/nakama.svg'
import { agentInitials, agentTag } from '../lib/agentPresentation'
import { dayLabel, sameDay } from '../lib/timefmt'
import { useApp } from '../state/store'
import MessageItem from './MessageItem'
import TabBar from './TabBar'

const SUGGESTIONS = [
  { icon: <Terminal size={15} />, text: 'Summarize today’s changes', fill: 'Summarize today’s changes in the repo.' },
  { icon: <Check size={15} />, text: 'Run the test suite', fill: 'Run the full test suite and report failures.' },
  { icon: <MessageSquare size={15} />, text: 'Draft a release note', fill: 'Draft a short release note for the latest changes.' }
]

export default function ChatView() {
  const currentSessionKey = useApp((s) => s.currentSessionKey)
  const session = useApp((s) => s.sessions[s.currentSessionKey])
  const sessionTitle = useApp((s) => s.sessionRows.find((r) => r.sessionId === s.currentSessionKey)?.title)
  const currentAgentId = useApp((s) => s.currentAgentId)
  const agents = useApp((s) => s.agents)
  const projectName = useApp((s) => s.projects.find((p) => p.id === s.currentProjectId)?.name)
  const hello = useApp((s) => s.hello)
  const connection = useApp((s) => s.connection)
  const sendMessage = useApp((s) => s.sendMessage)
  const abortRun = useApp((s) => s.abortRun)

  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const items = session?.items || []
  const running = session?.running || false
  const empty = items.length === 0

  const agentName = agents.find((a) => a.id === currentAgentId)?.name || hello?.agentName || currentAgentId || 'agent'
  const model = hello?.model || ''

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight
  }, [items])

  function submit(e?: FormEvent) {
    e?.preventDefault()
    const text = draft.trim()
    if (!text || running || connection !== 'open') return
    setDraft('')
    void sendMessage(text)
  }

  // date separators between calendar days
  const rendered: JSX.Element[] = []
  let lastTs: number | undefined
  items.forEach((item, i) => {
    if (item.ts && (!lastTs || !sameDay(item.ts, lastTs))) {
      rendered.push(<div key={`day-${i}`} className="msg-system">{dayLabel(item.ts)}</div>)
    }
    if (item.ts) lastTs = item.ts
    rendered.push(<MessageItem key={i} item={item} />)
  })

  return (
    <div className={`chat ${empty ? 'empty' : ''}`}>
      <TabBar />

      <header className="chat-head">
        <span className="avatar" style={{ width: 32, height: 32, fontSize: 12, background: 'var(--accent)' }}>
          {agentInitials(agentName, currentAgentId)}
        </span>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="chat-title">
            {sessionTitle || agentName}
            {projectName && <span style={{ color: 'var(--accent-text)', fontWeight: 500 }}> · {projectName}</span>}
          </div>
          <div className="chat-meta">{currentAgentId || hello?.agentId}{model ? ` · ${model}` : ''}</div>
        </div>
        {running && <span className="working"><Loader2 size={16} />working…</span>}
        {running && <button className="stop-btn" onClick={() => void abortRun()}><Square size={14} />Stop</button>}
      </header>

      <div className="chat-scroll" ref={scrollRef}>
        <div className="chat-col">
          {empty && (
            <div className="empty-state">
              <img className="empty-logo" src={logo} alt="" />
              <div className="empty-title">{agentName}</div>
              <div className="empty-sub">
                {projectName
                  ? `New chat in ${projectName} — ask anything.`
                  : currentAgentId === 'main' || !currentAgentId
                    ? 'Ask anything — tools, files, browsing and your installed agents are all here.'
                    : `You’re talking to ${agentName}. ${agentTag(currentAgentId)}.`}
              </div>
              <div className="suggestions">
                {SUGGESTIONS.map((g) => (
                  <button key={g.text} className="suggestion" onClick={() => setDraft(g.fill)}>{g.icon}{g.text}</button>
                ))}
              </div>
            </div>
          )}
          {rendered}
        </div>
      </div>

      <form className="composer" onSubmit={submit}>
        <div className="composer-box">
          <button type="button" className="composer-attach" title="attach"><Paperclip size={18} /></button>
          <textarea
            value={draft}
            placeholder={connection === 'open' ? 'Message the agent…  (Enter to send, Shift+Enter for newline)' : 'connecting…'}
            disabled={connection !== 'open'}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } }}
            rows={Math.min(8, Math.max(1, draft.split('\n').length))}
          />
          <button type="submit" className={`composer-send ${draft.trim() && !running ? 'ready' : ''}`} disabled={!draft.trim() || running || connection !== 'open'} title="send">
            <ArrowUp size={18} />
          </button>
        </div>
        <div className="composer-hint">{running ? 'agent is running — press Stop to interrupt' : `agentd runs locally${model ? ' · ' + model : ''}`}</div>
      </form>
    </div>
  )
}
