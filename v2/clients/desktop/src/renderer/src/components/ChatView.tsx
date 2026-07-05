import { FormEvent, useEffect, useRef, useState } from 'react'

import { useApp } from '../state/store'
import MessageItem from './MessageItem'

export default function ChatView() {
  const currentSessionKey = useApp((state) => state.currentSessionKey)
  const session = useApp((state) => state.sessions[state.currentSessionKey])
  const sessionTitle = useApp(
    (state) => state.sessionRows.find((row) => row.sessionId === state.currentSessionKey)?.title
  )
  const currentAgentId = useApp((state) => state.currentAgentId)
  const hello = useApp((state) => state.hello)
  const connection = useApp((state) => state.connection)
  const sendMessage = useApp((state) => state.sendMessage)
  const abortRun = useApp((state) => state.abortRun)

  const [draft, setDraft] = useState('')
  const scrollRef = useRef<HTMLDivElement>(null)
  const items = session?.items || []
  const running = session?.running || false

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight })
  }, [items])

  function submit(event?: FormEvent) {
    event?.preventDefault()
    const text = draft.trim()
    if (!text || running || connection !== 'open') return
    setDraft('')
    void sendMessage(text)
  }

  return (
    <div className="chat">
      <header className="chat-head">
        <div>
          <div className="chat-agent">{sessionTitle || currentAgentId || hello?.agentId || 'agent'}</div>
          <div className="chat-sub mono">
            {currentAgentId || hello?.agentId} · {currentSessionKey}
            {hello ? ` · ${hello.model}` : ''}
          </div>
        </div>
        {running && (
          <button className="button danger" onClick={() => void abortRun()}>
            ■ Stop
          </button>
        )}
      </header>

      <div className="chat-scroll" ref={scrollRef}>
        {items.length === 0 && (
          <div className="empty">
            <div className="empty-title">{hello?.agentName || 'agentd'}</div>
            <div className="empty-sub">
              Ask anything — tools, files, browsing, and your installed agents are all here.
            </div>
          </div>
        )}
        {items.map((item, index) => (
          <MessageItem key={index} item={item} />
        ))}
      </div>

      <form className="composer" onSubmit={submit}>
        <textarea
          value={draft}
          placeholder={connection === 'open' ? 'Message… (Enter to send, Shift+Enter for newline)' : 'connecting…'}
          disabled={connection !== 'open'}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === 'Enter' && !event.shiftKey) {
              event.preventDefault()
              submit()
            }
          }}
          rows={Math.min(8, Math.max(1, draft.split('\n').length))}
        />
        <button className="button primary" type="submit" disabled={!draft.trim() || running || connection !== 'open'}>
          {running ? '…' : 'Send'}
        </button>
      </form>
    </div>
  )
}
